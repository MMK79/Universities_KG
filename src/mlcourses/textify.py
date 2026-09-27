"""HTML/PDF/markdown -> plain text with line structure preserved, plus links."""

from __future__ import annotations

import re
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

BLOCK_TAGS = "p div li tr h1 h2 h3 h4 h5 h6 br dt dd section article table ul ol pre".split()


def html_to_text_and_links(html: str, base_url: str) -> tuple[str, list[tuple[str, str]]]:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "svg", "template"]):
        t.decompose()
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "javascript:", "tel:")):
            continue
        absu = urldefrag(urljoin(base_url, href))[0]
        links.append((absu, " ".join(a.get_text(" ").split())[:200]))
    # mark headings so the section detector can find them
    for level in range(1, 7):
        for h in soup.find_all(f"h{level}"):
            h.insert(0, "\n" + "#" * level + " ")
    for t in soup.find_all(BLOCK_TAGS):
        t.append("\n")
    title = soup.title.get_text(" ").strip() if soup.title else ""
    text = soup.get_text("")
    lines = [" ".join(ln.split()) for ln in text.splitlines()]
    body = "\n".join(ln for ln in lines if ln)
    if title:
        body = f"TITLE: {title}\n{body}"
    return body, links


MD_LINK = re.compile(r"\[([^\]]{0,200})\]\((\S+?)(?:\s+\"[^\"]*\")?\)")


def markdown_links(md: str, base_url: str) -> list[tuple[str, str]]:
    return [
        (urldefrag(urljoin(base_url, u))[0], " ".join(t.split())) for t, u in MD_LINK.findall(md)
    ]


def pdf_to_text(fp) -> str:
    from pypdf import PdfReader

    reader = PdfReader(fp)
    parts = []
    for i, p in enumerate(reader.pages):
        if i >= 40:  # syllabi are short; avoid pulling whole textbooks
            break
        parts.append(p.extract_text() or "")
    return "\n".join(parts)
