"""Impressions, discovery source, session context and zoom (schema v3)."""

import csv
import json
import sqlite3

from sqlmodel import Session

from app.db import make_engine
from app.models import SCHEMA_VERSION, Paper, ReadingSession

from .test_api import open_session, send
from .test_capture import client_event, events_of

SLATE = "slate-0001-abcd"


def impression(arxiv_id, position=1, **payload) -> dict:
    p = {
        "list": "library",
        "slate_id": SLATE,
        "position": position,
        "slate_size": 3,
        "filters": {"sort": "last_opened"},
        "policy": "library:last_opened",
        "purpose": "library",
        "score": None,
        "propensity": 1,
        **payload,
    }
    return {**client_event(arxiv_id, None, "impression", p), "schema_version": SCHEMA_VERSION}


CONTEXT = {
    "via": {"kind": "library", "slate_id": SLATE, "position": 2},
    "device": "desktop",
    "viewport": [1280, 860],
    "screen": [1512, 982],
    "dpr": 2,
    "local_hour": 21,
    "weekday": 3,
    "tz": "Australia/Sydney",
    "tz_offset_min": 660,
}


# --- impressions -------------------------------------------------------------------


def test_impressions_need_no_session(client, add_paper):
    paper = add_paper()
    assert send(client, impression(paper["arxiv_id"]))["stored"] == 1
    (e,) = events_of(client, "impression")
    assert e.session_id is None and e.schema_version == SCHEMA_VERSION
    assert e.payload["policy"] == "library:last_opened"
    # Nothing about the paper changes, so nothing is sent back
    assert send(client, impression(paper["arxiv_id"], position=2))["papers"] == []


def test_impressions_are_validated(client, make_client, add_paper):
    a = add_paper()["arxiv_id"]
    sid = open_session(client, client.get("/api/papers").json()[0])
    bad = [
        impression(a, position=4),  # past the end of the slate
        impression(a, position=0),
        impression(a, purpose="because"),
        impression(a, list="inbox"),
        impression(a, propensity=0),
        impression(a, propensity=1.5),
        impression(a, slate_id="x"),
        impression(a, filters="sort=added"),
        impression("2401.99999"),  # not in the library
        {**impression(a), "session_id": sid},  # impressions are never in a session
    ]
    assert send(client, *bad)["stored"] == 0
    bob = make_client("bob")
    assert send(bob, impression(a))["stored"] == 0  # alice's paper, not bob's


def test_recommender_impressions_keep_score_and_propensity(client, add_paper):
    a = add_paper()["arxiv_id"]
    send(client, impression(a, list="suggested", policy="b2-logreg@2026-11-10", purpose="explore",
                            score=0.41, propensity=0.12))
    (e,) = events_of(client, "impression")
    assert (e.payload["purpose"], e.payload["score"], e.payload["propensity"]) == ("explore", 0.41, 0.12)


# --- discovery source --------------------------------------------------------------


def test_add_records_where_the_paper_was_found(client):
    paper = client.post("/api/papers", json={"input": "2401.00001", "source": "colleague"}).json()
    assert paper["source"] == "colleague"
    (added,) = events_of(client, "paper_added")
    assert added.payload == {"input": "2401.00001", "source": "colleague", "intent": "open"}

    other = client.post("/api/papers", json={"input": "2401.00002"}).json()
    assert other["source"] is None
    # Pasting it again with an answer fills it in; it never overwrites one
    assert client.post("/api/papers", json={"input": "2401.00002", "source": "social"}).json()["source"] == "social"
    assert client.post("/api/papers", json={"input": "2401.00002", "source": "search"}).json()["source"] == "social"

    assert client.post("/api/papers", json={"input": "2401.00003", "source": "twitter"}).status_code == 422


# --- session context ---------------------------------------------------------------


def test_session_context_is_kept(client, add_paper):
    paper = add_paper()
    resp = client.post(f"/api/papers/{paper['key']}/sessions", json={"context": CONTEXT})
    assert resp.status_code == 201
    (opened,) = events_of(client, "paper_opened")
    assert opened.payload["context"] == CONTEXT
    with Session(client.app.state.engine) as db:
        assert db.get(ReadingSession, resp.json()["session_id"]).context == CONTEXT


def test_bad_context_never_stops_reading(client, add_paper):
    paper = add_paper()
    for body in (None, {}, {"context": {"device": "fridge"}}, {"context": {"viewport": [-1, 5]}}):
        kwargs = {} if body is None else {"json": body}
        assert client.post(f"/api/papers/{paper['key']}/sessions", **kwargs).status_code == 201
    assert all(e.payload["context"] is None for e in events_of(client, "paper_opened"))


def test_partial_context(client, add_paper):
    paper = add_paper()
    client.post(f"/api/papers/{paper['key']}/sessions", json={"context": {"via": {"kind": "direct"}, "local_hour": 7}})
    (opened,) = events_of(client, "paper_opened")
    assert opened.payload["context"] == {"via": {"kind": "direct"}, "local_hour": 7}


# --- zoom --------------------------------------------------------------------------


def test_zoom_changes(client, add_paper):
    paper = add_paper()
    sid = open_session(client, paper)
    a = paper["arxiv_id"]
    ok = client_event(a, sid, "zoom_changed", {"from": 1, "to": 1.15, "page": 3})
    bad = client_event(a, sid, "zoom_changed", {"from": 1, "to": 50, "page": 3})
    no_session = client_event(a, None, "zoom_changed", {"from": 1, "to": 1.15, "page": 3})
    assert send(client, ok, bad, no_session)["stored"] == 1


# --- export and migration ----------------------------------------------------------


def test_export_links_impressions_to_the_sessions_they_led_to(client, add_paper, settings, tmp_path):
    from app.cli import export

    a = add_paper("2401.00001", num_pages=None)
    b = add_paper("2401.00002", num_pages=None)
    send(client, impression(a["arxiv_id"], position=1), impression(b["arxiv_id"], position=2))
    sid = client.post(f"/api/papers/{b['key']}/sessions", json={"context": CONTEXT}).json()["session_id"]

    with Session(client.app.state.engine) as db:
        export(db, settings, tmp_path / "out")
    with (tmp_path / "out" / "impressions.csv").open() as f:
        rows = {r["arxiv_id"]: r for r in csv.DictReader(f)}
    assert rows["2401.00001"]["opened_session_id"] == ""  # seen, not opened: a negative
    assert rows["2401.00002"]["opened_session_id"] == sid
    assert json.loads(rows["2401.00002"]["filters"]) == {"sort": "last_opened"}
    with (tmp_path / "out" / "sessions.csv").open() as f:
        (s,) = list(csv.DictReader(f))
    assert (s["via"], s["via_position"], s["device"], s["local_hour"]) == ("library", "2", "desktop", "21")


def test_v3_columns_are_added_to_an_existing_database(tmp_path):
    path = tmp_path / "app.db"
    make_engine(f"sqlite:///{path}").dispose()
    con = sqlite3.connect(path)
    con.executescript(
        """
        ALTER TABLE papers DROP COLUMN source;
        ALTER TABLE sessions DROP COLUMN context;
        INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES (1, 'u', '!', 0, '2026-01-01');
        INSERT INTO papers (user_id, arxiv_id, title, authors, abstract, categories, status, status_source,
          rating_prompted, feedback_state, added_at) VALUES (1, '2401.1', 'T', '[]', '', '[]', 'read', 'auto', 0,
          'none', '2026-01-01');
        INSERT INTO sessions (session_id, user_id, arxiv_id, started_at, active_ms, is_return_visit)
          VALUES ('s1', 1, '2401.1', '2026-01-01', 0, 0);
        """
    )
    con.commit()
    con.close()
    with Session(make_engine(f"sqlite:///{path}")) as db:
        assert db.get(Paper, (1, "2401.1")).source is None
        assert db.get(ReadingSession, "s1").context is None
