"""Load the extracted JSON into Postgres. A load is a full, deterministic rebuild of the
derived tables for the loaded universities, so the database always equals cache + rules."""

from __future__ import annotations

import json
import re
from pathlib import Path

import psycopg

from .books import catalogue
from .config import CACHE, DATABASE_URL, ROOT, University


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)


# Phase-2 download results. Deliberately NOT tied to `materials` by a foreign key and not
# dropped by schema.sql: `load` rebuilds `materials`, but downloaded files must survive it.
# Keyed by URL; join to `materials` on url. Mirrors the DDL note at the end of schema.sql.
MATERIAL_FILES_DDL = """
CREATE TABLE IF NOT EXISTS material_files (
    url          text PRIMARY KEY,
    status       text NOT NULL,   -- ok | skipped-robots | too-large | http-error |
                                  -- not-document | skipped-login
    local_path   text,            -- relative to the materials root
    bytes        bigint,
    sha256       text,
    content_type text,
    http_status  int,
    detail       text,
    fetched_at   timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS material_files_sha ON material_files (sha256);
"""


def ensure_material_files(conn: psycopg.Connection) -> None:
    conn.execute(MATERIAL_FILES_DDL)
    conn.commit()


def apply_schema(conn: psycopg.Connection) -> None:
    conn.execute((ROOT / "db" / "schema.sql").read_text())
    conn.execute(MATERIAL_FILES_DDL)  # IF NOT EXISTS: keeps downloaded-file records
    conn.commit()


def course_key(code: str | None, title: str) -> str:
    if code:
        return "code:" + re.sub(r"[\s-]", "", code).upper()
    t = re.sub(r"\b(spring|fall|autumn|winter|summer|ws|ss)\b|\d{4}|[^\w\s]", " ", title.lower())
    return "title:" + " ".join(t.split())[:120]


def seed_books(conn: psycopg.Connection) -> dict[str, int]:
    ids = {}
    for b in catalogue():
        row = conn.execute(
            """INSERT INTO books (slug, title, authors, year, edition, publisher, isbn13, free_url)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (slug) DO UPDATE SET title=EXCLUDED.title, authors=EXCLUDED.authors,
                 year=EXCLUDED.year, edition=EXCLUDED.edition, publisher=EXCLUDED.publisher,
                 isbn13=EXCLUDED.isbn13
               RETURNING id""",
            (b.slug, b.title, b.authors, b.year, b.edition, b.publisher, b.isbn13, b.free_url),
        ).fetchone()
        ids[b.slug] = row[0]
    return ids


def load(unis: list[University], subset: str) -> dict:
    stats = {"universities": 0, "offerings": 0, "course_books": 0, "materials": 0}
    with connect() as conn:
        book_ids = seed_books(conn)
        for u in unis:
            p = CACHE / "extracted" / f"{u.key}.json"
            if not p.exists():
                continue
            data = json.loads(p.read_text())
            uid = conn.execute(
                """INSERT INTO universities (name, country, alpha_two, domains, web_pages,
                                             subset_tags)
                   VALUES (%s,%s,%s,%s,%s,ARRAY[%s])
                   ON CONFLICT (name, country) DO UPDATE SET domains=EXCLUDED.domains,
                     subset_tags=(SELECT array_agg(DISTINCT x) FROM unnest(
                        universities.subset_tags || EXCLUDED.subset_tags) x)
                   RETURNING id""",
                (u.name, u.country, u.alpha_two, u.domains, u.web_pages, subset),
            ).fetchone()[0]
            stats["universities"] += 1
            # rebuild derived rows for this university
            conn.execute("DELETE FROM courses WHERE university_id=%s", (uid,))
            conn.execute("DELETE FROM pages WHERE university_id=%s", (uid,))
            conn.execute("DELETE FROM crawl_log WHERE university_id=%s", (uid,))
            _load_log(conn, uid, CACHE / "logs" / f"{u.key}.jsonl")
            for pg in data["pages"]:
                conn.execute(
                    """INSERT INTO pages (university_id, url, fetched_at, fetch_method,
                         content_type, sha1, cache_path, n_chars, is_ml_course)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (url) DO NOTHING""",
                    (
                        uid,
                        pg["url"],
                        pg["fetched_at"],
                        pg["method"],
                        pg["content_type"],
                        pg["sha1"],
                        pg["cache_path"],
                        pg["n_chars"],
                        pg["is_ml_course"],
                    ),
                )
            for o in data["offerings"]:
                _load_offering(conn, uid, o, book_ids, stats)
        conn.commit()
    return stats


def _load_log(conn, uid: int, path: Path) -> None:
    if not path.exists():
        return
    with conn.cursor() as cur:
        rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
        cur.executemany(
            """INSERT INTO crawl_log (university_id, at, kind, url, query, status, detail)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            [
                (uid, r["at"], r["kind"], r["url"], r["query"], r["status"], r["detail"])
                for r in rows
            ],
        )


def _load_offering(conn, uid: int, o: dict, book_ids: dict[str, int], stats: dict) -> None:
    key = course_key(o["code"], o["title"])
    cid = conn.execute(
        """INSERT INTO courses (university_id, code, title, topic, level, department, course_key)
           VALUES (%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (university_id, course_key) DO UPDATE SET
             topic = CASE WHEN courses.topic='ml-core' THEN 'ml-core' ELSE EXCLUDED.topic END
           RETURNING id""",
        (uid, o["code"], o["title"], o["topic"], o["level"], o["department"], key),
    ).fetchone()[0]
    row = conn.execute(
        """INSERT INTO course_offerings (course_id, term, year, instructor, url, source_url,
                                         fetched_at, snippet, method)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'heuristic') ON CONFLICT (url) DO NOTHING
           RETURNING id""",
        (
            cid,
            o["term"],
            o["year"],
            o["instructor"],
            o["url"],
            o["url"],
            o["fetched_at"],
            o["snippet"],
        ),
    ).fetchone()
    if row is None:
        return
    oid = row[0]
    stats["offerings"] += 1
    for b in o["books"]:
        bid = book_ids.get(b["slug"]) if b["slug"] else None
        r = conn.execute(
            """INSERT INTO course_books (offering_id, book_id, role, raw_citation, confidence,
                                         method, source_url, fetched_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
            (
                oid,
                bid,
                b["role"],
                b["raw_citation"],
                b["confidence"],
                b["method"],
                b.get("source_url") or o["url"],
                o["fetched_at"],
            ),
        ).fetchone()
        stats["course_books"] += r is not None
    for m in o["materials"]:
        r = conn.execute(
            """INSERT INTO materials (offering_id, type, url, anchor_text, found_at, fetched_at)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
            (oid, m["type"], m["url"], m["anchor"], m["found_at"], o["fetched_at"]),
        ).fetchone()
        stats["materials"] += r is not None
