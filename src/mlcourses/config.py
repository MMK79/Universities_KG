"""Paths, crawl etiquette settings and subset selection."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache"
SUBSETS_FILE = ROOT / "config" / "subsets.toml"
UNIVERSITIES_JSON = ROOT / "world_universities_and_domains.json"
BOOKS_TOML = Path(__file__).parent / "data" / "books.toml"

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://mlcourses:mlcourses@localhost:5434/mlcourses"
)

# Identify ourselves honestly; the project URL explains what we do.
USER_AGENT = (
    "MLCourseSurvey/0.1 (academic survey of ML course textbooks; "
    "+https://github.com/MMK79/Universities_KG)"
)
MIN_DELAY_PER_HOST = 3.0  # seconds between requests to the same host
REQUEST_TIMEOUT = 30.0
MAX_CANDIDATES = 6  # course pages fetched per university (after ranking)
MAX_FOLLOW = 3  # 1-hop same-host links (syllabus/info/readings) per course page


@dataclass
class University:
    name: str
    country: str
    alpha_two: str | None
    domains: list[str]
    web_pages: list[str] = field(default_factory=list)
    search_domain: str = ""

    @property
    def key(self) -> str:
        return self.domains[0]

    def __post_init__(self) -> None:
        if not self.search_domain:
            self.search_domain = self.domains[0]


def load_subset(name: str) -> list[University]:
    cfg = tomllib.loads(SUBSETS_FILE.read_text())
    if name not in cfg["subsets"]:
        raise SystemExit(f"unknown subset {name!r}; have {sorted(cfg['subsets'])}")
    sub = cfg["subsets"][name]
    overrides = cfg.get("overrides", {})
    data = json.loads(UNIVERSITIES_JSON.read_text())
    by_domain = {}
    for rec in data:
        for d in rec["domains"]:
            by_domain.setdefault(d, rec)
    picked: list[dict] = []
    for d in sub.get("domains", []):
        if d not in by_domain:
            raise SystemExit(f"domain {d} not in {UNIVERSITIES_JSON.name}; use `extra`")
        picked.append(by_domain[d])
    countries = set(sub.get("countries", []))
    if countries:
        picked += [r for r in data if r["country"] in countries]
    picked += sub.get("extra", [])
    seen, out = set(), []
    for r in picked:
        k = (r["name"], r["country"])
        if k in seen:
            continue
        seen.add(k)
        u = University(
            name=r["name"],
            country=r["country"],
            alpha_two=r.get("alpha_two_code"),
            domains=list(r["domains"]),
            web_pages=list(r.get("web_pages") or []),
        )
        ov = overrides.get(u.key, {})
        if ov.get("search_domain"):
            u.search_domain = ov["search_domain"]
        out.append(u)
    return out
