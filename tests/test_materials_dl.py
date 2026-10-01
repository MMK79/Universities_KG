"""Download guards: sanitisation, eligibility, size cap, robots, login, total budget.
No network: httpx.MockTransport serves the responses."""

import httpx

from mlcourses import materials_dl as m


def test_slug_is_filesystem_safe():
    assert m.slug("../../etc/passwd") == "etc_passwd"
    assert m.slug("Université de Montréal") == "Universite_de_Montreal"
    assert m.slug("") == "x"
    assert "/" not in m.slug("a/b\\c") and not m.slug("...hidden").startswith(".")


def test_eligible_only_public_pdf_pptx_slides_notes():
    ok = "https://x.edu/c/lec1.pdf?v=2"
    assert m.eligible("slides", ok) and m.eligible("lecture_notes", "http://x.edu/a.PPTX")
    assert not m.eligible("video", ok)
    assert not m.eligible("homework", ok)
    assert not m.eligible("slides", "https://x.edu/a.html")
    assert not m.eligible("slides", "ftp://x.edu/a.pdf")
    assert not m.eligible("slides", "https://canvas.x.edu/a.pdf")
    assert not m.eligible("slides", "https://stanford.instructure.com/a.pdf")


def test_filename_sanitised_and_collisions_get_a_hash():
    assert m.filename_for("https://x.edu/a/My%20Lecture%201.pdf") == "My_Lecture_1.pdf"
    assert m.filename_for("https://x.edu/a/..%2f..%2fevil.pdf") == "evil.pdf"
    taken = {"l.pdf"}
    n = m.filename_for("https://x.edu/other/l.pdf", taken)
    assert n != "l.pdf" and n.startswith("l-") and n.endswith(".pdf")
    assert m.filename_for("https://x.edu/").startswith("file-")


def test_target_path_stays_under_root(tmp_path):
    p = m.target_path(tmp_path, "United States", "MIT", "6.867", "https://x.edu/l1.pdf")
    assert p == tmp_path / "United_States" / "MIT" / "6.867" / "l1.pdf"
    p = m.target_path(tmp_path, "..", "..", "..", "https://x.edu/l1.pdf")
    assert tmp_path.resolve() in p.resolve().parents


def _dl(tmp_path, handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return m.Downloader(tmp_path, kw.pop("budget", m.Budget(0)), client, delay=0, **kw)


def _serve(body=b"%PDF-1.4 hello", ctype="application/pdf", robots="", status=200):
    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404 if not robots else 200, text=robots)
        return httpx.Response(status, content=body, headers={"content-type": ctype})

    return h


def test_download_ok_records_size_and_sha(tmp_path):
    d = _dl(tmp_path, _serve())
    r = d.fetch("https://x.edu/l.pdf", tmp_path / "c" / "l.pdf")
    assert r.status == "ok" and r.bytes == 14 and len(r.sha256) == 64
    assert (tmp_path / r.local_path).read_bytes().startswith(b"%PDF")


def test_robots_disallow_is_respected(tmp_path):
    d = _dl(tmp_path, _serve(robots="User-agent: *\nDisallow: /private/\n"))
    assert d.fetch("https://x.edu/private/l.pdf", tmp_path / "l.pdf").status == "skipped-robots"
    assert not (tmp_path / "l.pdf").exists()
    assert d.fetch("https://x.edu/public/l.pdf", tmp_path / "p.pdf").status == "ok"


def test_too_large_by_stream_without_content_length(tmp_path):
    d = _dl(tmp_path, _serve(body=b"x" * 5000), max_file=1000)
    r = d.fetch("https://x.edu/big.pdf", tmp_path / "big.pdf")
    assert r.status == "too-large"
    assert not list(tmp_path.glob("big*"))  # no partial file left behind


def test_too_large_by_content_length_does_not_download(tmp_path):
    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "application/pdf",
                                            "content-length": "999999999"})  # fmt: skip

    r = _dl(tmp_path, h).fetch("https://x.edu/big.pdf", tmp_path / "big.pdf")
    assert r.status == "too-large"


def test_html_and_401_are_not_stored(tmp_path):
    assert (
        _dl(tmp_path, _serve(ctype="text/html"))
        .fetch("https://x.edu/l.pdf", tmp_path / "a.pdf")
        .status
        == "not-document"
    )
    assert (
        _dl(tmp_path, _serve(status=401)).fetch("https://x.edu/l.pdf", tmp_path / "b.pdf").status
        == "skipped-login"
    )
    assert (
        _dl(tmp_path, _serve(status=404)).fetch("https://x.edu/l.pdf", tmp_path / "c.pdf").status
        == "http-error"
    )


def test_total_budget_stops_downloads(tmp_path):
    budget = m.Budget(used=0, cap=20)
    d = _dl(tmp_path, _serve(body=b"x" * 14), budget=budget)
    assert d.fetch("https://x.edu/a.pdf", tmp_path / "a.pdf").status == "ok"
    assert d.fetch("https://x.edu/b.pdf", tmp_path / "b.pdf").status == "budget-stop"
    assert not (tmp_path / "b.pdf").exists()
    assert budget.used == 14


def test_budget_reserve_release():
    b = m.Budget(0, cap=10)
    assert b.reserve(10) and not b.reserve(1) and b.exhausted
    b.release(5)
    assert b.reserve(5)
