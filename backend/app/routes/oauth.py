"""Routes for "Continue with Google / GitHub". The flow is described in oauth.py.

Three modes, chosen when the browser starts:
- login: a known account signs in; an unknown one is turned away.
- signup: as login, but an unknown account becomes a new user. Needs the
  invite code, checked here before leaving for the provider.
- link: attaches the account to the signed-in user (from the profile page).

The callback always ends in a redirect: to the library, or to the profile page
for links, with `?auth_error=<code>` when something went wrong.
"""

import hashlib
import secrets
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from .. import auth, oauth
from ..db import get_db
from ..models import Identity, OAuthAttempt, User, utcnow
from .auth import _sign_in

router = APIRouter(prefix="/api/auth/oauth")

COOKIE = "taste_oauth"
ATTEMPT_MINUTES = 10


def _hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _provider(request: Request, name: str) -> tuple[oauth.Provider, tuple[str, str]]:
    provider = oauth.PROVIDERS.get(name)
    client = request.app.state.settings.oauth_client(name)
    if provider is None or client is None:
        raise HTTPException(404, f"Sign-in with {name} isn't set up")
    return provider, client


def _redirect_uri(request: Request, name: str) -> str:
    base = request.app.state.settings.public_url or str(request.base_url)
    return f"{base.rstrip('/')}/api/auth/oauth/{name}/callback"


class Start(BaseModel):
    mode: Literal["login", "signup", "link"] = "login"
    invite_code: str = Field(default="", max_length=256)


@router.post("/{name}/start")
def start(name: str, body: Start, request: Request, db: Session = Depends(get_db)):
    """Returns the provider URL to send the browser to, and sets the state cookie."""
    provider, (client_id, _) = _provider(request, name)
    settings = request.app.state.settings
    user_id = None
    if body.mode == "link":
        user_id = auth.current_user(request, db).id
    elif body.mode == "signup":
        if not settings.invite_code:
            raise HTTPException(403, "Sign-up is closed")
        if not secrets.compare_digest(body.invite_code.strip().encode(), settings.invite_code.encode()):
            raise HTTPException(403, "That invite code isn't right")
    state, verifier = secrets.token_urlsafe(32), oauth.new_verifier()
    db.add(OAuthAttempt(
        state_hash=_hash(state), provider=name, mode=body.mode, verifier=verifier, user_id=user_id,
        expires_at=utcnow() + timedelta(minutes=ATTEMPT_MINUTES),
    ))
    db.commit()
    url = oauth.authorize_url(provider, client_id, _redirect_uri(request, name), state, verifier)
    # A JSON answer rather than a redirect, so the invite code travels in a POST
    # body; the browser then navigates to `url` itself
    resp = JSONResponse({"url": url})
    resp.set_cookie(
        COOKIE, state, max_age=ATTEMPT_MINUTES * 60, httponly=True, secure=settings.cookie_secure, samesite="lax",
        path="/api/auth/oauth",
    )
    return resp


@router.get("/{name}/callback")
def callback(
    name: str,
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    cookie = request.cookies.get(COOKIE, "")
    attempt = db.get(OAuthAttempt, _hash(state)) if state else None
    # Cookie and URL must agree: the browser that started is the one that came back
    valid = (
        attempt is not None
        and secrets.compare_digest(cookie.encode(), state.encode())
        and attempt.provider == name
        and attempt.expires_at > utcnow()
    )
    if attempt is not None:
        db.delete(attempt)  # single use
        db.commit()
    mode = attempt.mode if attempt else "login"

    def done(path: str, err: str | None = None) -> RedirectResponse:
        resp = RedirectResponse(f"{path}?auth_error={err}" if err else path, status_code=303)
        resp.delete_cookie(COOKIE, path="/api/auth/oauth")
        return resp

    home = "/profile" if mode == "link" else "/"
    if not valid:
        return done(home, "expired")
    if error or not code:
        return done(home, "cancelled")
    provider, client = _provider(request, name)
    try:
        profile = oauth.fetch_profile(provider, client, code, attempt.verifier, _redirect_uri(request, name))
    except oauth.OAuthError:
        return done(home, "provider")

    identity = db.get(Identity, (name, profile.subject))
    if mode == "link":
        if identity is not None and identity.user_id != attempt.user_id:
            return done(home, "taken")
        identity = identity or Identity(provider=name, subject=profile.subject, user_id=attempt.user_id)
        identity.email, identity.last_used_at = profile.email, utcnow()
        db.add(identity)
        db.commit()
        return done(home)

    if identity is None:
        if mode != "signup":
            return done(home, "no_account")
        user = auth.create_provider_user(db, profile.username_hint)
        identity = Identity(provider=name, subject=profile.subject, user_id=user.id)
    else:
        user = db.get(User, identity.user_id)
    identity.email, identity.last_used_at = profile.email, utcnow()
    db.add(identity)
    db.commit()
    resp = done(home)
    _sign_in(request, resp, db, user)
    return resp
