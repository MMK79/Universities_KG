"""Phase 2: polite download of public slides / lecture notes (PDF, PPTX).

Rules (see docs/spec.md section 5): robots.txt checked with our UA before every file,
at most one request per second per host, one worker per host, no logins (LMS hosts and
HTML "login" responses are skipped), 50 MB per file, 6 GB in total, resumable: a URL
with a final result in `material_files` is never fetched again.

Layout on disk: <root>/<country>/<university>/<course-code>/<filename>.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
import unicodedata
import urllib.robotparser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from .config import USER_AGENT

MATERIALS_ROOT = Path("/Volumes/MigMig/Personal/Research/ML Course Materials")
WANTED_TYPES = ("slides", "lecture_notes")
WANTED_EXT = (".pdf", ".pptx")
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 6 * 1024**3
HOST_DELAY = 1.0  # seconds between requests to one host
# Hosts that sit behind a login (learning-management systems, forums).
LOGIN_LABELS = {"canvas", "moodle", "blackboard", "piazza", "brightspace", "gradescope", "edstem"}
LOGIN_SUFFIXES = ("instructure.com", "ed.stanford.edu")


def behind_login(host: str | None) -> bool:
    host = (host or "").lower()
    return bool(LOGIN_LABELS & set(host.split("."))) or host.endswith(LOGIN_SUFFIXES)


OK_CTYPES = ("pdf", "powerpoint", "presentationml", "octet-stream")

# final statuses: never retried. http-error is retried on the next run.
FINAL = ("ok", "skipped-robots", "too-large", "not-document", "skipped-login")


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def slug(s: str, maxlen: int = 80) -> str:
    """Filesystem-safe component: ASCII, no path separators, no leading dots."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._-")
    return (s or "x")[:maxlen]


def url_ext(url: str) -> str:
    return Path(unquote(urlsplit(url).path)).suffix.lower()


def eligible(mtype: str, url: str) -> bool:
    """Only public slides / lecture notes that are linked as PDF or PPTX files."""
    parts = urlsplit(url)
    return (
        mtype in WANTED_TYPES
        and parts.scheme in ("http", "https")
        and url_ext(url) in WANTED_EXT
        and not behind_login(parts.hostname)
    )


def filename_for(url: str, taken: set[str] | None = None) -> str:
    """Sanitised basename from the URL path; a short URL hash is added on a collision
    (or when the name is empty), so two different URLs never share one file."""
    base = Path(unquote(urlsplit(url).path)).name
    stem, ext = Path(base).stem, Path(base).suffix.lower()
    name = f"{slug(stem, 100)}{ext if ext in WANTED_EXT else ''}"
    if not stem or (taken is not None and name in taken):
        h = hashlib.sha1(url.encode()).hexdigest()[:8]
        name = f"{slug(stem, 90) if stem else 'file'}-{h}{ext if ext in WANTED_EXT else ''}"
    return name


def target_path(
    root: Path, country: str, university: str, course: str, url: str, taken: set[str] | None = None
) -> Path:
    p = root / slug(country) / slug(university) / slug(course, 60) / filename_for(url, taken)
    # defence in depth: the resolved path must stay below root
    if root.resolve() not in p.resolve().parents:
        raise ValueError(f"path escapes root: {p}")
    return p


@dataclass
class Result:
    url: str
    status: str
    local_path: str | None = None  # relative to the materials root
    bytes: int | None = None
    sha256: str | None = None
    content_type: str | None = None
    http_status: int | None = None
    detail: str = ""


class Budget:
    """Thread-safe running total against the global byte cap."""

    def __init__(self, used: int, cap: int = MAX_TOTAL_BYTES):
        self.used, self.cap, self._lock = used, cap, threading.Lock()

    def reserve(self, n: int) -> bool:
        with self._lock:
            if self.used + n > self.cap:
                return False
            self.used += n
            return True

    def release(self, n: int) -> None:
        with self._lock:
            self.used -= n

    @property
    def exhausted(self) -> bool:
        return self.used >= self.cap


class Downloader:
    def __init__(
        self,
        root: Path,
        budget: Budget,
        client: httpx.Client | None = None,
        delay: float = HOST_DELAY,
        max_file: int = MAX_FILE_BYTES,
    ):
        self.root, self.budget, self.delay, self.max_file = root, budget, delay, max_file
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = defaultdict(float)
        self._lock = threading.Lock()

    def _wait(self, host: str) -> None:
        with self._lock:
            gap = self.delay - (time.monotonic() - self._last[host])
            self._last[host] = time.monotonic() + max(gap, 0)
        if gap > 0:
            time.sleep(gap)

    def robots_ok(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            known = base in self._robots
        if not known:
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            self._wait(parts.netloc)
            try:
                r = self.client.get(base + "/robots.txt")
                if r.status_code >= 400:
                    rp = None  # no robots.txt -> allowed
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            with self._lock:
                self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    def fetch(self, url: str, dest: Path) -> Result:
        """Download one file to `dest` (relative path stored against self.root)."""
        host = urlsplit(url).netloc
        if not self.robots_ok(url):
            return Result(url, "skipped-robots")
        self._wait(host)
        reserved = 0
        try:
            with self.client.stream("GET", url) as r:
                final = str(r.url)
                if final != url and not self.robots_ok(final):
                    return Result(url, "skipped-robots", detail=f"redirect to {final}")
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if r.status_code == 401:  # authentication required: never go further
                    return Result(url, "skipped-login", http_status=401, content_type=ctype)
                if r.status_code != 200:
                    return Result(url, "http-error", http_status=r.status_code, content_type=ctype)
                if behind_login(urlsplit(final).hostname):
                    return Result(url, "skipped-login", detail=final, http_status=200)
                if (
                    re.search(r"login|signin|sso|auth", urlsplit(final).path.lower())
                    and final != url
                ):
                    return Result(url, "skipped-login", detail=final, http_status=200)
                if ctype and not any(k in ctype for k in OK_CTYPES):
                    return Result(
                        url, "not-document", content_type=ctype, http_status=200, detail=final
                    )
                clen = int(r.headers.get("content-length") or 0)
                if clen > self.max_file:
                    return Result(url, "too-large", bytes=clen, content_type=ctype, http_status=200)
                if clen and not self.budget.reserve(clen):
                    return Result(url, "budget-stop", detail="6 GB cap reached")
                reserved = clen
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(dest.suffix + ".part")
                h, n = hashlib.sha256(), 0
                try:
                    with tmp.open("wb") as f:
                        for chunk in r.iter_bytes(65536):
                            n += len(chunk)
                            if n > self.max_file:
                                tmp.unlink(missing_ok=True)
                                if reserved:
                                    self.budget.release(reserved)
                                    reserved = 0
                                return Result(
                                    url, "too-large", bytes=n, content_type=ctype, http_status=200
                                )
                            if n > reserved:  # no/under-stated Content-Length
                                if not self.budget.reserve(n - reserved):
                                    tmp.unlink(missing_ok=True)
                                    self.budget.release(reserved)
                                    reserved = 0
                                    return Result(url, "budget-stop", detail="6 GB cap reached")
                                reserved = n
                            h.update(chunk)
                            f.write(chunk)
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
                if reserved != n:  # Content-Length overstated
                    self.budget.release(reserved - n)
                tmp.replace(dest)
                return Result(
                    url,
                    "ok",
                    str(dest.relative_to(self.root)),
                    n,
                    h.hexdigest(),
                    ctype,
                    200,
                )
        except httpx.HTTPError as e:
            if reserved:
                self.budget.release(reserved)
            return Result(url, "http-error", detail=f"{type(e).__name__}: {e}"[:300])


# --------------------------------------------------------------------------- DB driver
CANDIDATES_SQL = """
SELECT DISTINCT ON (m.url) m.url, m.type, u.country, u.name, c.code, c.title
FROM materials m JOIN course_offerings o ON o.id=m.offering_id
JOIN courses c ON c.id=o.course_id JOIN universities u ON u.id=c.university_id
WHERE %(subset)s = ANY(u.subset_tags)
ORDER BY m.url, m.id
"""


def run(subset: str, root: Path = MATERIALS_ROOT, workers: int = 8, limit: int | None = None):
    from .db import connect, ensure_material_files

    conn = connect()
    ensure_material_files(conn)
    rows = [r for r in conn.execute(CANDIDATES_SQL, {"subset": subset}).fetchall()]
    rows = [r for r in rows if eligible(r[1], r[0])]
    done = {
        u: (s, p) for u, s, p in conn.execute("SELECT url, status, local_path FROM material_files")
    }
    used = conn.execute("SELECT coalesce(sum(bytes),0) FROM material_files WHERE status='ok'")
    budget = Budget(int(used.fetchone()[0]))
    todo = []
    for r in rows:
        st, p = done.get(r[0], (None, None))
        if st == "ok" and p and (root / p).exists():
            continue
        if st in FINAL and st != "ok":
            continue
        todo.append(r)
    if limit:
        todo = todo[:limit]
    print(f"{len(rows)} eligible urls, {len(todo)} to fetch, {budget.used / 1e6:.0f} MB used")

    taken: dict[Path, set[str]] = defaultdict(set)
    by_host: dict[str, list] = defaultdict(list)
    for r in todo:
        by_host[urlsplit(r[0]).netloc].append(r)
    dl = Downloader(root, budget)
    dblock = threading.Lock()
    seen_hash: dict[str, str] = {}
    for sha, p in conn.execute(
        "SELECT sha256, local_path FROM material_files WHERE status='ok'"
    ).fetchall():
        seen_hash[sha] = p

    def save(res: Result) -> None:
        with dblock:
            conn.execute(
                """INSERT INTO material_files (url,status,local_path,bytes,sha256,content_type,
                     http_status,detail,fetched_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (url) DO UPDATE SET status=EXCLUDED.status,
                     local_path=EXCLUDED.local_path, bytes=EXCLUDED.bytes,
                     sha256=EXCLUDED.sha256, content_type=EXCLUDED.content_type,
                     http_status=EXCLUDED.http_status, detail=EXCLUDED.detail,
                     fetched_at=EXCLUDED.fetched_at""",
                (res.url, res.status, res.local_path, res.bytes, res.sha256, res.content_type,
                 res.http_status, res.detail, utcnow()),
            )  # fmt: skip
            conn.commit()

    def work(host: str) -> None:
        for url, _t, country, uname, code, title in by_host[host]:
            if budget.exhausted:
                return
            with dblock:
                folder_key = root / slug(country) / slug(uname) / slug(code or title, 60)
                dest = target_path(root, country, uname, code or title, url, taken[folder_key])
                taken[folder_key].add(dest.name)
            res = dl.fetch(url, dest)
            if res.status == "ok":
                with dblock:
                    dup = seen_hash.get(res.sha256)
                    seen_hash.setdefault(res.sha256, res.local_path)
                if dup and dup != res.local_path and (root / dup).exists():
                    dest.unlink(missing_ok=True)  # identical bytes already stored elsewhere
                    res.local_path = dup
                    res.detail = "duplicate content; points at existing file"
            if res.status != "budget-stop":
                save(res)
            print(f"{res.status:15s} {url}", flush=True)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(work, by_host))
    s = conn.execute(
        "SELECT status, count(*), coalesce(sum(bytes),0) FROM material_files "
        "GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    return {st: {"files": n, "bytes": int(b)} for st, n, b in s}
