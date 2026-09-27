"""Polite fetching: robots.txt, per-host rate limit, honest UA, on-disk cache, audit log.

Everything that touches the network goes through `Fetcher`, so the etiquette rules are
enforced in one place. Firecrawl is used for (a) site-restricted web search and (b) as a
fallback renderer for JS-heavy pages — but only after *our* robots.txt check passes.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import CACHE, MIN_DELAY_PER_HOST, REQUEST_TIMEOUT, USER_AGENT

FIRECRAWL_API = "https://api.firecrawl.dev/v2"
_FC_LOCK = threading.Lock()  # one Firecrawl call at a time across worker threads
_FC_MIN_GAP = 4.0
_fc_last = [0.0]


def firecrawl_post(endpoint: str, body: dict, key: str, timeout: float = 120) -> dict:
    """POST to Firecrawl, serialised and spaced; retries 429/5xx with backoff."""
    for attempt in range(6):
        with _FC_LOCK:
            gap = _FC_MIN_GAP - (time.monotonic() - _fc_last[0])
            if gap > 0:
                time.sleep(gap)
            r = httpx.post(
                f"{FIRECRAWL_API}/{endpoint}",
                json=body,
                timeout=timeout,
                headers={"Authorization": f"Bearer {key}"},
            )
            _fc_last[0] = time.monotonic()
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("retry-after") or 0) or 10 * (attempt + 1)
            time.sleep(min(wait, 90))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()


@dataclass
class Page:
    url: str  # final URL after redirects
    requested_url: str
    fetched_at: str
    method: str  # httpx | firecrawl
    content_type: str
    text: str  # plain text / markdown used for extraction
    html: str  # raw HTML ('' for PDFs / firecrawl markdown)
    links: list[tuple[str, str]]  # (href, anchor text), absolute
    cache_path: str
    sha1: str

    def to_json(self) -> dict:
        return self.__dict__ | {"links": [list(x) for x in self.links]}

    @classmethod
    def from_json(cls, d: dict) -> Page:
        d = dict(d)
        d["links"] = [tuple(x) for x in d["links"]]
        return cls(**d)


class Fetcher:
    def __init__(self, log_path: Path, offline: bool = False):
        self.offline = offline
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en,*;q=0.5"},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        self.fc_key = os.environ.get("FIRECRAWL_API_KEY", "")

    # ---------------------------------------------------------------- audit log
    def log(self, kind: str, status: str, url: str = "", query: str = "", detail: str = ""):
        rec = {
            "at": now_iso(),
            "kind": kind,
            "url": url,
            "query": query,
            "status": status,
            "detail": detail[:500],
        }
        with self._lock, self.log_path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------------------------------------------------------------- etiquette
    def _wait(self, host: str) -> None:
        with self._lock:
            last = self._last.get(host, 0.0)
            delay = MIN_DELAY_PER_HOST - (time.monotonic() - last)
            self._last[host] = time.monotonic() + max(delay, 0)
        if delay > 0:
            time.sleep(delay)

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                self._wait(parts.netloc)
                r = self.client.get(base + "/robots.txt")
                if r.status_code >= 400:
                    rp = None  # no robots.txt (or forbidden to read) -> allowed
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError as e:
                self.log("robots", "error", base + "/robots.txt", detail=str(e))
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    # ---------------------------------------------------------------- search
    def search(self, query: str, limit: int = 10, country: str | None = None) -> list[dict]:
        cpath = CACHE / "search" / f"{sha1(query + str(limit))}.json"
        if cpath.exists():
            self.log("search", "cached", query=query)
            return json.loads(cpath.read_text())["results"]
        if self.offline or not self.fc_key:
            self.log("search", "skipped_offline", query=query)
            return []
        body = {"query": query, "limit": limit, "sources": ["web"]}
        if country:
            body["country"] = country
        try:
            web = firecrawl_post("search", body, self.fc_key, 90).get("data", {}).get("web", [])
        except httpx.HTTPError as e:
            self.log("search", "error", query=query, detail=str(e))
            return []
        results = [
            {
                "url": w.get("url", ""),
                "title": w.get("title", ""),
                "description": w.get("description", ""),
            }
            for w in web
        ]
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(
            json.dumps(
                {"query": query, "at": now_iso(), "results": results}, ensure_ascii=False, indent=1
            )
        )
        self.log("search", "ok", query=query, detail=f"{len(results)} results")
        return results

    # ---------------------------------------------------------------- fetch
    def fetch(self, url: str) -> Page | None:
        meta = CACHE / "pages" / f"{sha1(url)}.json"
        if meta.exists():
            d = json.loads(meta.read_text())
            if d.get("failed"):
                return None
            return Page.from_json(d)
        if self.offline:
            return None
        if not self.allowed(url):
            self.log("robots", "robots_disallowed", url)
            self._remember_failure(meta, url, "robots_disallowed")
            return None
        page = self._fetch_httpx(url)
        if page is None or (len(page.text) < 400 and not url.lower().endswith(".pdf")):
            fc = self._fetch_firecrawl(url)
            page = fc or page
        if page is None:  # transient failures are not cached: a rerun retries them
            return None
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps(page.to_json(), ensure_ascii=False))
        return page

    def _remember_failure(self, meta: Path, url: str, why: str) -> None:
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"url": url, "failed": why, "at": now_iso()}))

    def _store_raw(self, url: str, data: bytes, ext: str) -> tuple[str, str]:
        h = hashlib.sha1(data).hexdigest()
        p = CACHE / "raw" / f"{sha1(url)}.{ext}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return str(p.relative_to(CACHE.parent)), h

    def _fetch_httpx(self, url: str) -> Page | None:
        from .textify import html_to_text_and_links, pdf_to_text

        self._wait(urlsplit(url).netloc)
        try:
            r = self.client.get(url)
        except httpx.HTTPError as e:
            self.log("fetch", "error", url, detail=str(e))
            return None
        if r.status_code != 200:
            self.log("fetch", f"http_{r.status_code}", url)
            return None
        final = str(r.url)
        if final != url and not self.allowed(final):
            self.log("robots", "robots_disallowed", final, detail=f"redirect from {url}")
            return None
        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if "pdf" in ctype or final.lower().endswith(".pdf"):
            path, h = self._store_raw(url, r.content, "pdf")
            try:
                text = pdf_to_text(io.BytesIO(r.content))
            except Exception as e:  # noqa: BLE001 - broken PDFs are common
                self.log("fetch", "error", url, detail=f"pdf parse: {e}")
                return None
            self.log("fetch", "ok", url, detail=f"pdf {len(text)} chars")
            return Page(final, url, now_iso(), "httpx", "application/pdf", text, "", [], path, h)
        if "html" not in ctype and "text" not in ctype:
            self.log("fetch", "skipped_type", url, detail=ctype)
            return None
        html = r.text
        path, h = self._store_raw(url, r.content, "html")
        text, links = html_to_text_and_links(html, final)
        self.log("fetch", "ok", url, detail=f"html {len(text)} chars")
        return Page(final, url, now_iso(), "httpx", ctype, text, html, links, path, h)

    def _fetch_firecrawl(self, url: str) -> Page | None:
        from .textify import markdown_links

        if not self.fc_key:
            return None
        try:
            body = {"url": url, "formats": ["markdown", "links"], "onlyMainContent": False}
            d = firecrawl_post("scrape", body, self.fc_key).get("data", {})
        except httpx.HTTPError as e:
            self.log("firecrawl_scrape", "error", url, detail=str(e))
            return None
        md = d.get("markdown") or ""
        if not md:
            self.log("firecrawl_scrape", "empty", url)
            return None
        meta = d.get("metadata") or {}
        if meta.get("title"):
            md = f"TITLE: {meta['title']}\n{md}"
        path, h = self._store_raw(url, md.encode(), "md")
        final = meta.get("sourceURL") or url
        self.log("firecrawl_scrape", "ok", url, detail=f"md {len(md)} chars")
        return Page(
            final,
            url,
            now_iso(),
            "firecrawl",
            "text/markdown",
            md,
            "",
            markdown_links(md, final),
            path,
            h,
        )
