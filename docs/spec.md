# Universities ML Course Database — spec

Status: phase 1 (pilot) implemented 2026-09-27. Owner: Masoud. Code: `src/mlcourses/`.

## 1. Purpose

Build *my own* database of university **Machine Learning courses** and, above all, the
**textbooks** those courses assign, normalised to canonical books so adoption can be counted
across universities. Secondary (phase 2): the published course **materials** (slides, notes,
videos, homework, solutions, exams).

Why: it feeds the MSc thesis (a knowledge graph built from ML course material). Knowing which
textbooks ML courses actually use tells the thesis which books belong in the corpus.

## 2. Scope

| Item | Phase 1 (done) | Phase 2 |
|---|---|---|
| Universities | configurable subsets in `config/subsets.toml`; pilot = 43 universities | `us_top50`, `countries_core` (all universities in IR, DE, NL, NO, FI, CA, HK, KR, JP, CN) |
| Course | code, title, topic (`ml-core` / `ml-related`), level, department | catalogue/module-handbook pages as a second source |
| Offering | term, year, instructor, URL | more terms per course (archives) |
| Books | canonical catalogue (44 books), regex + URL + ISBN matching, raw citation, role, confidence | LLM pass for messy syllabi (`method='llm'`), catalogue growth from `regex-unmatched` rows |
| Materials | links classified into 7 types, stored with the page they were found on | verification (HEAD request), YouTube playlist/channel expansion |

The subset used by the university source list comes from the user's
`world_universities_and_domains.json` (Hipo *university-domains-list*, 10,191 entries).
Subsets pick by exact domain, by country, or add `extra` entries. `overrides` fix a search
domain when the dataset is stale (`sharif.ac.ir` → `sharif.edu`, `uw.edu` → `washington.edu`).

"ML course" = a page whose title/opening names machine learning (or deep/statistical learning,
pattern recognition, in 9 languages) **and** has at least three distinct course signals
(syllabus, lecture, homework, exam, grading, instructor, ECTS, Vorlesung, 课程, …).

## 3. Schema (Postgres 16, `db/schema.sql`)

```
universities 1─* courses 1─* course_offerings 1─* course_books *─1 books
                                         └────* materials
universities 1─* pages      (snapshot index: url, fetched_at, sha1, cache_path)
universities 1─* crawl_log  (every search / fetch / robots decision)
view book_adoption          (universities + offerings per canonical book)
```

Provenance rule: every fact row carries `source_url` (or `found_at`), `fetched_at`, the raw
text it was read from (`snippet` / `raw_citation` / `anchor_text`) and a `method`
(`regex`, `regex-unmatched`, `heuristic`, `llm` reserved, `manual`). The raw bytes of every
fetched page live in `cache/raw/` (gitignored) and are indexed by `pages.cache_path`.

`course_books.confidence`:

| value | rule |
|---|---|
| 0.95 | link to the book's canonical URL (with author near the anchor) or its ISBN on the page |
| 0.90 | distinctive title + author surname in the citation window; or bare canonical-URL link |
| 0.75 | distinctive title alone |
| 0.65 | generic title (*Deep Learning*, *Machine Learning*, *Pattern Classification*) + author |
| 0.30 | `regex-unmatched`: citation-like line under a textbook/references heading, no catalogue match (`book_id` NULL) |

`course_books.role`: `textbook` (under a "required/textbook" heading), `recommended`
("recommended/optional/further/references/literature"), `mentioned` (elsewhere, e.g. schedule
readings).

## 4. Pipeline

```
subset ─► discover (Firecrawl search, site:<domain>, EN + local-language query, fallback)
       ─► rank candidates (on-domain, ML in title, course signals, negative words) top 6
       ─► fetch (robots.txt check → httpx; Firecrawl render only if page text < 400 chars)
       ─► extract offering (title, code, term, instructor, level, dept) — heuristic
       ─► follow ≤3 same-course-site links (syllabus / info / readings / literature)
       ─► match books + unmatched citations + classify material links
       ─► cache/extracted/<domain>.json ─► load (full rebuild per university) ─► Postgres
```

* `make crawl SUBSET=pilot` — network run (search results and pages are cached; rerunning
  costs nothing for what is already cached).
* `make extract` — re-run extraction from cache only (no network); iterate on rules freely.
* `make load`, `make report` (→ `docs/findings-data.md`), `make verify-isbns`
  (Open Library check of the catalogue, sets `books.isbn_verified`).

## 5. Legal and ethics

* **robots.txt** is checked for every URL with our own UA before *any* fetch, including
  before handing a URL to Firecrawl for rendering. Disallowed URLs are logged
  (`crawl_log.status='robots_disallowed'`) and skipped.
* **Rate limit**: ≥ 3 s between requests to the same host; Firecrawl calls are serialised
  with ≥ 4 s spacing and back off on 429.
* **Identification**: UA `MLCourseSurvey/0.1 (academic survey …; +https://github.com/MMK79/Universities_KG)`.
* **No login-walled content**: no cookies, no credentials; Moodle/Canvas/LMS pages that
  require login are simply not reached (a known coverage gap, see findings).
* **Scale**: ≤ 6 course pages + ≤ 3 follow-ups per university per run — this is a survey,
  not a mirror. Raw snapshots stay local (gitignored) for provenance and are not republished.
* Only bibliographic facts (which book a course uses) and URLs are stored in the database;
  course content itself is not redistributed.

## 6. Known limits (phase 1)

* Discovery depends on one search index (Firecrawl search). Sites it indexes thinly
  (Iran, China, Japan, Korea in the pilot) yield few or no candidates. See findings.
* Course metadata is heuristic; `code` and `level` are the weakest fields.
* A course page that only says "see Moodle" has no extractable books.
* The catalogue is hand-curated; books outside it appear only as `regex-unmatched` rows.
