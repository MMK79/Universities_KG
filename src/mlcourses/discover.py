"""Find candidate ML course pages for a university via site-restricted web search."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .config import MAX_CANDIDATES, University

# Machine-learning phrase per language; the English one is always used as well.
ML_TERMS = {
    "Germany": "Maschinelles Lernen",
    "Netherlands": "machine learning",
    "Norway": "maskinlæring",
    "Finland": "koneoppiminen",
    "China": "机器学习",
    "Hong Kong": "machine learning",
    "Japan": "機械学習",
    "Korea, Republic of": "기계학습",
    "Iran": "یادگیری ماشین",
}
LOCAL_COURSE_WORD = {
    "Germany": "Vorlesung Literatur",
    "Norway": "emne pensum",
    "Finland": "kurssi kirjallisuus",
    "China": "课程 教材",
    "Japan": "講義 教科書",
    "Korea, Republic of": "강의 교재",
    "Iran": "درس منابع",
}
ISO = {
    "Germany": "DE",
    "Norway": "NO",
    "Finland": "FI",
    "China": "CN",
    "Japan": "JP",
    "Korea, Republic of": "KR",
    "Iran": "IR",
    "Netherlands": "NL",
    "Hong Kong": "HK",
    "Canada": "CA",
    "United States": "US",
}

ML_TITLE = re.compile(
    r"machine[\s-]+learning|maschinell\w*\s+lernen|maskinl[æa]ring|koneoppimi|机器学习|機械学習|"
    r"기계\s?학습|머신\s?러닝|یادگیری\s+ماشین|statistical\s+learning|deep\s+learning|"
    r"pattern\s+recognition|mustererkennung|\bML\b",
    re.I,
)
COURSE_SIGNAL = re.compile(
    r"syllabus|course|lecture|class|semester|spring|fall|autumn|winter|term|quarter|"
    r"homework|assignment|exam|textbook|module|modul|vorlesung|übung|ects|credits|"
    r"\b[A-Z]{2,5}\s?-?\d{3,4}\b|\b\d{2}-\d{3}\b|\b6\.\d{3,4}\b|课程|講義|강의|درس",
    re.I,
)
NEGATIVE = re.compile(
    r"news|press|event|seminar series|talk|colloquium|job|career|position|phd position|"
    r"people|faculty|profile|publication|paper|thesis|dissertation|arxiv|workshop|"
    r"conference|admission|program(me)? overview|degree|bachelor's program|master's program|"
    r"blog|story|podcast",
    re.I,
)
BAD_URL = re.compile(
    r"/(news|events?|people|person|staff|profile|publications?|blog|stories)/", re.I
)


def queries_for(u: University) -> list[tuple[str, str | None]]:
    d = u.search_domain
    qs = [(f'site:{d} "machine learning" course syllabus textbook', ISO.get(u.country))]
    term = ML_TERMS.get(u.country)
    if term and term.lower() != "machine learning":
        extra = LOCAL_COURSE_WORD.get(u.country, "")
        qs.append((f"site:{d} {term} {extra}".strip(), ISO.get(u.country)))
    else:
        qs.append((f'site:{d} "machine learning" lecture homework', ISO.get(u.country)))
    return qs


def fallback_queries(u: University) -> list[tuple[str, str | None]]:
    """Used when the main queries return (almost) nothing — e.g. thinly indexed sites."""
    return [(f"site:{u.search_domain} machine learning", None)]


def on_domain(url: str, u: University) -> bool:
    host = urlsplit(url).netloc.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in u.domains + [u.search_domain])


def score(result: dict) -> int:
    title = result.get("title", "")
    desc = result.get("description", "")[:800]
    url = result.get("url", "")
    s = 0
    if ML_TITLE.search(title):
        s += 4
    elif ML_TITLE.search(url.replace("-", " ").replace("_", " ")):
        s += 2
    if ML_TITLE.search(desc):
        s += 1
    s += min(len(COURSE_SIGNAL.findall(title)), 2) * 2
    s += min(len(COURSE_SIGNAL.findall(desc)), 3)
    if re.search(
        r"textbook|reference|reading|literature|literatur|教材|教科書|교재|منابع", desc, re.I
    ):
        s += 2
    if NEGATIVE.search(title) or BAD_URL.search(url):
        s -= 5
    return s


def rank_candidates(u: University, results: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in results:
        url = r.get("url", "")
        if not url or not on_domain(url, u):
            continue
        key = url.rstrip("/").removesuffix("/index.html")
        if key in seen:
            continue
        seen.add(key)
        r = dict(r, score=score(r))
        if r["score"] >= 5:
            out.append(r)
    out.sort(key=lambda r: -r["score"])
    return out[:MAX_CANDIDATES]
