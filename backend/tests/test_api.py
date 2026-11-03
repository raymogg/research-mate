import json
from datetime import timedelta

from app import metrics
from app.models import utcnow

from .conftest import dwell


def open_session(client, paper) -> str:
    resp = client.post(f"/api/papers/{paper['key']}/sessions")
    assert resp.status_code == 201
    return resp.json()["session_id"]


def send(client, *events) -> dict:
    resp = client.post("/api/events", json={"events": list(events)})
    assert resp.status_code == 200, resp.text
    return resp.json()


def log_lines(settings) -> list[dict]:
    path = settings.feedback_log
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# --- adding papers ---------------------------------------------------------


def test_add_paper_variants_resolve_to_one_paper(client):
    first = client.post("/api/papers", json={"input": "https://arxiv.org/abs/2401.12345"})
    assert first.status_code == 201
    for raw in ("https://arxiv.org/pdf/2401.12345v2", "2401.12345"):
        again = client.post("/api/papers", json={"input": raw})
        assert again.status_code == 200
        assert again.json()["arxiv_id"] == "2401.12345"
    assert len(client.get("/api/papers").json()) == 1
    assert first.json()["status"] == "unread"


def test_add_paper_rejects_bad_input(client):
    resp = client.post("/api/papers", json={"input": "https://example.com/paper"})
    assert resp.status_code == 400


def test_old_style_ids_work_via_key(client):
    paper = client.post("/api/papers", json={"input": "cs/0101001"}).json()
    assert paper["key"] == "cs_0101001"
    assert client.get("/api/papers/cs_0101001").json()["arxiv_id"] == "cs/0101001"


# --- reading metrics -------------------------------------------------------


def test_dwell_rollups(client, add_paper):
    paper = add_paper()
    sid = open_session(client, paper)
    send(client, dwell(paper["arxiv_id"], sid, 3, 60), dwell(paper["arxiv_id"], sid, 3, 60.5))
    send(client, dwell(paper["arxiv_id"], sid, 4, 2))
    m = client.get(f"/api/papers/{paper['key']}").json()["metrics"]
    assert m["page_ms"] == {"3": 120_500, "4": 2000}
    assert m["total_ms"] == 122_500
    assert m["pages_viewed"] == 1  # page 4 is under the 5 s threshold
    assert m["furthest_page"] == 4
    assert m["visits"] == 1


def test_resent_events_are_counted_once(client, add_paper):
    paper = add_paper()
    sid = open_session(client, paper)
    event = dwell(paper["arxiv_id"], sid, 1, 30, event_id="fixed-id-123")
    assert send(client, event)["stored"] == 1
    assert send(client, event)["stored"] == 0
    assert client.get(f"/api/papers/{paper['key']}").json()["metrics"]["total_ms"] == 30_000


def test_invalid_events_are_dropped(client, add_paper):
    paper = add_paper()
    sid = open_session(client, paper)
    bad = [
        dwell(paper["arxiv_id"], sid, 0, 5),
        dwell(paper["arxiv_id"], "no-such-session", 1, 5),
        {**dwell(paper["arxiv_id"], sid, 1, 5), "type": "status_changed"},
    ]
    assert send(client, *bad)["stored"] == 0


def test_return_visits(client, add_paper, monkeypatch):
    paper = add_paper()
    open_session(client, paper)  # first visit
    open_session(client, paper)  # 0 min later: same visit
    assert client.get(f"/api/papers/{paper['key']}").json()["metrics"]["visits"] == 1

    later = utcnow() + timedelta(minutes=31)
    monkeypatch.setattr(metrics, "utcnow", lambda: later)
    resp = client.post(f"/api/papers/{paper['key']}/sessions").json()
    assert resp["is_return_visit"] is True
    assert resp["paper"]["metrics"]["visits"] == 2


# --- status rules ----------------------------------------------------------


def test_opening_marks_reading(client, add_paper):
    paper = add_paper()
    resp = client.post(f"/api/papers/{paper['key']}/sessions").json()
    assert resp["paper"]["status"] == "reading"
    assert resp["paper"]["status_source"] == "auto"


def test_auto_read_at_80_percent(client, add_paper, settings):
    paper = add_paper(num_pages=20)
    sid = open_session(client, paper)
    send(client, *[dwell(paper["arxiv_id"], sid, p, 6) for p in range(1, 16)])  # 15/20
    assert client.get(f"/api/papers/{paper['key']}").json()["status"] == "reading"

    out = send(client, dwell(paper["arxiv_id"], sid, 16, 6))  # 16/20 = 80%
    state = out["papers"][0]
    assert state["status"] == "read"
    assert state["status_source"] == "auto"
    assert state["rating_prompted"] is False

    types = [e["type"] for e in log_lines(settings)]
    assert types.count("completion_reached") == 1
    send(client, dwell(paper["arxiv_id"], sid, 17, 6))
    assert [e["type"] for e in log_lines(settings)].count("completion_reached") == 1


def test_auto_read_never_overrides_manual_abandoned(client, add_paper):
    paper = add_paper(num_pages=5)
    sid = open_session(client, paper)
    client.patch(f"/api/papers/{paper['key']}", json={"status": "abandoned"})
    out = send(client, *[dwell(paper["arxiv_id"], sid, p, 10) for p in range(1, 6)])
    assert out["papers"][0]["status"] == "abandoned"


def test_reopening_a_read_paper_keeps_it_read(client, add_paper):
    paper = add_paper()
    client.patch(f"/api/papers/{paper['key']}", json={"status": "read"})
    resp = client.post(f"/api/papers/{paper['key']}/sessions").json()
    assert resp["paper"]["status"] == "read"


def test_manual_status_validation(client, add_paper):
    paper = add_paper()
    assert client.patch(f"/api/papers/{paper['key']}", json={"status": "loved"}).status_code == 400


# --- rating, tags, filtering ----------------------------------------------


def test_rating_set_change_clear(client, add_paper, settings):
    paper = add_paper()
    key = paper["key"]
    assert client.patch(f"/api/papers/{key}", json={"rating": 4, "prompted": True}).json()["rating"] == 4
    assert client.patch(f"/api/papers/{key}", json={"rating": 2}).json()["rating"] == 2
    assert client.patch(f"/api/papers/{key}", json={"rating": None}).json()["rating"] is None
    assert client.patch(f"/api/papers/{key}", json={"rating": 6}).status_code == 422

    ratings = [e["payload"] for e in log_lines(settings) if e["type"] == "rating_set"]
    assert ratings == [
        {"from": None, "to": 4, "prompted": True},
        {"from": 4, "to": 2, "prompted": False},
        {"from": 2, "to": None, "prompted": False},
    ]


def test_rating_prompted_flag(client, add_paper):
    paper = add_paper()
    assert client.patch(f"/api/papers/{paper['key']}", json={"rating_prompted": True}).json()["rating_prompted"]


def test_tags_are_normalised_and_removable(client, add_paper):
    paper = add_paper()
    key = paper["key"]
    client.put(f"/api/papers/{key}/tags/  Reward%20Models ")
    client.put(f"/api/papers/{key}/tags/reward models")  # duplicate after normalising
    client.put(f"/api/papers/{key}/tags/rlhf")
    assert client.get(f"/api/papers/{key}").json()["tags"] == ["reward models", "rlhf"]
    assert client.get("/api/tags").json() == [
        {"name": "reward models", "count": 1},
        {"name": "rlhf", "count": 1},
    ]
    client.delete(f"/api/papers/{key}/tags/rlhf")
    assert client.get(f"/api/papers/{key}").json()["tags"] == ["reward models"]
    assert [t["name"] for t in client.get("/api/tags").json()] == ["reward models"]


def test_filters_and_sort(client, add_paper):
    a = add_paper("2401.00001")
    b = add_paper("2401.00002")
    c = add_paper("2401.00003")
    client.put(f"/api/papers/{a['key']}/tags/rlhf")
    client.put(f"/api/papers/{a['key']}/tags/taste")
    client.put(f"/api/papers/{b['key']}/tags/rlhf")
    client.patch(f"/api/papers/{a['key']}", json={"rating": 5, "status": "read"})
    client.patch(f"/api/papers/{b['key']}", json={"rating": 3})

    def ids(**params):
        return [p["arxiv_id"] for p in client.get("/api/papers", params=params).json()]

    assert ids(tag="rlhf", sort="added") == ["2401.00002", "2401.00001"]
    assert ids(tag=["rlhf", "taste"]) == ["2401.00001"]
    assert ids(status="read") == ["2401.00001"]
    assert ids(status=["unread"], sort="added") == ["2401.00003", "2401.00002"]
    assert ids(min_rating=4) == ["2401.00001"]
    assert ids(sort="rating") == ["2401.00001", "2401.00002", "2401.00003"]

    open_session(client, c)
    assert ids()[0] == "2401.00003"  # most recently opened first


# --- feedback forwarding ---------------------------------------------------


def test_every_event_reaches_feedback_log_once(client, add_paper, feedback, settings):
    paper = add_paper()
    sid = open_session(client, paper)

    feedback.down = True
    send(client, dwell(paper["arxiv_id"], sid, 1, 10, event_id="while-down-1"))
    client.put(f"/api/papers/{paper['key']}/tags/later")
    assert "while-down-1" not in {e["event_id"] for e in log_lines(settings)}

    feedback.down = False
    send(client, dwell(paper["arxiv_id"], sid, 2, 10, event_id="after-up-1"))
    lines = log_lines(settings)
    ids = [e["event_id"] for e in lines]
    assert len(ids) == len(set(ids))
    assert {"while-down-1", "after-up-1"} <= set(ids)
    assert {e["type"] for e in lines} >= {"paper_added", "paper_opened", "status_changed", "page_dwell", "tag_added"}
    assert all(e["user_id"] == 1 for e in lines)


def test_stub_dedupes_resent_batches(client):
    body = {"events": [{"event_id": "x1", "type": "page_dwell"}]}
    assert client.post("/feedback/events", json=body).json() == {"accepted": ["x1"]}
    assert client.post("/feedback/events", json=body).json() == {"accepted": ["x1"]}
    assert client.get("/feedback/health").json() == {"ok": True}


def test_paper_closed_ends_session(client, add_paper):
    paper = add_paper()
    sid = open_session(client, paper)
    closed = {**dwell(paper["arxiv_id"], sid, 1, 1), "type": "paper_closed", "payload": {"active_ms": 1000}}
    assert send(client, closed)["stored"] == 1


def test_config_endpoint(client):
    assert client.get("/api/config").json()["auto_read_threshold"] == 0.8


def test_same_batch_racing_itself_is_not_an_error(client, add_paper, monkeypatch):
    """Another request stores the event between our duplicate check and our commit."""
    from sqlmodel import Session

    from app import events
    from app.models import Event

    paper = add_paper()
    sid = open_session(client, paper)
    event = dwell(paper["arxiv_id"], sid, 1, 9, event_id="raced-event-1")
    real = metrics.record_event
    raced = []

    def record_then_lose_race(db, user_id, arxiv_id, type, payload, session_id=None, ts=None, event_id=None, config=None):
        out = real(db, user_id, arxiv_id, type, payload, session_id, ts, event_id, config)
        if out is not None and not raced:
            raced.append(1)
            with Session(client.app.state.engine) as other:
                other.add(Event(event_id=event_id, user_id=user_id, arxiv_id=arxiv_id, type=type,
                                payload=payload, session_id=session_id, ts=ts))
                other.commit()
        return out

    monkeypatch.setattr(events.metrics, "record_event", record_then_lose_race)
    assert send(client, event)["stored"] == 0  # the other request stored it
    assert raced
    assert client.get(f"/api/papers/{paper['key']}").json()["metrics"]["total_ms"] == 9000
