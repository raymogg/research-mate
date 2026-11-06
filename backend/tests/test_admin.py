import csv
import json

from sqlmodel import Session

from app.cli import export
from app.events import CLIENT_EVENT_TYPES, EVENT_CATALOG

from .test_api import dwell, open_session, send


def test_catalog_covers_client_types():
    assert CLIENT_EVENT_TYPES == {
        "page_dwell", "section_dwell", "paper_closed", "text_copied", "impression", "zoom_changed", "view_changed"
    }
    assert all({"source", "description", "payload"} <= set(spec) for spec in EVENT_CATALOG.values())


def test_overview_counts_and_delivery(client, add_paper, feedback):
    paper = add_paper(num_pages=4)
    sid = open_session(client, paper)
    feedback.down = True
    send(client, dwell(paper["arxiv_id"], sid, 1, 7), dwell(paper["arxiv_id"], sid, 2, 3))

    o = client.get("/api/admin/overview").json()
    t = o["totals"]
    assert t["papers"] == 1 and t["papers_by_status"]["reading"] == 1
    assert t["sessions"] == 1 and t["total_active_ms"] == 10_000
    assert t["unreported_events"] == 2 and t["oldest_unreported_ts"]
    counts = {e["type"]: e["count"] for e in o["event_types"]}
    assert counts["page_dwell"] == 2 and counts["paper_added"] == 1 and counts["tag_removed"] == 0
    assert o["storage"]["feedback_log_lines"] == t["events"] - 2

    feedback.down = False
    client.put(f"/api/papers/{paper['key']}/tags/x")  # triggers a forward
    assert client.get("/api/admin/overview").json()["totals"]["unreported_events"] == 0


def test_events_sessions_papers(client, add_paper):
    paper = add_paper(num_pages=4)
    sid = open_session(client, paper)
    send(client, dwell(paper["arxiv_id"], sid, 1, 7), dwell(paper["arxiv_id"], sid, 3, 2))

    events = client.get("/api/admin/events", params={"type": "page_dwell"}).json()
    assert len(events) == 2 and all(e["title"] == paper["title"] for e in events)
    assert client.get("/api/admin/events", params={"limit": 1}).json()[0]["type"]

    s = client.get("/api/admin/sessions").json()[0]
    assert s["session_id"] == sid and s["active_ms"] == 9000 and s["pages_touched"] == 2 and s["events"] == 3  # opened + 2 dwell

    p = client.get("/api/admin/papers").json()[0]
    assert p["page_ms"] == {"1": 7000, "3": 2000} and p["pages_viewed"] == 1


def test_export(client, add_paper, settings, tmp_path):
    paper = add_paper(num_pages=4)
    sid = open_session(client, paper)
    send(client, dwell(paper["arxiv_id"], sid, 2, 6))
    client.patch(f"/api/papers/{paper['key']}", json={"rating": 3})

    out = tmp_path / "export"
    with Session(client.app.state.engine) as db:
        export(db, settings, out)
    papers = list(csv.DictReader((out / "papers.csv").open()))
    assert papers[0]["rating"] == "3" and papers[0]["total_active_ms"] == "6000"
    assert list(csv.DictReader((out / "page_dwell.csv").open())) == [
        {"user_id": "1", "arxiv_id": paper["arxiv_id"], "page": "2", "active_ms": "6000"}
    ]
    events = [json.loads(line) for line in (out / "events.jsonl").open()]
    assert {e["type"] for e in events} >= {"paper_added", "paper_opened", "page_dwell", "rating_set"}
    assert len(list(csv.DictReader((out / "sessions.csv").open()))) == 1
