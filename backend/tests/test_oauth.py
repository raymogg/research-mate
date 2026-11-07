from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import oauth
from app.models import OAuthAttempt, utcnow


@pytest.fixture
def accounts(app, monkeypatch):
    """Fake provider accounts by code: the callback's `code` picks who signed in."""
    s = app.state.settings
    s.google_client_id, s.google_client_secret = "gid", "gsecret"
    s.github_client_id, s.github_client_secret = "hid", "hsecret"
    known = {
        "ada": oauth.Profile(subject="g-1", email="ada@example.com", username_hint="ada"),
        "ada2": oauth.Profile(subject="g-2", email="ada@example.com", username_hint="ada"),
        "gh": oauth.Profile(subject="77", email=None, username_hint="Ada-L"),
    }
    seen = []

    def fake(provider, client, code, verifier, redirect_uri):
        seen.append((provider.name, client, verifier, redirect_uri))
        if code not in known:
            raise oauth.OAuthError("bad code")
        return known[code]

    monkeypatch.setattr(oauth, "fetch_profile", fake)
    return seen


def start(c, provider="google", mode="login", code=""):
    return c.post(f"/api/auth/oauth/{provider}/start", json={"mode": mode, "invite_code": code})


def sign_in(c, code, provider="google", mode="login", invite="letmein"):
    resp = start(c, provider, mode, invite)
    assert resp.status_code == 200, resp.text
    state = parse_qs(urlparse(resp.json()["url"]).query)["state"][0]
    return c.get(f"/api/auth/oauth/{provider}/callback?code={code}&state={state}", follow_redirects=False)


def test_providers_show_once_configured(anon, accounts, app):
    assert anon.get("/api/config").json()["providers"] == ["google", "github"]
    app.state.settings.github_client_secret = ""
    assert anon.get("/api/config").json()["providers"] == ["google"]
    assert start(anon, "github").status_code == 404
    assert start(anon, "myspace").status_code == 404


def test_authorize_url_uses_pkce_and_the_public_url(anon, accounts, app):
    app.state.settings.public_url = "https://reader.example/"
    url = urlparse(start(anon).json()["url"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com"
    assert q["redirect_uri"] == "https://reader.example/api/auth/oauth/google/callback"
    assert q["code_challenge_method"] == "S256" and q["client_id"] == "gid" and q["scope"] == "openid email profile"


def test_signup_needs_invite_then_signs_in(anon, accounts, app):
    assert start(anon, mode="signup", code="nope").status_code == 403
    assert sign_in(anon, "ada", mode="login").headers["location"] == "/?auth_error=no_account"
    resp = sign_in(anon, "ada", mode="signup")
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    me = anon.get("/api/me").json()
    assert me["username"] == "ada" and me["has_password"] is False
    assert me["identities"][0]["provider"] == "google" and me["identities"][0]["email"] == "ada@example.com"
    # Same Google account again, from a fresh browser: same user, no invite needed
    other = TestClient(app)
    assert sign_in(other, "ada").headers["location"] == "/"
    assert other.get("/api/me").json()["id"] == me["id"]
    # A second Google account with the same email is a different account, not a takeover
    third = TestClient(app)
    sign_in(third, "ada2", mode="signup")
    assert third.get("/api/me").json()["username"] == "ada-2"
    # GitHub logins become valid usernames
    gh = TestClient(app)
    sign_in(gh, "gh", provider="github", mode="signup")
    assert gh.get("/api/me").json()["username"] == "ada-l"
    app.state.settings.invite_code = ""
    assert start(TestClient(app), mode="signup", code="").status_code == 403


def test_callback_rejects_forged_reused_or_expired_state(anon, accounts, app):
    sign_in(anon, "ada", mode="signup")
    attacker = TestClient(app)
    resp = start(attacker)
    state = parse_qs(urlparse(resp.json()["url"]).query)["state"][0]
    # The victim's browser lands on a callback the attacker started: no matching cookie
    victim = TestClient(app)
    r = victim.get(f"/api/auth/oauth/google/callback?code=ada&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/?auth_error=expired" and victim.get("/api/me").status_code == 401
    # The attempt is spent even for its own browser now
    r = attacker.get(f"/api/auth/oauth/google/callback?code=ada&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/?auth_error=expired"
    # Started for google, returned to github
    resp = start(attacker)
    state = parse_qs(urlparse(resp.json()["url"]).query)["state"][0]
    r = attacker.get(f"/api/auth/oauth/github/callback?code=gh&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/?auth_error=expired"
    # Expired
    resp = start(attacker)
    state = parse_qs(urlparse(resp.json()["url"]).query)["state"][0]
    with Session(app.state.engine) as db:
        for a in db.exec(select(OAuthAttempt)):
            a.expires_at = utcnow() - timedelta(seconds=1)
            db.add(a)
        db.commit()
    r = attacker.get(f"/api/auth/oauth/google/callback?code=ada&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/?auth_error=expired" and attacker.get("/api/me").status_code == 401


def test_provider_errors_and_cancelling(anon, accounts):
    assert sign_in(anon, "bad-code", mode="signup").headers["location"] == "/?auth_error=provider"
    resp = start(anon)
    state = parse_qs(urlparse(resp.json()["url"]).query)["state"][0]
    r = anon.get(f"/api/auth/oauth/google/callback?error=access_denied&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/?auth_error=cancelled"


def test_link_and_unlink_from_the_profile(client, accounts, anon, app):
    assert start(anon, mode="link").status_code == 401  # linking needs a signed-in user
    resp = sign_in(client, "gh", provider="github", mode="link")
    assert resp.headers["location"] == "/profile"
    assert client.get("/api/auth/me").json()["username"] == "alice"  # still alice
    assert [i["provider"] for i in client.get("/api/me").json()["identities"]] == ["github"]
    # GitHub now signs in as alice
    browser = TestClient(app)
    sign_in(browser, "gh", provider="github")
    assert browser.get("/api/auth/me").json()["username"] == "alice"

    # Someone else's Google account can't be linked to alice
    ada = TestClient(app)
    sign_in(ada, "ada", mode="signup")
    assert sign_in(client, "ada", mode="link").headers["location"] == "/profile?auth_error=taken"

    # ada has only Google, so she can't remove it; alice has a password, so she can
    assert ada.delete("/api/me/identities/google").status_code == 400
    assert client.delete("/api/me/identities/google").status_code == 404
    assert client.delete("/api/me/identities/github").status_code == 204
    assert client.get("/api/me").json()["identities"] == []
