# Pilot findings — ML courses & textbooks (2026-09-27)

Subset `pilot`: 43 universities (14 US, 4 CA, 5 DE, 3 NL, 2 NO, 2 FI, 3 HK, 2 KR, 2 JP,
2 CN, 4 IR). Crawled 2026-09-27 with the phase-1 pipeline (`docs/spec.md`). Database:
Postgres (docker, `make up`). The full generated tables are in
[`findings-data.md`](findings-data.md). Regenerate with `make report`.

**What is measured and what is inferred.** Every count below is a database query result
(measured). The precision figures come from one person looking over a random sample
(inferred, one rater, no second check). Treat them as rough estimates.

## Headline numbers

| Metric | Value |
|---|---|
| Universities with ≥1 ML course page found | **33 / 43 (77%)**. 32 have a course with "machine learning" in the title |
| Universities with ≥1 catalogue textbook extracted | **27 / 43 (63%)** |
| Universities with any reference extracted (incl. unmatched citations) | 30 / 43 (70%) |
| Course offerings found / distinct courses | 138 / 109 |
| Offerings with ≥1 matched textbook | 84 / 138 (61%) |
| Book mentions: matched to catalogue / unmatched citation lines | 303 / 129 |
| Material links (slides, video, homework, solutions, exams) | 1,408 |
| Pages fetched and snapshotted | 259 (plus one-hop follow-ups, logged) |

## Top textbooks by adoption (distinct universities, any role)

| # | Book | Universities | Offerings |
|---|---|---|---|
| 1 | Hastie, Tibshirani, Friedman — *The Elements of Statistical Learning* | 21 | 44 |
| 2 | Bishop — *Pattern Recognition and Machine Learning* | 19 | 32 |
| 3 | Murphy — *Machine Learning: A Probabilistic Perspective* | 16 | 28 |
| 4 | Goodfellow, Bengio, Courville — *Deep Learning* | 14 | 25 |
| 5 | James, Witten, Hastie, Tibshirani — *An Introduction to Statistical Learning* | 11 | 20 |
| 6 | Mitchell — *Machine Learning* | 10 | 18 |
| 7 | Sutton & Barto — *Reinforcement Learning: An Introduction* | 8 | 10 |
| 8 | Shalev-Shwartz & Ben-David — *Understanding Machine Learning* | 7 | 10 |
| 8 | Duda, Hart, Stork — *Pattern Classification* | 7 | 9 |
| 8 | MacKay — *Information Theory, Inference, and Learning Algorithms* | 7 | 8 |

If we count only mentions under a "required / textbook" heading (`role='textbook'`, 85 rows),
the order changes to Murphy MLAPP (8 universities), ESL (7), PRML (7), Goodfellow (5),
Mitchell (5). The same three books lead either way. The newer Murphy *PML: An
Introduction* (6 universities) is gaining on MLAPP.

38 of the 44 catalogue books were seen at least once. All catalogue ISBNs except one were
confirmed against Open Library (`make verify-isbns`). Bach's *Learning Theory from First
Principles* returned 404 there, so it is not confirmed.

## Coverage by country

| Country | Universities | ML course found | Textbook extracted |
|---|---|---|---|
| United States | 14 | 14 | 14 |
| Canada | 4 | 4 | 4 |
| Hong Kong | 3 | 3 | 2 |
| Korea | 2 | 2 | 2 |
| Germany | 5 | 3 | 3 |
| Norway | 2 | 2 | 1 |
| Japan | 2 | 2 | 0 |
| China | 2 | 1 | 1 |
| Netherlands | 3 | 1 | 0 |
| Finland | 2 | 1* | 0 |
| Iran | 4 | 0 | 0 |

\* The only Aalto "course" is a research paper about a syllabus, a false positive. In
practice Finland is 0/2.

Coverage is close to complete for North America and falls off sharply elsewhere. The
causes are in the failure modes below, and it is mostly a *discovery* problem, not an
extraction problem.

## Precision (spot checks, one rater, unverified)

- **Book matches**: from a random sample of 40 matched rows, 39 were correct. The one
  error was the course title "Neural Networks and Deep Learning" (Toronto CSC413) being
  taken for Nielsen's book. That book is now marked `generic`.
- **Offerings**: about 6 of 138 are not course pages (study-programme pages and module
  handbooks at TU Darmstadt and UiO, one research paper). A MOOC redirect (KAIST →
  Coursera) is now rejected.
- **Metadata** is weaker. Terms were found for 89/138 offerings and instructors for
  49/138. Level is `unknown` for 24/109 courses. The code field has occasional junk, such
  as a room number or a navigation code.

## Failure modes

1. **Search-index coverage (the main gap).** One search index finds few course pages for
   Iranian, Chinese and Japanese sites. For Iran, the dataset domain for Sharif is out of
   date (`sharif.ac.ir` vs `sharif.edu`, now overridden). AUT and UT searches return
   journal articles, and IUST returns 4 results in total. Course pages there often live
   on personal pages, GitHub or Telegram rather than the university domain.
2. **Login-walled or LMS content.** Current Stanford CS229, TUM and many European courses
   point to Ed, Canvas or Moodle for materials and readings. By design we do not log in.
3. **JavaScript single-page apps.** Helsinki's `studies.helsinki.fi` serves 22 characters
   of HTML, and the Firecrawl renderer returned HTTP 500 on three attempts.
4. **Catalogue pages without books.** NTNU and UiO course catalogue pages ("emne",
   "pensum") and Japanese syllabus catalogues often say "announced at semester start"
   rather than list books.
5. **Historical bias.** Search surfaces archived offerings. Offering years are 14 before
   2015, 27 from 2015–20, 57 from 2021 on and 40 unknown. Adoption is therefore
   "cumulative over two decades", not "current". Filter by `course_offerings.year` for a
   current view.
6. **Module handbooks.** One PDF lists dozens of courses, and we store it as a single
   offering. For example, TU Darmstadt's MSc AI & ML handbook yields 10 books.
7. **Rate limits.** Firecrawl returned HTTP 429 on the first parallel run. Calls are now
   serialised with at least 4 s spacing and a retry. The 429 rows are still in
   `crawl_log`.

## What this means for the thesis corpus

The KG thesis corpus should include ESL, PRML, Murphy (MLAPP and/or PML1), Goodfellow et
al. and ISL. These five account for most of the adoption signal in the pilot. ISL, ESL,
PML1/2, MML, CIML, D2L, Understanding ML and ITILA are also freely available online,
which makes them practical corpus sources. The phase-2 materials table (slides, notes,
homework and exams, with URLs) is the other corpus source.

## Next steps

1. Discovery beyond the site search: crawl department "courses" index pages, and add a
   second search provider for IR, CN and JP. Try GitHub search for Iranian course repos.
2. An LLM pass (`method='llm'`) over the 129 unmatched citation lines and over pages with
   a course but no books. Use it to grow `books.toml`.
3. Split module handbooks into one row per module.
4. Scale to `us_top50`, then `countries_core`. At about 2–3 searches per university, this
   is roughly 5–6 Firecrawl credits per university.
5. Phase 2: HEAD-check material URLs, expand YouTube playlists, and link homework to
   solution pairs.
