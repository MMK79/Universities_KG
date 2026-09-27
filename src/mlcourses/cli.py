"""Command line: mlcourses {schema,crawl,extract,load,report,verify-isbns,list}."""

from __future__ import annotations

import argparse
import sys

from . import pipeline
from .config import load_subset


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mlcourses")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("schema", help="drop + recreate all tables")
    for name in ("crawl", "extract", "load", "list"):
        p = sp.add_parser(name)
        p.add_argument("--subset", default="pilot")
        p.add_argument("--only", nargs="*", help="restrict to these domains")
        if name == "crawl":
            p.add_argument("--workers", type=int, default=4)
    r = sp.add_parser("report")
    r.add_argument("--subset", default="pilot")
    sp.add_parser("verify-isbns")
    a = ap.parse_args(argv)

    if a.cmd == "schema":
        from .db import apply_schema, connect

        with connect() as c:
            apply_schema(c)
        print("schema applied")
        return 0
    if a.cmd == "report":
        from .report import run

        print(run(a.subset))
        return 0
    if a.cmd == "verify-isbns":
        from .isbn import verify

        return verify()

    unis = load_subset(a.subset)
    if a.only:
        unis = [u for u in unis if u.key in set(a.only)]
    if a.cmd == "list":
        for u in unis:
            print(f"{u.key:24s} {u.country:20s} {u.name}")
        print(f"{len(unis)} universities")
    elif a.cmd == "crawl":
        print(f"crawling {len(unis)} universities (subset={a.subset})")
        pipeline.run(unis, offline=False, workers=a.workers)
    elif a.cmd == "extract":
        pipeline.run(unis, offline=True, workers=8)
    elif a.cmd == "load":
        from .db import load

        print(load(unis, a.subset))
    return 0


if __name__ == "__main__":
    sys.exit(main())
