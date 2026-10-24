"""Export the database as small JSON files for the website -> exports/site/.

Files: pipeline, universities, textbooks, courses, materials, quality. Everything is a
query result (measured) except the entries in quality.json that are marked `inferred`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime

from .config import ROOT
from .db import connect, ensure_material_files
from .materials_dl import eligible

OUT = ROOT / "exports" / "site"


def _q(conn, sql: str, subset: str) -> list[dict]:
    cur = conn.execute(sql, {"subset": subset})
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _one(conn, sql: str, subset: str):
    return conn.execute(sql, {"subset": subset}).fetchone()[0]


IN_SUBSET = "%(subset)s = ANY(u.subset_tags)"
OFFER = (
    "course_offerings o JOIN courses c ON c.id=o.course_id "
    "JOIN universities u ON u.id=c.university_id"
)


def universities(conn, subset: str) -> list[dict]:
    return _q(
        conn,
        f"""SELECT u.name, u.country, u.domains[1] AS domain,
               EXISTS (SELECT 1 FROM courses c WHERE c.university_id=u.id) AS ml_course_found,
               EXISTS (SELECT 1 FROM courses c JOIN course_offerings o ON o.course_id=c.id
                       JOIN course_books cb ON cb.offering_id=o.id AND cb.book_id IS NOT NULL
                       WHERE c.university_id=u.id) AS textbook_extracted,
               (SELECT count(*) FROM courses c JOIN course_offerings o ON o.course_id=c.id
                 WHERE c.university_id=u.id) AS n_offerings
            FROM universities u WHERE {IN_SUBSET} ORDER BY u.country, u.name""",
        subset,
    )


def textbooks(conn, subset: str) -> list[dict]:
    books = _q(
        conn,
        "SELECT id, slug, title, authors, year, edition, isbn13, isbn_verified, free_url "
        "FROM books ORDER BY title",
        subset,
    )
    uses = _q(
        conn,
        f"""SELECT cb.book_id, u.name AS university, u.country, c.code AS course,
                   c.title AS course_title, o.term, o.url, cb.role, cb.confidence
            FROM course_books cb JOIN {OFFER} ON o.id=cb.offering_id
            WHERE cb.book_id IS NOT NULL AND {IN_SUBSET}
            ORDER BY u.name, c.code, o.term""",
        subset,
    )
    by_book = defaultdict(list)
    for r in uses:
        by_book[r["book_id"]].append(r)
    out = []
    for b in books:
        us = by_book.get(b["id"], [])
        by_role = {}
        for role in ("textbook", "recommended", "mentioned"):
            sub = [x for x in us if x["role"] == role]
            by_role[role] = {
                "universities": len({x["university"] for x in sub}),
                "offerings": len({x["url"] for x in sub}),
            }
        tb = by_role["textbook"]
        by_role_split = {
            "textbook": tb,
            "other": {
                "universities": len({x["university"] for x in us if x["role"] != "textbook"}),
                "offerings": len({x["url"] for x in us if x["role"] != "textbook"}),
            },
        }
        by_country = defaultdict(lambda: [set(), set()])
        for x in us:
            by_country[x["country"]][0].add(x["university"])
            by_country[x["country"]][1].add(x["url"])
        out.append(
            {
                **{k: v for k, v in b.items() if k != "id"},
                "universities": len({x["university"] for x in us}),
                "offerings": len({x["url"] for x in us}),
                "by_role": by_role_split,
                "by_role_detail": by_role,
                "by_country": {
                    k: {"universities": len(v[0]), "offerings": len(v[1])}
                    for k, v in sorted(by_country.items())
                },
                "used_by": [
                    {
                        "university": x["university"],
                        "course": x["course"] or x["course_title"],
                        "term": x["term"],
                        "role": x["role"],
                        "url": x["url"],
                    }
                    for x in us
                ],
            }
        )
    out.sort(key=lambda b: (-b["universities"], -b["offerings"], b["title"]))
    return out


def courses(conn, subset: str) -> list[dict]:
    rows = _q(
        conn,
        f"""SELECT o.id AS oid, u.name AS university, u.country, c.code, c.title, c.topic,
                   c.level, o.term, o.year, o.url
            FROM {OFFER} WHERE {IN_SUBSET} ORDER BY u.name, c.code, o.term""",
        subset,
    )
    books = defaultdict(list)
    for r in _q(
        conn,
        f"""SELECT cb.offering_id AS oid, b.title, cb.role FROM course_books cb
            JOIN books b ON b.id=cb.book_id JOIN {OFFER} ON o.id=cb.offering_id
            WHERE {IN_SUBSET} ORDER BY b.title""",
        subset,
    ):
        books[r["oid"]].append({"title": r["title"], "role": r["role"]})
    unmatched = {
        r["oid"]: r["n"]
        for r in _q(
            conn,
            f"""SELECT cb.offering_id AS oid, count(*) AS n FROM course_books cb
                JOIN {OFFER} ON o.id=cb.offering_id
                WHERE cb.book_id IS NULL AND {IN_SUBSET} GROUP BY 1""",
            subset,
        )
    }
    mats = defaultdict(dict)
    for r in _q(
        conn,
        f"""SELECT m.offering_id AS oid, m.type, count(*) AS n FROM materials m
            JOIN {OFFER} ON o.id=m.offering_id WHERE {IN_SUBSET} GROUP BY 1,2""",
        subset,
    ):
        mats[r["oid"]][r["type"]] = r["n"]
    out = []
    for r in rows:
        oid = r.pop("oid")
        out.append(
            {
                **r,
                "books": books.get(oid, []),
                "unmatched_citations": unmatched.get(oid, 0),
                "materials": mats.get(oid, {}),
            }
        )
    return out


def materials(conn, subset: str) -> list[dict]:
    return _q(
        conn,
        f"""SELECT u.name AS university, c.code AS course, c.title AS course_title, m.type,
                   m.url, m.anchor_text AS title, o.url AS found_on,
                   f.status AS download_status, f.local_path, f.bytes, f.sha256
            FROM materials m JOIN {OFFER} ON o.id=m.offering_id
            LEFT JOIN material_files f ON f.url=m.url
            WHERE {IN_SUBSET} ORDER BY u.name, c.code, m.type, m.url""",
        subset,
    )


def pipeline(conn, subset: str) -> dict:
    n = lambda sql: _one(conn, sql, subset)  # noqa: E731
    n_off = n(f"SELECT count(*) FROM {OFFER} WHERE {IN_SUBSET}")
    n_courses = n(
        f"SELECT count(DISTINCT c.id) FROM courses c JOIN universities u "
        f"ON u.id=c.university_id WHERE {IN_SUBSET}"
    )
    matched = n(
        f"SELECT count(*) FROM course_books cb JOIN {OFFER} ON o.id=cb.offering_id "
        f"WHERE cb.book_id IS NOT NULL AND {IN_SUBSET}"
    )
    unmatched = n(
        f"SELECT count(*) FROM course_books cb JOIN {OFFER} ON o.id=cb.offering_id "
        f"WHERE cb.book_id IS NULL AND {IN_SUBSET}"
    )
    n_mat = n(
        f"SELECT count(*) FROM materials m JOIN {OFFER} ON o.id=m.offering_id WHERE {IN_SUBSET}"
    )
    urls = conn.execute(
        f"""SELECT DISTINCT m.url, m.type FROM materials m JOIN {OFFER} ON o.id=m.offering_id
            WHERE {IN_SUBSET}""",
        {"subset": subset},
    ).fetchall()
    elig = sum(1 for u, t in urls if eligible(t, u))  # public PDF/PPTX slides + lecture notes
    dl = {
        r["status"]: r
        for r in _q(
            conn,
            f"""SELECT f.status, count(*) AS files, coalesce(sum(f.bytes),0)::bigint AS bytes
                FROM material_files f WHERE f.url IN (SELECT m.url FROM materials m
                  JOIN {OFFER} ON o.id=m.offering_id WHERE {IN_SUBSET}) GROUP BY 1""",
            subset,
        )
    }
    ok = dl.get("ok", {"files": 0, "bytes": 0})
    stages = [
        {
            "id": "universities",
            "label": "Universities in the subset",
            "count": n(f"SELECT count(*) FROM universities u WHERE {IN_SUBSET}"),
            "what": "A configured list of universities (domains from the Hipo university-domains list).",
        },
        {
            "id": "pages_fetched",
            "label": "Pages fetched",
            "count": n(
                f"SELECT count(*) FROM pages p JOIN universities u ON u.id=p.university_id WHERE {IN_SUBSET}"
            ),
            "what": "A site-restricted web search finds candidate pages; each is fetched politely (robots.txt, rate limit, no logins) and snapshotted.",
        },
        {
            "id": "ml_course_pages",
            "label": "ML course pages found",
            "count": n(
                f"SELECT count(*) FROM pages p JOIN universities u ON u.id=p.university_id "
                f"WHERE p.is_ml_course AND {IN_SUBSET}"
            ),
            "what": "A page counts as an ML course if its title names machine learning (or deep/statistical learning, pattern recognition) and it shows at least three course signals (syllabus, lecture, exam, grading...).",
        },
        {
            "id": "offerings",
            "label": "Course offerings / distinct courses",
            "count": n_off,
            "count_secondary": n_courses,
            "what": "Title, code, term, level and instructor are read from each course page; offerings of the same course are merged into one distinct course.",
        },
        {
            "id": "references",
            "label": "References extracted",
            "count": matched + unmatched,
            "breakdown": {"matched_to_catalogue": matched, "unmatched_citations": unmatched},
            "what": "Book mentions are matched to a hand-curated catalogue of canonical textbooks (title, author, ISBN, link); citation-like lines with no match are kept as unmatched.",
        },
        {
            "id": "material_links",
            "label": "Material links found",
            "count": n_mat,
            "what": "Links on course pages are classified as slides, lecture notes, video, homework, solutions or exams.",
        },
        {
            "id": "materials_downloaded",
            "label": "Materials downloaded",
            "count": ok["files"],
            "count_secondary": elig,
            "bytes": ok["bytes"],
            "breakdown": {k: v["files"] for k, v in dl.items()},
            "what": "Public slides and lecture notes (PDF/PPTX only) are downloaded politely: robots.txt, 1 request/s per host, 50 MB per file, no logins; out of the eligible distinct links.",
        },
    ]
    return {"subset": subset, "stages": stages}


def quality(conn, subset: str) -> dict:
    n = lambda sql: _one(conn, sql, subset)  # noqa: E731
    n_off = n(f"SELECT count(*) FROM {OFFER} WHERE {IN_SUBSET}")
    n_courses = n(
        f"SELECT count(*) FROM courses c JOIN universities u ON u.id=c.university_id WHERE {IN_SUBSET}"
    )
    measured = {
        "offerings": n_off,
        "offerings_with_term": n(
            f"SELECT count(*) FROM {OFFER} WHERE o.term IS NOT NULL AND {IN_SUBSET}"
        ),
        "offerings_with_instructor": n(
            f"SELECT count(*) FROM {OFFER} WHERE o.instructor IS NOT NULL AND {IN_SUBSET}"
        ),
        "courses": n_courses,
        "courses_level_unknown": n(
            f"SELECT count(*) FROM courses c JOIN universities u ON u.id=c.university_id "
            f"WHERE c.level='unknown' AND {IN_SUBSET}"
        ),
        "offerings_before_2015": n(
            f"SELECT count(*) FROM {OFFER} WHERE o.year<2015 AND {IN_SUBSET}"
        ),
        "offerings_2015_2020": n(
            f"SELECT count(*) FROM {OFFER} WHERE o.year BETWEEN 2015 AND 2020 AND {IN_SUBSET}"
        ),
        "offerings_2021_on": n(f"SELECT count(*) FROM {OFFER} WHERE o.year>=2021 AND {IN_SUBSET}"),
        "offerings_year_unknown": n(
            f"SELECT count(*) FROM {OFFER} WHERE o.year IS NULL AND {IN_SUBSET}"
        ),
        "offerings_with_matched_book": n(
            f"""SELECT count(DISTINCT o.id) FROM {OFFER} JOIN course_books cb ON cb.offering_id=o.id
                AND cb.book_id IS NOT NULL WHERE {IN_SUBSET}"""
        ),
        "catalogue_books": conn.execute("SELECT count(*) FROM books").fetchone()[0],
    }
    return {
        "subset": subset,
        "note": "Items marked measured are database query results. Items marked inferred are "
        "spot checks and are rough estimates.",
        "measured": measured,
        "caveats": [
            {
                "claim": "Book matches are about 97% precise.",
                "basis": "inferred",
                "detail": "39 of 40 randomly sampled matched rows were correct. The one error (a course title taken for Nielsen's book) is now handled by marking that book 'generic'.",
            },
            {
                "claim": "About 6 of 138 offerings are not course pages.",
                "basis": "inferred",
                "detail": "Study-programme pages and module handbooks (TU Darmstadt, UiO) and one research paper. A MOOC redirect is now rejected.",
            },
            {
                "claim": "Term, instructor and level are heuristic and weaker than titles and books.",
                "basis": "measured",
                "detail": "See measured.offerings_with_term, offerings_with_instructor and courses_level_unknown. The course-code field has occasional junk (a room number, a navigation code).",
            },
            {
                "claim": "Counts are cumulative over two decades, not current.",
                "basis": "measured",
                "detail": "Search surfaces archived offerings; see measured.offerings_before_2015 / 2015_2020 / 2021_on / year_unknown. Filter by year for a current view.",
            },
            {
                "claim": "Coverage is near complete for North America and falls off elsewhere.",
                "basis": "measured",
                "detail": "Mostly a discovery problem: one search index finds few course pages for Iran, China and Japan; Moodle/Canvas/Ed content is behind logins and is not reached by design; some sites are JavaScript apps.",
            },
            {
                "claim": "Aalto's only 'course' is a false positive.",
                "basis": "inferred",
                "detail": "It is a research paper about a syllabus, so Finland is effectively 0 of 2.",
            },
            {
                "claim": "Unmatched citations are catalogue gaps, not errors.",
                "basis": "measured",
                "detail": "They are citation-like lines under a textbook/references heading with no catalogue match (confidence 0.30).",
            },
            {
                "claim": "Catalogue ISBNs are verified, except one.",
                "basis": "measured",
                "detail": "Checked against Open Library; Bach's 'Learning Theory from First Principles' returned 404 and is not confirmed.",
            },
            {
                "claim": "Downloaded materials are only the public PDF/PPTX slides and notes linked from course pages.",
                "basis": "measured",
                "detail": "Video, homework, exams, HTML/Google-Drive links and anything behind a login are not downloaded. Failures are listed per reason in pipeline.json.",
            },
        ],
    }


def run(subset: str = "pilot") -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        ensure_material_files(conn)
        files = {
            "pipeline.json": pipeline(conn, subset),
            "universities.json": universities(conn, subset),
            "textbooks.json": textbooks(conn, subset),
            "courses.json": courses(conn, subset),
            "materials.json": materials(conn, subset),
            "quality.json": quality(conn, subset),
        }
    meta = {"subset": subset, "generated_at": datetime.now(UTC).isoformat(timespec="seconds")}
    sizes = {}
    for name, data in files.items():
        if isinstance(data, dict):
            data = {**meta, **data}
        else:
            data = {**meta, "items": data}
        p = OUT / name
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n")
        sizes[name] = p.stat().st_size
    sizes["total"] = sum(sizes.values())
    return sizes
