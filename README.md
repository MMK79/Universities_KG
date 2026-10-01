# Universities — ML course & textbook database

A database of university **Machine Learning courses** and the **textbooks** they use, with
the course materials (slides, videos, homework, solutions, exams) as a second layer. Every
fact keeps its provenance: source URL, fetch date and the raw text it came from.

- Spec (scope, schema, pipeline, legal/ethics): [`docs/spec.md`](docs/spec.md)
- Pilot findings: [`docs/findings.md`](docs/findings.md) · generated tables:
  [`docs/findings-data.md`](docs/findings-data.md)

## Quick start

```bash
uv sync
make up                    # Postgres 16 in docker, host port 5434, schema auto-applied
make crawl SUBSET=pilot    # search + fetch + extract + load (needs FIRECRAWL_API_KEY)
make report                # regenerate docs/findings-data.md from the database
make download-materials    # phase 2: polite, resumable download of public PDF/PPTX slides + notes
make export-site           # write JSON for the website to exports/site/
make test                  # pytest (DB test skipped if Postgres is down)
make lint                  # ruff
make psql                  # open a SQL shell
```

`make crawl` loads `FIRECRAWL_API_KEY` from the vault's `.env`
(`FIRECRAWL_ENV=/path/to/.env make crawl` to override). Everything fetched is cached in
`cache/` (gitignored): rerunning is free, and `make extract && make load` replays the
extraction from the cache without touching the network.

Subsets live in [`config/subsets.toml`](config/subsets.toml): `pilot` (43 universities),
`smoke` (3), `us_top50`, `countries_core` (every university in the target countries).
List one with `uv run mlcourses list --subset pilot`; crawl a few with
`uv run mlcourses crawl --subset pilot --only mit.edu tum.de`.

## Useful queries

```sql
SELECT * FROM book_adoption LIMIT 15;                          -- textbook adoption
SELECT u.name, c.code, c.title, o.term, o.url                  -- courses per university
  FROM course_offerings o JOIN courses c ON c.id=o.course_id
  JOIN universities u ON u.id=c.university_id ORDER BY 1;
SELECT raw_citation, source_url FROM course_books              -- catalogue gaps
  WHERE book_id IS NULL;
SELECT type, count(*) FROM materials GROUP BY 1;               -- material links
SELECT status, count(*), sum(bytes) FROM material_files GROUP BY 1;  -- downloaded files
```

## Layout

| Path | What |
|---|---|
| `src/mlcourses/` | the pipeline (`discover`, `http` fetcher, `extract`, `books` matcher, `db`, `report`, `cli`) |
| `src/mlcourses/data/books.toml` | canonical textbook catalogue (patterns, ISBN-13, checked against Open Library) |
| `config/subsets.toml` | which universities to crawl; per-domain overrides |
| `db/schema.sql` | Postgres schema |
| `src/mlcourses/materials_dl.py` | phase-2 downloader (robots, 1 req/s/host, 50 MB/file, 6 GB total, resumable) |
| `src/mlcourses/export_site.py`, `exports/site/` | JSON export for the website: pipeline, universities, textbooks, courses, materials, quality |
| `main.py`, `produced_files/` | the original open-courseware link scrapers (paperhelp / learningplatforms lists) — kept as is |
| `world_universities_and_domains.json`, `university-domains-list-api/` | Hipo university/domain list, the university source |

## Etiquette

robots.txt is honoured for every URL (also before handing a page to Firecrawl), ≥3 s per
host for pages (≥1 s per host for file downloads), honest User-Agent, no logins. See `docs/spec.md` §5.

Downloaded files are stored outside the repo, in
`/Volumes/MigMig/Personal/Research/ML Course Materials/<country>/<university>/<course-code>/`;
`material_files` in the database records status, path, size and sha256 for each URL.
`make download-materials` skips what is already done and retries only HTTP errors.

## Sources

- Open courseware collections: https://www.paperhelp.org/blog/top-50-university-open-courseware-collections.html
- University domains: https://github.com/Hipo/university-domains-list.git
