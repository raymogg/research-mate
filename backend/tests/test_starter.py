from sqlmodel import Session, func, select

from app import arxiv, starter
from app.models import Event, ReadingSession


def test_visitors_see_the_starter_list(anon):
    body = anon.get("/api/starter").json()
    assert body["policy"] == starter.POLICY
    assert [p["arxiv_id"] for p in body["papers"]][0] == "1706.03762"
    assert len(body["papers"]) == 5 and all(p["key"] and p["title"] and p["why"] for p in body["papers"])


def test_visitors_read_only_starter_papers_and_nothing_is_recorded(anon, app, monkeypatch):
    fetched = []

    def fake_pdf(arxiv_id, version, dest):
        fetched.append((arxiv_id, version))
        return 3, iter([b"%PD", b""])

    monkeypatch.setattr(arxiv, "stream_pdf", fake_pdf)
    monkeypatch.setattr(arxiv, "fetch_html", lambda arxiv_id, version: f"<html>{arxiv_id} {arxiv.THEME}</html>")

    resp = anon.get("/api/starter/1706.03762/pdf")
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/pdf"
    assert fetched == [("1706.03762", None)]
    html = anon.get("/api/starter/1706.03762/html?theme=dark")
    assert html.status_code == 200 and "dark" in html.text and "script-src 'none'" in html.headers["content-security-policy"]

    # Any other paper needs an account, so the site isn't an open arXiv proxy
    assert anon.get("/api/starter/2401.12345/pdf").status_code == 404
    assert anon.get("/api/starter/2401.12345/html").status_code == 404
    assert fetched == [("1706.03762", None)]

    with Session(app.state.engine) as db:
        assert db.exec(select(func.count()).select_from(Event)).one() == 0
        assert db.exec(select(func.count()).select_from(ReadingSession)).one() == 0


def test_signed_in_readers_add_starter_papers_as_suggested(client):
    paper = client.post("/api/papers", json={"input": "1706.03762", "source": "suggested", "intent": "later"})
    assert paper.status_code == 201 and paper.json()["source"] == "suggested"
    assert client.post("/api/papers", json={"input": "2401.12345", "source": "made-up"}).status_code == 422
