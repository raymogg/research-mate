from sqlmodel import Session, select

from app.models import Event, Login

from .conftest import PASSWORD, dwell
from .test_api import open_session, send


def signup(c, username="bob", password=PASSWORD, code="letmein"):
    return c.post("/api/auth/signup", json={"username": username, "password": password, "invite_code": code})


def test_api_requires_sign_in(anon):
    for method, path in [
        ("get", "/api/papers"),
        ("post", "/api/papers"),
        ("get", "/api/papers/2401.12345/pdf"),
        ("post", "/api/events"),
        ("get", "/api/tags"),
        ("get", "/api/admin/overview"),
        ("get", "/api/auth/me"),
    ]:
        assert getattr(anon, method)(path).status_code == 401, path
    assert anon.get("/api/config").json()["signup_open"] is True


def test_signup_needs_the_invite_code(anon):
    assert signup(anon, code="nope").status_code == 403
    assert signup(anon, password="short").status_code == 400
    assert signup(anon, username="no spaces").status_code == 400
    resp = signup(anon, username="  Bob ")
    assert resp.status_code == 201 and resp.json() == {"id": 1, "username": "bob", "is_admin": False}
    assert anon.get("/api/auth/me").json()["username"] == "bob"  # signed in straight away
    assert signup(anon, username="BOB").json()["detail"] == "That username is taken"


def test_signup_closed_without_code(anon, app):
    app.state.settings.invite_code = ""
    assert signup(anon).status_code == 403
    assert anon.get("/api/config").json()["signup_open"] is False


def test_login_logout(anon, app):
    signup(anon)
    anon.post("/api/auth/logout")
    assert anon.get("/api/auth/me").status_code == 401
    assert anon.post("/api/auth/login", json={"username": "bob", "password": "wrong"}).status_code == 401
    assert anon.post("/api/auth/login", json={"username": "nobody", "password": "wrong"}).status_code == 401
    assert anon.post("/api/auth/login", json={"username": "Bob", "password": PASSWORD}).status_code == 200
    assert anon.get("/api/auth/me").status_code == 200
    with Session(app.state.engine) as db:
        token = anon.cookies.get("taste_login")
        stored = [login.token_hash for login in db.exec(select(Login))]
        assert token not in stored and len(stored) == 1  # only the hash is kept


def test_admin_only_for_admins(make_client):
    assert make_client("carol").get("/api/admin/overview").status_code == 403


def test_users_are_isolated(client, make_client, app):
    bob = make_client("bob")
    mine = client.post("/api/papers", json={"input": "2401.12345"}).json()
    client.put(f"/api/papers/{mine['key']}/tags/private")
    sid = open_session(client, mine)
    send(client, dwell(mine["arxiv_id"], sid, 1, 30))

    assert bob.get("/api/papers").json() == []
    assert bob.get("/api/tags").json() == []
    assert bob.get(f"/api/papers/{mine['key']}").status_code == 404
    assert bob.patch(f"/api/papers/{mine['key']}", json={"rating": 1}).status_code == 404
    # Bob can't write events into Alice's session, even for a paper he also has.
    theirs = bob.post("/api/papers", json={"input": "2401.12345"}).json()
    assert theirs["metrics"]["total_ms"] == 0 and theirs["tags"] == [] and theirs["status"] == "unread"
    assert send(bob, dwell(mine["arxiv_id"], sid, 2, 30))["stored"] == 0
    assert client.get(f"/api/papers/{mine['key']}").json()["metrics"]["total_ms"] == 30_000

    users = {u["username"]: u for u in client.get("/api/admin/users").json()}
    assert users["alice"]["papers"] == 1 and users["alice"]["total_active_ms"] == 30_000
    assert users["bob"]["papers"] == 1 and users["bob"]["total_active_ms"] == 0
    only_bob = client.get("/api/admin/overview", params={"user_id": users["bob"]["id"]}).json()["totals"]
    assert only_bob["papers"] == 1 and only_bob["events"] == 1 and only_bob["users"] == 2
    with Session(app.state.engine) as db:
        assert {e.user_id for e in db.exec(select(Event))} == {users["alice"]["id"], users["bob"]["id"]}


def test_second_user_reuses_metadata(client, make_client, monkeypatch):
    from app import arxiv

    client.post("/api/papers", json={"input": "2401.12345"})
    monkeypatch.setattr(arxiv, "fetch_metadata", lambda _: (_ for _ in ()).throw(AssertionError("refetched")))
    resp = make_client("bob").post("/api/papers", json={"input": "2401.12345"})
    assert resp.status_code == 201 and resp.json()["title"] == "Paper 2401.12345"


def test_feedback_log_carries_user_ids(client, make_client, settings):
    import json

    make_client("bob").post("/api/papers", json={"input": "2401.12345"})
    client.post("/api/papers", json={"input": "2401.99999"})
    lines = [json.loads(line) for line in settings.feedback_log.read_text().splitlines()]
    assert {(e["arxiv_id"], e["user_id"]) for e in lines} == {("2401.12345", 2), ("2401.99999", 1)}


def test_feedback_stub_rejects_outside_callers(app):
    from fastapi.testclient import TestClient

    outsider = TestClient(app, client=("203.0.113.9", 5000))
    assert outsider.post("/feedback/events", json={"events": []}).status_code == 403
