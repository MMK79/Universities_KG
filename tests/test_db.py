"""Integration test: needs the docker Postgres (`make up`); skipped otherwise."""

import pytest

from mlcourses.db import course_key


def test_course_key():
    assert course_key("CS 229", "Machine Learning") == "code:CS229"
    assert course_key(None, "Machine Learning (Fall 2024)") == "title:machine learning"


def test_database_roundtrip():
    psycopg = pytest.importorskip("psycopg")
    from mlcourses.db import connect, seed_books

    try:
        conn = connect()
    except psycopg.OperationalError:
        pytest.skip("Postgres not running")
    with conn:
        ids = seed_books(conn)
        assert "bishop-prml" in ids
        n = conn.execute("SELECT count(*) FROM books").fetchone()[0]
        assert n >= 40
        conn.rollback()
