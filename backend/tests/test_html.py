from uuid import uuid4

import pytest

from app import arxiv

from .conftest import dwell
from .test_api import open_session, send
from .test_context import CONTEXT

PAGE = """<!DOCTYPE html><html><head><title>A Paper</title>
<link rel="stylesheet" href="/static/browse/0.3.4/css/arxiv-html-papers-20260823.css" type="text/css">
<link rel="stylesheet" href="https://static.arxiv.org/static/design-system/latest/fonts.css">
<script src="/static/x.js"></script><script>alert(1)</script></head>
<body><header class="arxiv-html-header">site</header>
<div class="ltx_page_main"><div class="ltx_page_content">
<article class="ltx_document">
<h1 class="ltx_title">A Paper</h1>
<div class="ltx_abstract" id="abstract">We study things.</div>
<section id="S1" class="ltx_section"><p>See <a href="#S2">Section 2</a> and
<a href="https://github.com/x/y">code</a> and <a href="javascript:steal()">this</a>.</p>
<img src="2606.12683v1/x1.png" onerror="steal()" alt="fig">
<script>steal()</script><iframe src="https://evil.example"></iframe></section>
<section id="S2" class="ltx_section"><p onclick='steal()'>Two</p></section>
</article></div></div><footer>site footer</footer></body></html>"""


def test_reader_document_keeps_the_paper_and_drops_the_rest():
    doc = arxiv.reader_document(PAGE, "https://arxiv.org/html/2606.12683v1")
    assert '<section id="S1"' in doc and "We study things." in doc
    assert "<script" not in doc and "<iframe" not in doc and "steal()" not in doc
    assert "site footer" not in doc and "arxiv-html-header" not in doc
    assert 'href="https://arxiv.org/static/browse/0.3.4/css/arxiv-html-papers-20260823.css"' in doc
    assert "design-system" not in doc
    assert 'src="https://arxiv.org/html/2606.12683v1/x1.png"' in doc
    assert 'href="#S2"' in doc  # in-paper links stay in the paper
    assert '<a target="_blank" rel="noopener noreferrer" href="https://github.com/x/y"' in doc
    assert 'data-theme="__THEME__"' in doc and "<title>A Paper</title>" in doc


def test_reader_document_needs_an_article():
    with pytest.raises(arxiv.NoHtml):
        arxiv.reader_document("<html><body>No HTML for you</body></html>", "https://arxiv.org/html/x")


@pytest.fixture
def html_source(monkeypatch):
    calls = []

    def fake(arxiv_id, version):
        calls.append((arxiv_id, version))
        if arxiv_id == "2402.00002":
            raise arxiv.NoHtml(arxiv_id)
        return arxiv.reader_document(PAGE, f"https://arxiv.org/html/{arxiv_id}{version or ''}")

    monkeypatch.setattr(arxiv, "fetch_html", fake)
    return calls


def test_html_endpoint_serves_cached_paper_with_a_no_script_policy(client, add_paper, html_source, settings):
    paper = add_paper()
    resp = client.get(f"/api/papers/{paper['key']}/html?theme=dark")
    assert resp.status_code == 200 and resp.headers["x-html-source"] == "arxiv"
    policy = resp.headers["content-security-policy"]
    assert "script-src 'none'" in policy and "frame-ancestors 'self'" in policy
    assert 'data-theme="dark"' in resp.text
    again = client.get(f"/api/papers/{paper['key']}/html")
    assert again.headers["x-html-source"] == "cache" and 'data-theme="light"' in again.text
    assert html_source == [("2401.12345", "v2")]
    assert client.get(f"/api/papers/{paper['key']}/html?theme=neon").status_code == 422


def test_html_endpoint_404s_without_html_and_needs_the_paper(client, make_client, add_paper, html_source):
    paper = add_paper("2402.00002")
    resp = client.get(f"/api/papers/{paper['key']}/html")
    assert resp.status_code == 404 and "no HTML" in resp.json()["detail"]
    assert make_client("bob").get("/api/papers/2401.12345/html").status_code == 404  # not in bob's library


def section(arxiv_id, sid, name, seconds, index=1, of=4, share=0.25):
    return {
        "event_id": str(uuid4()),
        "type": "section_dwell",
        "arxiv_id": arxiv_id,
        "session_id": sid,
        "ts": "2026-11-06T01:00:00Z",
        "payload": {"section": name, "index": index, "of": of, "share": share, "active_ms": int(seconds * 1000),
                    "band_ms": [int(seconds * 100)] * 10},
        "schema_version": 5,
    }


def test_reading_sections_counts_time_and_completes_the_paper(client, add_paper):
    paper = add_paper(num_pages=None)  # the PDF was never opened, so no page count
    sid = client.post(f"/api/papers/{paper['key']}/sessions", json={"context": {**CONTEXT, "device": "phone", "view": "html"}}).json()[
        "session_id"]
    a = paper["arxiv_id"]
    out = send(client, section(a, sid, "abstract", 20, 1, share=0.1), section(a, sid, "S1", 60, 2, share=0.4))
    p = out["papers"][0]
    assert p["metrics"]["total_ms"] == 80_000 and p["metrics"]["completion"] == 0.5 and p["status"] == "reading"
    out = send(client, section(a, sid, "S2", 30, 3, share=0.35), section(a, sid, "bib", 2, 4, share=0.15))
    p = out["papers"][0]
    # 0.85 of the height read (bib got under PAGE_VIEWED_MIN_S): past the 0.8 threshold
    assert p["metrics"]["completion"] == 0.85 and p["status"] == "read"
    detail = client.get(f"/api/papers/{paper['key']}").json()
    assert detail["metrics"]["section_ms"] == {"abstract": 20_000, "S1": 60_000, "S2": 30_000, "bib": 2_000}
    events = client.get("/api/admin/events?type=completion_reached").json()
    assert events[0]["payload"]["html_share_viewed"] == 0.85
    opened = client.get("/api/admin/events?type=paper_opened").json()[0]
    assert opened["payload"]["context"]["view"] == "html"


def test_pdf_and_html_reading_combine_by_the_further_one(client, add_paper):
    paper = add_paper(num_pages=10)
    sid = open_session(client, paper)
    a = paper["arxiv_id"]
    send(client, dwell(a, sid, 1, 30), dwell(a, sid, 2, 30))
    out = send(client, section(a, sid, "S1", 30, 1, share=0.3))
    assert out["papers"][0]["metrics"]["completion"] == 0.3  # html 0.3 beats pdf 0.2
    assert out["papers"][0]["metrics"]["total_ms"] == 90_000


@pytest.mark.parametrize("payload", [
    {"section": "", "index": 1, "of": 2, "share": 0.5, "active_ms": 10, "band_ms": [0] * 10},
    {"section": "S1", "index": 3, "of": 2, "share": 0.5, "active_ms": 10, "band_ms": [0] * 10},
    {"section": "S1", "index": 1, "of": 2, "share": 1.5, "active_ms": 10, "band_ms": [0] * 10},
    {"section": "S1", "index": 1, "of": 2, "share": 0.5, "active_ms": 0, "band_ms": [0] * 10},
    {"section": "S1", "index": 1, "of": 2, "share": 0.5, "active_ms": 10},
])
def test_bad_section_dwell_is_dropped(client, add_paper, payload):
    paper = add_paper()
    sid = open_session(client, paper)
    e = section(paper["arxiv_id"], sid, "S1", 1)
    e["payload"] = payload
    assert send(client, e)["stored"] == 0


def test_view_changes_and_html_zoom_and_copy(client, add_paper):
    paper = add_paper()
    sid = open_session(client, paper)

    def event(type, payload):
        return {"event_id": str(uuid4()), "type": type, "arxiv_id": paper["arxiv_id"], "session_id": sid,
                "ts": "2026-11-06T01:00:00Z", "payload": payload, "schema_version": 5}

    good = [
        event("view_changed", {"from": "html", "to": "pdf", "reason": "no_html"}),
        event("zoom_changed", {"from": 1, "to": 1.15, "page": None, "section": "S2"}),
        event("text_copied", {"page": None, "section": "S2", "chars": 40}),
    ]
    bad = [
        event("view_changed", {"from": "pdf", "to": "pdf", "reason": "user"}),
        event("view_changed", {"from": "pdf", "to": "epub", "reason": "user"}),
        event("zoom_changed", {"from": 1, "to": 1.15, "page": None}),
    ]
    assert send(client, *good, *bad)["stored"] == 3
