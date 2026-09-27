"""Check catalogue ISBNs against Open Library (read-only, one request per book, 1 req/s)."""

from __future__ import annotations

import re
import time

import httpx

from .books import catalogue
from .config import USER_AGENT


def _words(s: str) -> set[str]:
    """Latin words of 4+ letters, plus CJK runs of 2+ characters."""
    return set(re.findall(r"[a-z]{4,}", s.lower())) | set(re.findall(r"[\u4e00-\u9fff]{2,}", s))


def verify() -> int:
    bad = 0
    results = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True) as c:
        for b in catalogue():
            if not b.isbn13:
                continue
            time.sleep(1.0)
            r = c.get(f"https://openlibrary.org/isbn/{b.isbn13}.json")
            if r.status_code != 200:
                status, got = f"not found ({r.status_code})", ""
            else:
                got = r.json().get("title", "")
                wt, wg = _words(b.title), _words(got)
                ok = bool(wg & wt) or any(a in g or g in a for a in wt for g in wg)
                status = "ok" if ok else "TITLE MISMATCH"
            results[b.slug] = status == "ok"
            bad += status != "ok"
            print(f"{b.slug:22s} {b.isbn13}  {status:18s} {got[:60]}")
    try:
        from .db import connect

        with connect() as conn:
            for slug, ok in results.items():
                conn.execute("UPDATE books SET isbn_verified=%s WHERE slug=%s", (ok, slug))
    except Exception as e:  # noqa: BLE001 - db optional here
        print(f"(db not updated: {e})")
    print(f"{bad} ISBNs not verified")
    return 0
