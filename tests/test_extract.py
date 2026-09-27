from mlcourses.config import load_subset
from mlcourses.discover import rank_candidates, score
from mlcourses.extract import (
    build_offering,
    classify_material,
    find_code,
    find_level,
    find_term,
)

PAGE = """TITLE: Introduction to Machine Learning | 10-301 + 10-601 | Spring 2026
# Introduction to Machine Learning
Instructors: Pat Virtue and Matt Gormley
## Syllabus
Lectures, homework, exams, office hours and grading policies.
### 3. Recommended Textbooks
Machine Learning, Tom Mitchell.
Machine Learning: a Probabilistic Perspective, Kevin Murphy.
"""


def test_build_offering():
    links = [("https://x.edu/hw1.pdf", "Homework 1"), ("https://youtu.be/abc", "Lecture video")]
    o = build_offering("https://x.edu/10601/", PAGE, links)
    assert o is not None
    assert o.code == "10-301"
    assert o.term == "Spring 2026" and o.year == 2026
    assert o.instructor == "Pat Virtue and Matt Gormley"
    assert o.topic == "ml-core"
    assert {b.slug for b in o.books} >= {"mitchell-ml", "murphy-mlpp"}
    assert {m.type for m in o.materials} == {"homework", "video"}


def test_non_course_page_rejected():
    assert build_offering("https://x.edu/news", "TITLE: ML news\nWe won an award.", []) is None


def test_codes_terms_levels():
    assert find_code("CS 229: Machine Learning", "", "") == "CS 229"
    assert find_code("6.3900 Introduction to Machine Learning", "", "") == "6.3900"
    assert find_code("Machine Learning", "", "https://tu.de/in2064/") == "IN2064"
    assert find_term("Machine Learning WS 23/24", "", "")[0] == "WS 23/24"
    assert find_term("", "Fall 2019 schedule", "") == ("Fall 2019", 2019)
    assert find_level("CS 7641", "", "CS 7641") == "grad"
    assert find_level("x", "an undergraduate course", None) == "undergrad"


def test_material_classifier():
    assert classify_material("https://a.edu/hw3_solutions.pdf", "HW3 solutions") == (
        "homework_solution"
    )
    assert classify_material("https://a.edu/midterm_sol.pdf", "Midterm solution") == (
        "exam_solution"
    )
    assert classify_material("https://a.edu/lec05.pptx", "Lecture 5 slides") == "slides"
    assert classify_material("https://www.youtube.com/playlist?list=1", "") == "video"
    assert classify_material("https://a.edu/about", "About") is None


def test_ranking_prefers_course_pages_on_domain():
    [u] = [u for u in load_subset("smoke") if u.key == "cmu.edu"]
    res = [
        {"url": "https://www.cs.cmu.edu/~x/10601/", "title": "10-601 Machine Learning Spring 2024",
         "description": "syllabus, homework, textbook"},
        {"url": "https://news.cmu.edu/ml", "title": "Machine learning news", "description": ""},
        {"url": "https://example.com/ml", "title": "Machine Learning course", "description": ""},
    ]
    ranked = rank_candidates(u, res)
    assert [r["url"] for r in ranked] == ["https://www.cs.cmu.edu/~x/10601/"]
    assert score(res[0]) > score(res[1])


def test_pilot_subset_resolves():
    assert len(load_subset("pilot")) == 43
