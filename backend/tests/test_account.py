from sqlalchemy import func, select
from sqlmodel import Session, SQLModel

from app.account import user_tables
from app.models import Identity, User

from .conftest import dwell
from .test_api import log_lines, open_session, send


def read_a_paper(c, arxiv_id="2401.12345", tag="rl"):
    paper = c.post("/api/papers", json={"input": arxiv_id}).json()
    c.patch(f"/api/papers/{paper['key']}", json={"num_pages": 4})
    assert c.put(f"/api/papers/{paper['key']}/tags/{tag}").status_code < 300
    sid = open_session(c, paper)
    send(c, dwell(arxiv_id, sid, 1, 30), dwell(arxiv_id, sid, 2, 20))
    return paper


def rows_for(app, user_id: int) -> dict[str, int]:
    with Session(app.state.engine) as db:
        conn = db.connection()
        out = {t.name: conn.execute(select(func.count()).select_from(t).where(t.c.user_id == user_id)).scalar()
               for t in user_tables()}
        out["users"] = conn.execute(select(func.count()).select_from(User).where(User.id == user_id)).scalar()
        return out


def token_for(c, name="script") -> str:
    resp = c.post("/api/me/tokens", json={"name": name})
    assert resp.status_code == 201
    return resp.json()["token"]


def test_profile_summarises_my_data(client):
    read_a_paper(client)
    me = client.get("/api/me").json()
    assert me["username"] == "alice" and me["identities"] == []
    assert me["data"]["papers"] == 1 and me["data"]["sessions"] == 1 and me["data"]["active_ms"] == 50_000
    assert me["data"]["events"] >= 3 and me["backup_retention_days"] == 14
    events = client.get("/api/me/events?limit=2").json()
    assert len(events) == 2 and events[0]["title"] == "Paper 2401.12345"
    assert len(client.get("/api/me/events?limit=2&offset=2").json()) >= 1


def test_export_holds_every_table_of_mine_and_no_secrets(client, make_client):
    read_a_paper(client)
    read_a_paper(make_client("bob"), "2402.00001", tag="bobs")
    token_for(client)
    resp = client.get("/api/me/export")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith('attachment; filename="researchmate-alice-')
    data = resp.json()
    assert data["user"]["username"] == "alice"
    for table in user_tables():
        if table.name != "oauth_attempts":
            assert table.name in data, table.name
    assert {p["arxiv_id"] for p in data["papers"]} == {"2401.12345"}
    assert [t["tag"] for t in data["paper_tags"]] == ["rl"]
    assert {e["user_id"] for e in data["events"]} == {data["user"]["id"]}
    text = resp.text
    assert "token_hash" not in text and "password_hash" not in text and "scrypt$" not in text
    assert "2402.00001" not in text and "bobs" not in text


def test_export_api_takes_a_token_and_only_ever_returns_its_owner(client, make_client, anon):
    read_a_paper(client)
    bob = make_client("bob")
    read_a_paper(bob, "2402.00001")
    alice_token, bob_token = token_for(client), token_for(bob)

    assert anon.get("/api/me/export").status_code == 401
    for header in ("Bearer nope", f"Basic {alice_token}", "Bearer ", alice_token):
        assert anon.get("/api/me/export", headers={"Authorization": header}).status_code == 401, header

    as_alice = anon.get("/api/me/export", headers={"Authorization": f"Bearer {alice_token}"}).json()
    as_bob = anon.get("/api/me/export", headers={"Authorization": f"Bearer {bob_token}"}).json()
    assert as_alice["user"]["username"] == "alice" and as_bob["user"]["username"] == "bob"
    assert [p["arxiv_id"] for p in as_bob["papers"]] == ["2402.00001"]
    # A token beats the cookie: bob's browser holding alice's token gets alice, never a mix
    mixed = bob.get("/api/me/export", headers={"Authorization": f"Bearer {alice_token}"}).json()
    assert mixed["user"]["username"] == "alice" and {p["arxiv_id"] for p in mixed["papers"]} == {"2401.12345"}
    # There's no way to name another user
    assert anon.get("/api/me/export?user_id=2", headers={"Authorization": f"Bearer {alice_token}"}).json()[
        "user"]["username"] == "alice"


def test_tokens_work_only_for_export_and_stop_when_revoked(client, anon):
    token = token_for(client)
    headers = {"Authorization": f"Bearer {token}"}
    for path in ("/api/me", "/api/papers", "/api/me/events", "/api/admin/overview"):
        assert anon.get(path, headers=headers).status_code == 401, path
    assert anon.post("/api/me/tokens", json={"name": "x"}, headers=headers).status_code == 401

    me = client.get("/api/me").json()
    assert [t["name"] for t in me["tokens"]] == ["script"] and "token" not in me["tokens"][0]
    assert anon.get("/api/me/export", headers=headers).status_code == 200
    assert client.get("/api/me").json()["tokens"][0]["last_used_at"] is not None
    tid = me["tokens"][0]["id"]
    assert client.delete(f"/api/me/tokens/{tid}").status_code == 204
    assert anon.get("/api/me/export", headers=headers).status_code == 401


def test_cant_revoke_someone_elses_token(client, make_client):
    bob = make_client("bob")
    token_for(bob)
    bob_tid = bob.get("/api/me").json()["tokens"][0]["id"]
    assert client.delete(f"/api/me/tokens/{bob_tid}").status_code == 404
    assert len(bob.get("/api/me").json()["tokens"]) == 1


def test_delete_account_removes_every_row_and_log_line(app, settings, client, make_client):
    read_a_paper(client)
    token_for(client)
    bob = make_client("bob")
    read_a_paper(bob, "2402.00001")
    alice_id = client.get("/api/me").json()["id"]
    with Session(app.state.engine) as db:
        db.add(Identity(provider="github", subject="42", user_id=alice_id))
        db.commit()
    assert any(e["user_id"] == alice_id for e in log_lines(settings))
    bob_before = rows_for(app, 2)

    def delete(c, confirm):
        return c.request("DELETE", "/api/me", json={"confirm": confirm})

    assert delete(client, "bob").status_code == 400
    assert delete(client, " Alice ").status_code == 204
    assert client.get("/api/me").status_code == 401  # signed out

    assert set(rows_for(app, alice_id).values()) == {0}
    assert not any(e["user_id"] == alice_id for e in log_lines(settings))
    assert rows_for(app, 2) == bob_before and bob.get("/api/papers").json()[0]["arxiv_id"] == "2402.00001"


def test_every_per_user_table_is_covered():
    # Deletion and export find tables by their user_id column. A per-user table
    # keyed some other way would be missed; this lists the ones we know of.
    names = {t.name for t in user_tables()}
    assert names == {"logins", "identities", "oauth_attempts", "api_tokens", "papers", "paper_tags", "sessions", "events"}
    assert {t.name for t in SQLModel.metadata.sorted_tables} - names == {"users"}
