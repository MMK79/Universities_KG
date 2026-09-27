-- Universities ML course database — schema v1 (2026-09-27).
-- Every extracted fact carries provenance: source_url + fetched_at + snippet (+ method).
-- method: 'regex' (deterministic first pass), 'heuristic', 'llm' (reserved), 'manual'.

DROP TABLE IF EXISTS materials, course_books, course_offerings, courses, books,
    pages, crawl_log, universities CASCADE;

CREATE TABLE universities (
    id            serial PRIMARY KEY,
    name          text NOT NULL,
    country       text NOT NULL,
    alpha_two     text,
    domains       text[] NOT NULL,
    web_pages     text[],
    subset_tags   text[] NOT NULL DEFAULT '{}',   -- which configured subsets include it
    UNIQUE (name, country)
);

-- One row per fetch/search/robots decision. Append-only audit trail.
CREATE TABLE crawl_log (
    id            bigserial PRIMARY KEY,
    university_id int REFERENCES universities(id) ON DELETE CASCADE,
    at            timestamptz NOT NULL,
    kind          text NOT NULL,        -- search | fetch | robots | firecrawl_scrape | follow
    url           text,
    query         text,
    status        text NOT NULL,        -- ok | http_<code> | robots_disallowed | error | cached
    detail        text
);

-- Fetched page snapshots (the raw bytes live in the gitignored cache/).
CREATE TABLE pages (
    id            bigserial PRIMARY KEY,
    university_id int REFERENCES universities(id) ON DELETE CASCADE,
    url           text NOT NULL UNIQUE,
    fetched_at    timestamptz NOT NULL,
    fetch_method  text NOT NULL,        -- httpx | firecrawl
    content_type  text,
    sha1          text,
    cache_path    text,
    n_chars       int,
    is_ml_course  boolean NOT NULL DEFAULT false
);

CREATE TABLE courses (
    id            serial PRIMARY KEY,
    university_id int NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
    code          text,                 -- e.g. CS229, 10-601, IN4050
    title         text NOT NULL,
    topic         text NOT NULL,        -- ml-core | ml-related (deep/statistical learning, PR)
    level         text,                 -- undergrad | grad | mixed | unknown
    department    text,
    course_key    text NOT NULL,        -- normalised code or title, for dedup
    UNIQUE (university_id, course_key)
);

CREATE TABLE course_offerings (
    id            serial PRIMARY KEY,
    course_id     int NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    term          text,                 -- 'Fall 2024', 'WS 2023/24'
    year          int,
    instructor    text,
    url           text NOT NULL UNIQUE,
    source_url    text NOT NULL,
    fetched_at    timestamptz NOT NULL,
    snippet       text,                 -- raw text the fields were read from
    method        text NOT NULL DEFAULT 'heuristic'
);

-- Canonical books (seeded from src/mlcourses/data/books.toml).
CREATE TABLE books (
    id            serial PRIMARY KEY,
    slug          text NOT NULL UNIQUE, -- bishop-prml
    title         text NOT NULL,
    authors       text NOT NULL,
    year          int,
    edition       text,
    publisher     text,
    isbn13        text,
    isbn_verified boolean,              -- checked against Open Library (NULL = not checked)
    free_url      text
);

CREATE TABLE course_books (
    id            bigserial PRIMARY KEY,
    offering_id   int NOT NULL REFERENCES course_offerings(id) ON DELETE CASCADE,
    book_id       int REFERENCES books(id),  -- NULL = unmatched citation (catalogue gap)
    role          text,                 -- textbook | recommended | mentioned
    raw_citation  text NOT NULL,
    confidence    real NOT NULL,        -- 0..1
    method        text NOT NULL,        -- regex | regex-unmatched | llm | manual
    source_url    text NOT NULL,
    fetched_at    timestamptz NOT NULL,
    UNIQUE (offering_id, book_id, source_url)
);

CREATE TABLE materials (
    id            bigserial PRIMARY KEY,
    offering_id   int NOT NULL REFERENCES course_offerings(id) ON DELETE CASCADE,
    type          text NOT NULL,        -- slides | lecture_notes | video | homework |
                                        -- homework_solution | exam | exam_solution
    url           text NOT NULL,
    anchor_text   text,
    found_at      text NOT NULL,        -- page URL the link was found on
    fetched_at    timestamptz NOT NULL,
    method        text NOT NULL DEFAULT 'heuristic',
    UNIQUE (offering_id, url)
);

CREATE INDEX ON course_books (book_id);
CREATE INDEX ON materials (type);
CREATE INDEX ON crawl_log (university_id, kind);

-- Adoption view: how many distinct universities / offerings use each canonical book.
CREATE VIEW book_adoption AS
SELECT b.slug, b.title, b.authors,
       count(DISTINCT c.university_id) AS universities,
       count(DISTINCT cb.offering_id)  AS offerings
FROM books b
JOIN course_books cb ON cb.book_id = b.id
JOIN course_offerings o ON o.id = cb.offering_id
JOIN courses c ON c.id = o.course_id
GROUP BY b.id
ORDER BY universities DESC, offerings DESC;
