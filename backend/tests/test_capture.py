"""Schema versioning, thresholds on events, reading-shape and copy events, post-visit questions."""

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from sqlmodel import Session, select

from app.db import make_engine
from app.models import SCHEMA_VERSION, Event, Paper

from .test_api import dwell, log_lines, open_session, send


def events_of(client, type: str) -> list[Event]:
    with Session(client.app.state.engine) as db:
        return db.exec(select(Event).where(Event.type == type)).all()


def client_event(arxiv_id, session_id, type, payload, **extra) -> dict:
    return {
        "event_id": str(uuid4()),
        "type": type,
        "arxiv_id": arxiv_id,
        "session_id": session_id,
        "ts": datetime.now(UTC).isoformat(),
        "payload": payload,
        **extra,
    }


# --- schema version and thresholds -------------------------------------------------


def test_server_events_carry_version_and_thresholds(client, add_paper, settings):
    add_paper()
    (added,) = events_of(client, "paper_added")
    assert added.schema_version == SCHEMA_VERSION
    assert added.config == settings.event_config()
    assert set(added.config) == {"idle_cutoff_s", "return_visit_gap_min", "page_viewed_min_s", "auto_read_threshold"}
    (line,) = [e for e in log_lines(settings) if e["type"] == "paper_added"]
    assert line["schema_version"] == SCHEMA_VERSION and line["config"] == settings.event_config()


def test_client_events_keep_the_thresholds_the_browser_used(client, add_paper):
    paper = add_paper()
    sid = open_session(client, paper)
    used = {"idle_cutoff_s": 90, "page_viewed_min_s": 5, "unknown": 1, "auto_read_threshold": "high"}
    send(client, {**dwell(paper["arxiv_id"], sid, 1, 3), "schema_version": 2, "config": used})
    (e,) = events_of(client, "page_dwell")
    assert e.schema_version == 2
    assert e.config == {"idle_cutoff_s": 90, "page_viewed_min_s": 5}  # junk dropped


def test_events_queued_by_an_old_browser_are_version_1(client, add_paper, settings):
    paper = add_paper()
    sid = open_session(client, paper)
    send(client, dwell(paper["arxiv_id"], sid, 1, 3))  # no schema_version, no config
    (e,) = events_of(client, "page_dwell")
    assert e.schema_version == 1
    assert e.config == settings.event_config()  # best guess: what the server had


# --- reading shape and copies ------------------------------------------------------


def test_page_dwell_bands(client, add_paper):
    paper = add_paper()
    sid = open_session(client, paper)
    good = dwell(paper["arxiv_id"], sid, 1, 4)
    good["payload"]["band_ms"] = [4000, 4000, 4000, 2000, 0, 0, 0, 0, 0, 0]
    short = dwell(paper["arxiv_id"], sid, 1, 4)
    short["payload"]["band_ms"] = [1, 2, 3]
    negative = dwell(paper["arxiv_id"], sid, 1, 4)
    negative["payload"]["band_ms"] = [-1] * 10
    assert send(client, good, short, negative)["stored"] == 1
    (e,) = events_of(client, "page_dwell")
    assert e.payload["band_ms"][0] == 4000


def test_text_copied_keeps_length_only(client, add_paper):
    paper = add_paper()
    sid = open_session(client, paper)
    a = paper["arxiv_id"]
    ok = client_event(a, sid, "text_copied", {"page": 3, "chars": 120})
    no_page = client_event(a, sid, "text_copied", {"page": None, "chars": 8})
    empty = client_event(a, sid, "text_copied", {"page": 3, "chars": 0})
    text = client_event(a, sid, "text_copied", {"page": 3, "chars": "the copied words"})
    assert send(client, ok, no_page, empty, text)["stored"] == 2
    assert sorted(e.payload["chars"] for e in events_of(client, "text_copied")) == [8, 120]


# --- post-visit questions ----------------------------------------------------------

ANSWER = {"interest": 4, "value": 5, "novelty": 2, "level": "just_right", "reasons": ["great_method"],
          "context": "after_reading", "shown_ms": 5400}


def test_first_open_asks_once(client, add_paper):
    paper = add_paper()
    assert paper["feedback_state"] == "none"
    assert client.get("/api/feedback/pending").json() == []
    open_session(client, paper)
    (pending,) = client.get("/api/feedback/pending").json()
    assert pending["arxiv_id"] == paper["arxiv_id"] and pending["feedback_state"] == "pending"

    resp = client.post(f"/api/papers/{paper['key']}/feedback", json=ANSWER)
    assert resp.status_code == 200 and resp.json()["feedback_state"] == "done"
    (e,) = events_of(client, "paper_feedback")
    assert e.payload == {k: ANSWER[k] for k in ("interest", "value", "novelty", "level", "reasons", "context", "shown_ms")}

    assert client.get("/api/feedback/pending").json() == []
    assert client.post(f"/api/papers/{paper['key']}/feedback", json=ANSWER).status_code == 409
    open_session(client, paper)  # later visits don't ask again
    assert client.get("/api/feedback/pending").json() == []


def test_partial_answers_and_skips(client, add_paper):
    a = add_paper("2401.00001")
    b = add_paper("2401.00002")
    for p in (a, b):
        open_session(client, p)
    partial = {"novelty": 3, "context": "next_visit", "shown_ms": 100}
    assert client.post(f"/api/papers/{a['key']}/feedback", json=partial).json()["feedback_state"] == "done"
    skip = {"skipped": "didnt_read", "context": "next_visit", "shown_ms": 900}
    assert client.post(f"/api/papers/{b['key']}/feedback", json=skip).json()["feedback_state"] == "skipped"
    (e,) = events_of(client, "feedback_skipped")
    assert e.payload == {"why": "didnt_read", "context": "next_visit", "shown_ms": 900}


def test_answers_are_validated(client, add_paper):
    paper = add_paper()
    open_session(client, paper)
    url = f"/api/papers/{paper['key']}/feedback"
    base = {"context": "after_reading", "shown_ms": 10}
    for bad in (
        base,  # nothing answered
        {**base, "interest": 4, "skipped": "skip"},  # both
        {**base, "interest": 6},
        {**base, "level": "meh"},
        {**base, "reasons": ["boring"]},
        {**base, "interest": 3, "context": "whenever"},
    ):
        assert client.post(url, json=bad).status_code == 422, bad
    assert client.get("/api/feedback/pending").json()[0]["feedback_state"] == "pending"


def test_pending_is_per_user(client, make_client, add_paper):
    paper = add_paper()
    open_session(client, paper)
    bob = make_client("bob")
    assert bob.get("/api/feedback/pending").json() == []
    assert bob.post(f"/api/papers/{paper['key']}/feedback", json=ANSWER).status_code == 404


def test_papers_opened_before_questions_existed_are_not_asked(client, add_paper):
    paper = add_paper()
    open_session(client, paper)
    with Session(client.app.state.engine) as db:  # as if opened before this feature
        p = db.get(Paper, (1, paper["arxiv_id"]))
        p.feedback_state = "none"
        db.add(p)
        db.commit()
    open_session(client, paper)
    assert client.get("/api/feedback/pending").json() == []


# --- migration ---------------------------------------------------------------------


def test_new_columns_are_added_to_an_existing_database(tmp_path):
    path = tmp_path / "app.db"
    make_engine(f"sqlite:///{path}").dispose()
    con = sqlite3.connect(path)
    con.executescript(
        """
        ALTER TABLE events DROP COLUMN schema_version;
        ALTER TABLE events DROP COLUMN config;
        ALTER TABLE papers DROP COLUMN feedback_state;
        INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES (1, 'u', '!', 0, '2026-01-01');
        INSERT INTO papers (user_id, arxiv_id, title, authors, abstract, categories, status, status_source,
          rating_prompted, added_at) VALUES (1, '2401.1', 'T', '[]', '', '[]', 'read', 'auto', 0, '2026-01-01');
        INSERT INTO events (event_id, user_id, arxiv_id, type, ts, payload)
          VALUES ('e1', 1, '2401.1', 'paper_added', '2026-01-01', '{}');
        """
    )
    con.commit()
    con.close()
    engine = make_engine(f"sqlite:///{path}")
    make_engine(f"sqlite:///{path}")  # second start is a no-op
    with Session(engine) as db:
        e = db.get(Event, "e1")
        assert e.schema_version == 1 and e.config is None
        assert db.get(Paper, (1, "2401.1")).feedback_state == "none"


def test_answers_in_admin_and_export(client, add_paper, settings, tmp_path):
    import csv

    from app.cli import export

    a = add_paper("2401.00001")
    add_paper("2401.00002")  # never opened: blank answer columns
    open_session(client, a)
    client.post(f"/api/papers/{a['key']}/feedback", json=ANSWER)
    rows = {r["arxiv_id"]: r for r in client.get("/api/admin/papers").json()}
    assert rows["2401.00001"]["feedback"]["value"] == 5
    assert rows["2401.00002"]["feedback"] is None

    with Session(client.app.state.engine) as db:
        export(db, settings, tmp_path / "out")
    with (tmp_path / "out" / "papers.csv").open() as f:
        papers = {r["arxiv_id"]: r for r in csv.DictReader(f)}
    assert papers["2401.00001"]["feedback_interest"] == "4"
    assert papers["2401.00001"]["feedback_reasons"] == "great_method"
    assert papers["2401.00002"]["feedback_state"] == "none" and papers["2401.00002"]["feedback_value"] == ""
