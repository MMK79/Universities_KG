"""LLM-free textbook matcher: canonical catalogue + regex + ISBN, with confidence.

Confidence levels
  0.95  canonical URL of the book is linked, or its ISBN appears
  0.90  distinctive title + an author surname in the citation window
  0.75  distinctive title alone
  0.65  generic title ("Deep Learning", "Machine Learning") + author in the window
Anything else is not a match. Citation-like lines inside a references section that match
no catalogue book are kept as `regex-unmatched` so the catalogue can grow.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache

from .config import BOOKS_TOML

ISBN_RE = re.compile(
    r"\b(?:ISBN(?:-1[03])?:?\s*)?((?:97[89][-\s]?)?\d[-\s]?\d{2,5}[-\s]?\d{2,7}"
    r"[-\s]?\d{1,7}[-\s]?[\dX])\b"
)
SECTION_RE = re.compile(
    r"text\s?books?|required\s+(?:text|reading)|readings?|references?|literature|literatur|"
    r"bibliograph|course\s+materials?|recommended|resources|books?|lehrbuch|pensum|"
    r"教材|参考书|参考文献|教科書|参考書|교재|참고|منابع|مراجع|کتاب",
    re.I,
)
YEAR = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")
NOT_CITATION = re.compile(
    r"updated|solution|exam|midterm|homework|lecture|due|deadline|"
    r"office|room|copyright|©|all rights",
    re.I,
)
AUTHORISH = re.compile(r"[A-Z][a-z]+,\s?[A-Z]\.|[A-Z]\.\s?[A-Z][a-z]+|\bet al\.|\band\b [A-Z]")
CITATION_HINT = re.compile(
    r"ISBN|Springer|MIT Press|Cambridge|O'Reilly|Wiley|Elsevier|"
    r"Morgan Kaufmann|Pearson|Prentice|McGraw|Oxford University Press|Academic Press|CRC|"
    r"Manning|Packt|No Starch|edition|\b\d(?:st|nd|rd|th)\s+ed\b",
    re.I,
)
STOP_WORDS = {"and", "of", "to", "in", "for", "with", "from", "the", "a", "an", "on"}


def _title_cased(s: str) -> bool:
    """'Understanding Machine Learning' yes; prose 'understanding machine learning' no."""
    words = [w for w in re.findall(r"[^\W\d_]+", s) if w.lower() not in STOP_WORDS]
    if not words or not all(w.isascii() for w in words):
        return True  # CJK etc.: no case information
    return sum(w[0].isupper() for w in words) >= max(2, len(words) - 1)


@dataclass(frozen=True)
class Book:
    slug: str
    title: str
    authors: str
    year: int | None
    edition: str | None
    publisher: str | None
    isbn13: str | None
    generic: bool
    title_res: tuple[re.Pattern, ...]
    author_res: tuple[re.Pattern, ...]
    url_patterns: tuple[str, ...]
    free_url: str | None = None


@dataclass
class BookHit:
    slug: str | None
    raw_citation: str
    confidence: float
    role: str
    method: str
    source_url: str = ""


@lru_cache(maxsize=1)
def catalogue() -> tuple[Book, ...]:
    data = tomllib.loads(BOOKS_TOML.read_text())
    out = []
    for b in data["book"]:
        out.append(
            Book(
                slug=b["slug"],
                title=b["title"],
                authors=b["authors"],
                year=b.get("year"),
                edition=b.get("edition"),
                publisher=b.get("publisher"),
                isbn13=b.get("isbn13"),
                generic=b.get("generic", False),
                title_res=tuple(re.compile(p, re.I) for p in b["title_patterns"]),
                author_res=tuple(re.compile(p, re.I) for p in b.get("author_patterns", [])),
                url_patterns=tuple(b.get("url_patterns", [])),
                free_url=b.get("free_url"),
            )
        )
    return tuple(out)


def norm_isbn(s: str) -> str:
    return re.sub(r"[^\dX]", "", s.upper())


def isbn10_to_13(i10: str) -> str:
    core = "978" + i10[:9]
    total = sum((1 if k % 2 == 0 else 3) * int(c) for k, c in enumerate(core))
    return core + str((10 - total % 10) % 10)


def find_isbns(text: str) -> list[str]:
    out = []
    for m in ISBN_RE.finditer(text):
        n = norm_isbn(m.group(1))
        if len(n) == 13 and n.startswith(("978", "979")):
            out.append(n)
        elif len(n) == 10 and "ISBN" in text[max(0, m.start() - 8) : m.end()].upper():
            out.append(isbn10_to_13(n))
    return out


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    s = text.rfind("\n", 0, pos) + 1
    e = text.find("\n", pos)
    return s, (len(text) if e == -1 else e)


def _window(text: str, start: int, end: int) -> str:
    """The citation line plus its neighbours (citations often wrap in PDFs)."""
    s, e = _line_bounds(text, start)
    ps, _ = _line_bounds(text, max(s - 2, 0))
    _, ne = _line_bounds(text, min(e + 1, len(text)))
    return text[max(ps, start - 250) : min(ne, end + 250)]


def _citation(text: str, start: int, end: int) -> str:
    """The line holding the match (the raw citation string), clipped around the match."""
    s, e = _line_bounds(text, start)
    if e - s < 50:  # short line (heading or wrapped title) -> add the next line
        _, e = _line_bounds(text, min(e + 1, len(text)))
    if e - s > 320:
        s, e = max(s, start - 120), min(e, end + 200)
    return " ".join(text[s:e].split())


def _role(text: str, pos: int) -> str:
    """Role from the nearest preceding heading-ish line (within 3000 chars)."""
    chunk = text[max(0, pos - 3000) : pos]
    for line in reversed(chunk.splitlines()[-60:]):
        ln = line.strip().lower()
        if not ln or len(ln) > 80:
            continue
        if re.search(r"required|textbook|lehrbuch|教材|教科書|교재|pensum|main text", ln):
            return "textbook"
        if re.search(
            r"recommend|optional|further|additional|supplement|reference|reading|"
            r"literatur|参考|참고|منابع|resources",
            ln,
        ):
            return "recommended"
    return "mentioned"


def _url_hit(b: Book, text: str, href: str, anchor: str) -> BookHit:
    """A link to the book's canonical URL. Pick the anchor occurrence whose window names an
    author (anchors like "Machine Learning" also occur in the course title)."""
    best = None
    if anchor and len(anchor) >= 4:
        start = 0
        while (i := text.find(anchor, start)) >= 0:
            win = _window(text, i, i + len(anchor))
            if any(a.search(win) for a in b.author_res):
                best = (i, _citation(text, i, i + len(anchor)))
                break
            start = i + 1
    if best:
        i, win = best
        return BookHit(b.slug, f"{win} <{href}>", 0.95, _role(text, i), "regex")
    return BookHit(b.slug, f"[{anchor}]({href})", 0.9, "mentioned", "regex")


def match_books(text: str, links: list[tuple[str, str]] = ()) -> list[BookHit]:
    hits: dict[str, BookHit] = {}

    def keep(h: BookHit) -> None:
        cur = hits.get(h.slug)
        if cur is None or h.confidence > cur.confidence:
            hits[h.slug] = h

    cat = catalogue()
    isbn_index = {b.isbn13: b for b in cat if b.isbn13}
    for isbn in find_isbns(text):
        b = isbn_index.get(isbn)
        if b:
            i = text.find(isbn[-4:])
            keep(
                BookHit(
                    b.slug,
                    _window(text, max(i, 0), max(i, 0) + 4).strip()[:400],
                    0.95,
                    _role(text, max(i, 0)),
                    "regex",
                )
            )
    for b in cat:
        for href, anchor in links:
            if any(p.lower() in href.lower() for p in b.url_patterns if p):
                keep(_url_hit(b, text, href, anchor))
                break
        for tre in b.title_res:
            for m in tre.finditer(text):
                win = _window(text, m.start(), m.end())
                has_author = any(a.search(win) for a in b.author_res)
                if b.generic and not has_author:
                    continue
                if not has_author and not _title_cased(m.group(0)):
                    continue  # a common phrase in prose, not a citation
                if b.generic:
                    conf = 0.65
                else:
                    conf = 0.9 if has_author else 0.75
                keep(
                    BookHit(
                        b.slug,
                        _citation(text, m.start(), m.end()),
                        conf,
                        _role(text, m.start()),
                        "regex",
                    )
                )
    return list(hits.values())


def unmatched_citations(text: str, matched: list[BookHit], limit: int = 15) -> list[BookHit]:
    """Citation-looking lines under a references/textbook heading that matched nothing."""
    lines = text.splitlines()
    out: list[BookHit] = []
    matched_raw = " ".join(h.raw_citation for h in matched)
    in_sec, budget = False, 0
    for ln in lines:
        s = ln.strip().lstrip("#*-• ").strip()
        if not s:
            continue
        if len(s) <= 60 and SECTION_RE.search(s) and not CITATION_HINT.search(s):
            in_sec, budget = True, 20
            continue
        if not in_sec:
            continue
        budget -= 1
        if budget <= 0:
            in_sec = False
            continue
        if ln.lstrip().startswith("#"):
            in_sec = False
            continue
        if not (25 <= len(s) <= 400) or NOT_CITATION.search(s) or s[:60] in matched_raw:
            continue
        if any(t.search(s) for b in catalogue() for t in b.title_res if not b.generic):
            continue  # a catalogue book (matched elsewhere on the page)
        if CITATION_HINT.search(s) or (YEAR.search(s) and AUTHORISH.search(s) and len(s) > 40):
            out.append(BookHit(None, s, 0.3, "recommended", "regex-unmatched"))
            if len(out) >= limit:
                break
    return out
