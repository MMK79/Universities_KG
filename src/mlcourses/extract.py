"""Heuristic (LLM-free) extraction of course metadata and course materials from a page."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .books import BookHit, match_books, unmatched_citations
from .discover import ML_TITLE

CORE_ML = re.compile(
    r"machine[\s-]+learning|maschinell\w*\s+lernen|maskinl[æa]ring|koneoppimi|机器学习|機械学習|"
    r"기계\s?학습|머신\s?러닝|یادگیری\s+ماشین",
    re.I,
)
COURSE_WORDS = re.compile(
    r"syllabus|lecture|homework|assignment|problem set|exam|midterm|grading|instructor|"
    r"office hours|prerequisite|semester|credits?|\bTAs?\b|teaching assistant|ECTS|"
    r"textbook|course (?:description|overview|information|schedule)|vorlesung|übung|"
    r"prüfung|klausur|leistungspunkte|pensum|课程|作业|考试|講義|課題|강의|과제|시험|"
    r"درس|تمرین|امتحان|کلاس",
    re.I,
)
CODE_RES = [
    re.compile(r"\b(\d{2}-\d{3})\b"),  # CMU 10-601
    re.compile(r"\b(6\.(?:\d{3,4}|S\d{3}))\b"),  # MIT 6.036 / 6.3900
    re.compile(r"\b((?:[A-Z]{2,5}|[A-Z]{2,4}\s?[A-Z]{1,2})\s?-?\d{3,5}[A-Z]?)\b"),  # CS 229, IN4050
]
CODE_STOP = {
    "ISBN",
    "PDF",
    "ROOM",
    "FALL",
    "COVID",
    "ISO",
    "RFC",
    "HTTP",
    "UTF",
    "GPU",
    "CPU",
    "SPRING",
    "WS",
    "SS",
    "AM",
    "PM",
    "UTC",
    "GMT",
    "IEEE",
    "ACM",
    "ICML",
    "NIPS",
    "NEURIPS",
    "ICLR",
    "AAAI",
    "CVPR",
    "TOEFL",
    "ISSN",
    "DOI",
    "ARXIV",
    "MSC",
    "BSC",
    "TEL",
    "FAX",
    "PHONE",
    "ZIP",
    "BOX",
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "SEPT",
    "OCT",
    "NOV",
    "DEC",
    "TITLE",
}
TERM_RES = [
    re.compile(
        r"\b(Spring|Fall|Autumn|Winter|Summer)\s*(?:Semester|Term|Quarter)?\s*'?"
        r"((?:19|20)\d{2}|\d{2})\b",
        re.I,
    ),
    re.compile(
        r"\b(WS|SS|WiSe|SoSe|Wintersemester|Sommersemester)\s*((?:20)?\d{2}"
        r"(?:\s?/\s?(?:20)?\d{2})?)\b"
    ),
    re.compile(r"\b((?:19|20)\d{2})\s*(Spring|Fall|Autumn|Winter|Summer)\b", re.I),
    re.compile(r"(20\d{2})\s*(?:年)?\s*(春|秋|夏|冬)"),
]
INSTRUCTOR_RE = re.compile(
    r"(?:Instructors?|Lecturers?|Professors?|Taught by|Teacher|Dozent(?:en|in)?|Lehrende|"
    r"Faculty|Course (?:leader|coordinator|responsible)|Examiner|授课教师|主讲|担当教員|교수|"
    r"استاد)\s*(?:\(s\))?\s*[:：\-–]\s*([^\n]{3,140})",
    re.I,
)
DEPT_HOSTS = {
    "cs": "Computer Science",
    "eecs": "EECS",
    "stat": "Statistics",
    "stats": "Statistics",
    "math": "Mathematics",
    "ece": "ECE",
    "ml": "Machine Learning",
    "cse": "Computer Science & Engineering",
    "informatik": "Informatics",
    "in": "Informatics",
    "ee": "Electrical Engineering",
    "ics": "Information & CS",
    "ce": "Computer Engineering",
    "ai": "AI",
}


@dataclass
class Material:
    type: str
    url: str
    anchor: str
    found_at: str


@dataclass
class Offering:
    url: str
    title: str
    code: str | None
    topic: str
    term: str | None
    year: int | None
    instructor: str | None
    level: str
    department: str | None
    snippet: str
    books: list[BookHit] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    extra_sources: list[str] = field(default_factory=list)


def page_title(text: str) -> str:
    title = ""
    for ln in text.splitlines()[:40]:
        if ln.startswith("TITLE: "):
            title = ln[7:]
            continue
        if ln.startswith("# "):
            h1 = ln[2:].strip()
            if ML_TITLE.search(h1) or not title:
                return h1[:200]
    if not title:
        for ln in text.splitlines()[:10]:
            if ML_TITLE.search(ln) and len(ln) < 200:
                return ln.strip()
    return title[:200]


def is_ml_course(text: str, title: str) -> tuple[bool, str]:
    head = title + "\n" + text[:1500]
    if not ML_TITLE.search(head):
        return False, ""
    signals = {m.group(0).lower() for m in COURSE_WORDS.finditer(text[:20000])}
    if len(signals) < 3:
        return False, ""
    topic = "ml-core" if CORE_ML.search(title) or CORE_ML.search(text[:300]) else "ml-related"
    return True, topic


def _codes(src: str) -> list[str]:
    out = []
    for rx in CODE_RES:
        for m in rx.finditer(src):
            c = m.group(1).strip()
            prefix = re.match(r"[A-Za-z]+", c)
            if prefix and prefix.group(0).upper() in CODE_STOP:
                continue
            out.append(re.sub(r"\s+", " ", c))
    return out


def find_code(title: str, text: str, url: str) -> str | None:
    """Code in the page title wins; otherwise the most frequent code on the page."""
    t = _codes(title)
    if t:
        return t[0]
    body = _codes(text[:8000])
    if body:
        from collections import Counter

        cnt = Counter(body)
        best = max(cnt.values())
        return next(c for c in body if cnt[c] == best)
    u = _codes(urlsplit(url).path.replace("/", " ").upper())
    return u[0] if u else None


def find_term(title: str, text: str, url: str) -> tuple[str | None, int | None]:
    for src in (title, text[:4000], url):
        for rx in TERM_RES:
            m = rx.search(src)
            if not m:
                continue
            a, b = m.group(1), m.group(2)
            if a[:1].isdigit():
                a, b = b, a
            yr = re.search(r"(?:19|20)?\d{2}", b).group(0)
            year = int(yr) if len(yr) == 4 else 2000 + int(yr)
            return f"{a} {b}".strip(), year
    m = re.search(r"\b(20[0-2]\d)\b", title + " " + url)
    return (None, int(m.group(1))) if m else (None, None)


def find_instructor(text: str) -> str | None:
    m = INSTRUCTOR_RE.search(text[:8000])
    if not m:
        return None
    v = re.split(r"\s{2,}|\||;|Office|Email|E-mail|TAs?:|Teaching", m.group(1))[0].strip(" ,.:")
    return v[:120] or None


def find_level(title: str, text: str, code: str | None) -> str:
    t = (title + " " + text[:6000]).lower()
    grad = re.search(
        r"\bgraduate\b|master'?s?\b|\bmsc\b|\bph\.?d\b|doctoral|研究生|大学院|대학원|"
        r"کارشناسی ارشد",
        t,
    )
    ug = re.search(r"undergraduate|bachelor|\bbsc\b|本科|学部|학부|کارشناسی(?! ارشد)", t)
    if code:
        n = re.search(r"(\d{3,4})", code.replace("-", ""))
        if n and re.match(r"[A-Z]{2,5}\s?\d", code):
            num = int(n.group(1)[:3])
            if num >= 500 and not ug:
                return "grad"
            if num < 400 and not grad:
                return "undergrad"
    if grad and ug:
        return "mixed"
    if grad:
        return "grad"
    if ug:
        return "undergrad"
    return "unknown"


def find_department(url: str, text: str) -> str | None:
    host = urlsplit(url).netloc.lower().split(".")
    for part in host[:-2]:
        if part in DEPT_HOSTS:
            return DEPT_HOSTS[part]
    m = re.search(
        r"(?:Department|School|Faculty|Institute) of ([A-Z][A-Za-z &,]{3,60})", text[:6000]
    )
    return m.group(1).strip(" ,") if m else None


MAT_RULES: list[tuple[str, re.Pattern]] = [
    (
        "exam_solution",
        re.compile(
            r"(exam|midterm|final|quiz|klausur|prüfung).{0,30}(solution|sol\b|"
            r"answer|key|lösung)|(solution|sol)s?.{0,20}(exam|midterm|final)",
            re.I,
        ),
    ),
    (
        "homework_solution",
        re.compile(
            r"(hw|homework|assignment|problem.?set|pset|exercise|"
            r"übung|sheet).{0,30}(solution|sol\b|answer|lösung)|"
            r"(solution|sol)s?[\s_-]?(hw|homework|\d)",
            re.I,
        ),
    ),
    (
        "exam",
        re.compile(
            r"\b(exam|midterm|final[\s_-]?exam|past[\s_-]?paper|quiz|klausur|"
            r"prüfung)s?\b",
            re.I,
        ),
    ),
    (
        "homework",
        re.compile(
            r"\b(hw\d*|homework|assignment|problem[\s_-]?set|pset\d*|"
            r"exercise|übung|übungsblatt|sheet\d*)s?\b",
            re.I,
        ),
    ),
    (
        "video",
        re.compile(
            r"youtube\.com|youtu\.be|vimeo\.com|panopto|bilibili|video|recording|"
            r"mediasite|echo360",
            re.I,
        ),
    ),
    ("slides", re.compile(r"slides?|\.pptx?\b|lecture[\s_-]?\d+.*\.pdf|folien|handout", re.I)),
    ("lecture_notes", re.compile(r"lecture[\s_-]?notes|\bnotes\b|scribe|skript|script", re.I)),
]


def classify_material(href: str, anchor: str) -> str | None:
    s = f"{anchor} {href.rsplit('/', 1)[-1]}"
    host = urlsplit(href).netloc.lower()
    if re.search(r"youtube\.com|youtu\.be|vimeo\.com|bilibili", host):
        return "video"
    for typ, rx in MAT_RULES:
        if rx.search(s):
            return typ
    return None


def extract_materials(links: list[tuple[str, str]], found_at: str) -> list[Material]:
    out, seen = [], set()
    for href, anchor in links:
        if not href.startswith("http") or href in seen:
            continue
        typ = classify_material(href, anchor)
        if typ:
            seen.add(href)
            out.append(Material(typ, href, anchor[:200], found_at))
    return out


FOLLOW_RE = re.compile(
    r"syllabus|course info|logistics|information|textbook|readings?|"
    r"literature|literatur|resources|about|policies|general|schedule|"
    r"教材|参考|教科書|교재|منابع",
    re.I,
)


def follow_links(links: list[tuple[str, str]], base: str, limit: int) -> list[str]:
    bh = urlsplit(base)
    base_dir = bh.path.rsplit("/", 1)[0]
    out = []
    for href, anchor in links:
        p = urlsplit(href)
        if p.netloc != bh.netloc or href.rstrip("/") == base.rstrip("/"):
            continue
        if not p.path.startswith(base_dir):  # stay inside the course site
            continue
        if FOLLOW_RE.search(anchor) or FOLLOW_RE.search(p.path.rsplit("/", 1)[-1]):
            if href not in out:
                out.append(href)
        if len(out) >= limit:
            break
    return out


def build_offering(
    page_url: str, text: str, links: list[tuple[str, str]], hint_title: str = ""
) -> Offering | None:
    """hint_title: the search-result title, often carries the course code the page hides."""
    title = page_title(text)
    ok, topic = is_ml_course(text, title)
    if not ok:
        return None
    html_title = text[7 : text.find("\n")] if text.startswith("TITLE: ") else ""
    code = find_code(f"{html_title} {title} {hint_title}", text, page_url)
    term, year = find_term(title, text, page_url)
    books = match_books(text, links)
    books += unmatched_citations(text, books)
    for h in books:
        h.source_url = page_url
    snippet = "\n".join(text.splitlines()[:12])[:800]
    return Offering(
        url=page_url,
        title=title or page_url,
        code=code,
        topic=topic,
        term=term,
        year=year,
        instructor=find_instructor(text),
        level=find_level(title, text, code),
        department=find_department(page_url, text),
        snippet=snippet,
        books=books,
        materials=extract_materials(links, page_url),
    )


def merge_followed(off: Offering, url: str, text: str, links: list[tuple[str, str]]) -> None:
    """Merge books/materials from a followed sub-page (syllabus, readings) into the offering."""
    have = {b.slug for b in off.books if b.slug}
    new = match_books(text, links)
    new += unmatched_citations(text, new)
    for h in new:
        h.source_url = url
        if h.slug is None or h.slug not in have:
            off.books.append(h)
    known = {m.url for m in off.materials}
    off.materials += [m for m in extract_materials(links, url) if m.url not in known]
    off.extra_sources.append(url)
    if off.instructor is None:
        off.instructor = find_instructor(text)
