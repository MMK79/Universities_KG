from mlcourses.books import catalogue, find_isbns, isbn10_to_13, match_books, unmatched_citations


def slugs(text, links=()):
    return {h.slug for h in match_books(text, list(links))}


def test_catalogue_loads_and_slugs_unique():
    cat = catalogue()
    assert len(cat) >= 40
    assert len({b.slug for b in cat}) == len(cat)


def test_distinctive_title_with_author_is_high_confidence():
    hits = match_books("Textbook: Pattern Recognition and Machine Learning, Chris Bishop.")
    [h] = [h for h in hits if h.slug == "bishop-prml"]
    assert h.confidence == 0.9
    assert "Pattern Recognition" in h.raw_citation


def test_generic_title_needs_author():
    assert "goodfellow-dl" not in slugs("This course is an introduction to deep learning.")
    assert "goodfellow-dl" in slugs("Deep Learning, Ian Goodfellow, Yoshua Bengio, A. Courville")
    assert "mitchell-ml" not in slugs("Machine Learning, Spring 2024, CS 4780")
    assert "mitchell-ml" in slugs("Machine Learning, Tom Mitchell, McGraw-Hill 1997")


def test_url_match():
    text = "Readings\nDeep Learning book\n"
    assert "goodfellow-dl" in slugs(text, [("https://www.deeplearningbook.org/", "DL book")])


def test_isbn_match_and_conversion():
    assert isbn10_to_13("0387310738") == "9780387310732"
    assert "9780387310732" in find_isbns("ISBN 978-0-387-31073-2")
    assert "bishop-prml" in slugs("Some book. ISBN: 978-0-387-31073-2")


def test_role_from_heading():
    text = "## Required textbook\nThe Elements of Statistical Learning, Hastie et al.\n"
    [h] = [h for h in match_books(text) if h.slug == "hastie-esl"]
    assert h.role == "textbook"
    text = "## Further reading\nThe Elements of Statistical Learning, Hastie et al.\n"
    [h] = [h for h in match_books(text) if h.slug == "hastie-esl"]
    assert h.role == "recommended"


def test_unmatched_citations_collects_real_citations_only():
    text = (
        "## Literature\n"
        "Some Author, A. Nice Book on Kernels. Springer, 2011.\n"
        "Last updated April 23, 2026 (03:37 PM).\n"
        "## Schedule\nLecture 1\n"
    )
    un = unmatched_citations(text, [])
    assert [u.raw_citation for u in un] == ["Some Author, A. Nice Book on Kernels. Springer, 2011."]


def test_chinese_textbooks():
    assert "lihang-slm" in slugs("教材：李航，《统计学习方法》，清华大学出版社")
    assert "zhou-ml" in slugs("参考书：周志华《机器学习》")
