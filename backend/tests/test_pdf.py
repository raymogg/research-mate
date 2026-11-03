from itertools import zip_longest

import httpx
import pytest

from app import arxiv

PDF = b"%PDF-1.7 " + b"x" * (3 * arxiv.CHUNK)


@pytest.fixture
def arxiv_pdf(monkeypatch):
    """Serve `state["body"]` for any arXiv PDF URL, counting requests."""
    state = {"body": PDF, "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        return httpx.Response(200, content=state["body"], headers={"content-type": "application/pdf"})

    real = httpx.Client
    monkeypatch.setattr(arxiv.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


def test_first_open_streams_then_caches(client, add_paper, arxiv_pdf, settings):
    paper = add_paper()
    first = client.get(f"/api/papers/{paper['key']}/pdf")
    assert first.status_code == 200 and first.content == PDF
    assert first.headers["x-pdf-source"] == "arxiv"
    assert first.headers["content-length"] == str(len(PDF))  # lets the browser show progress

    second = client.get(f"/api/papers/{paper['key']}/pdf")
    assert second.content == PDF and second.headers["x-pdf-source"] == "cache"
    assert arxiv_pdf["calls"] == 1
    assert [p.name for p in settings.pdf_dir.iterdir()] == ["2401.12345v2.pdf"]


def test_overlapping_downloads_both_succeed(arxiv_pdf, tmp_path):
    dest = tmp_path / "pdfs" / "2501.10100v5.pdf"
    _, a = arxiv.stream_pdf("2501.10100", "v5", dest)
    _, b = arxiv.stream_pdf("2501.10100", "v5", dest)
    got_a, got_b = b"", b""
    for chunk_a, chunk_b in zip_longest(a, b, fillvalue=b""):  # interleaved, as with two requests at once
        got_a += chunk_a
        got_b += chunk_b
    assert got_a == got_b == PDF
    assert dest.read_bytes() == PDF
    assert [p.name for p in dest.parent.iterdir()] == [dest.name]


def test_abandoned_download_leaves_no_cache(arxiv_pdf, tmp_path):
    dest = tmp_path / "pdfs" / "x.pdf"
    _, body = arxiv.stream_pdf("2501.10100", None, dest)
    next(body)
    body.close()  # browser navigated away mid-download
    assert list(dest.parent.iterdir()) == []


def test_non_pdf_is_a_502(client, add_paper, arxiv_pdf, settings):
    arxiv_pdf["body"] = b"<html>rate limited</html>"
    paper = add_paper()
    resp = client.get(f"/api/papers/{paper['key']}/pdf")
    assert resp.status_code == 502 and "did not return a PDF" in resp.json()["detail"]
    assert not settings.pdf_dir.exists() or list(settings.pdf_dir.iterdir()) == []
