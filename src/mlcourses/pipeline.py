"""Per-university pipeline: search -> rank -> fetch -> extract -> follow -> JSON.

Output: cache/extracted/<domain>.json (one per university) and cache/logs/<domain>.jsonl
(audit log). Re-running with offline=True replays everything from the cache, so extraction
rules can be improved without touching the network or spending search credits.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from .config import CACHE, MAX_FOLLOW, University
from .discover import fallback_queries, queries_for, rank_candidates
from .extract import build_offering, follow_links, merge_followed
from .http import Fetcher, now_iso


def process(u: University, offline: bool = False) -> dict:
    log = CACHE / "logs" / f"{u.key}.jsonl"
    if not offline and log.exists():
        log.unlink()  # fresh audit log per online run; the cache keeps earlier artefacts
    f = Fetcher(log, offline=offline)
    results = []
    for q, country in queries_for(u):
        results += f.search(q, limit=10, country=country)
    if len(results) < 3:
        for q, country in fallback_queries(u):
            results += f.search(q, limit=10, country=country)
    cands = rank_candidates(u, results)
    pages, offerings = [], []
    seen_urls = set()
    for c in cands:
        page = f.fetch(c["url"])
        if page is None:
            continue
        pages.append(
            {
                "url": page.url,
                "fetched_at": page.fetched_at,
                "method": page.method,
                "content_type": page.content_type,
                "sha1": page.sha1,
                "cache_path": page.cache_path,
                "n_chars": len(page.text),
                "is_ml_course": False,
            }
        )
        key = page.url.rstrip("/").removesuffix("/index.html")
        if key in seen_urls:
            continue
        seen_urls.add(key)
        off = build_offering(page.url, page.text, page.links, c.get("title", ""))
        if off is None:
            continue
        pages[-1]["is_ml_course"] = True
        dup = next(
            (o for o in offerings if o["code"] and o["code"] == off.code and o["term"] == off.term),
            None,
        )
        for sub in follow_links(page.links, page.url, MAX_FOLLOW):
            sp = f.fetch(sub)
            if sp is None:
                continue
            f.log("follow", "ok", sub, detail=f"from {page.url}")
            pages.append(
                {
                    "url": sp.url,
                    "fetched_at": sp.fetched_at,
                    "method": sp.method,
                    "content_type": sp.content_type,
                    "sha1": sp.sha1,
                    "cache_path": sp.cache_path,
                    "n_chars": len(sp.text),
                    "is_ml_course": False,
                }
            )
            merge_followed(off, sp.url, sp.text, sp.links)
        if dup is not None:  # same course + term under another URL: merge as extra source
            have = {b["slug"] for b in dup["books"] if b["slug"]}
            dup["books"] += [asdict(b) for b in off.books if b.slug is None or b.slug not in have]
            known = {m["url"] for m in dup["materials"]}
            dup["materials"] += [asdict(m) for m in off.materials if m.url not in known]
            dup["extra_sources"].append(off.url)
            continue
        offerings.append(asdict(off) | {"fetched_at": page.fetched_at})
    out = {
        "university": asdict(u),
        "processed_at": now_iso(),
        "n_search_results": len(results),
        "candidates": cands,
        "pages": pages,
        "offerings": offerings,
    }
    p = CACHE / "extracted" / f"{u.key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def run(unis: list[University], offline: bool = False, workers: int = 4) -> list[dict]:
    def one(u: University) -> dict:
        try:
            r = process(u, offline=offline)
            print(
                f"  {u.key:22s} results={r['n_search_results']:3d} cands="
                f"{len(r['candidates'])} offerings={len(r['offerings'])} "
                f"books={sum(1 for o in r['offerings'] for b in o['books'] if b['slug'])}",
                flush=True,
            )
            return r
        except Exception as e:  # noqa: BLE001 - one bad site must not stop the crawl
            print(f"  {u.key:22s} FAILED: {e!r}", flush=True)
            return {
                "university": asdict(u),
                "error": repr(e),
                "offerings": [],
                "pages": [],
                "candidates": [],
            }

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, unis))
