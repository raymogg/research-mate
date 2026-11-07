"""Accounts, cookie logins and export API tokens.

Accounts sign in with Google or GitHub only (see oauth.py); there are no
passwords. The login cookie and API tokens are random; the database stores only
their SHA-256, so a leaked database can't be replayed.
"""

import hashlib
import re
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from .db import get_db
from .models import ApiToken, Login, User, utcnow

COOKIE = "taste_login"
USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
# password_hash of every account. Passwords are gone; older accounts may still
# hold a hash, which nothing reads.
NO_PASSWORD = "!"
TOKEN_PREFIX = "trk_"


def normalise_username(username: str) -> str:
    name = username.strip().lower()
    if not USERNAME_RE.match(name):
        raise ValueError("Usernames are 3-32 characters: letters, digits, '.', '_' or '-'")
    return name


def create_user(db: Session, username: str, is_admin: bool = False) -> User:
    """An account with no way to sign in yet (tests). Raises ValueError for a bad or taken username."""
    name = normalise_username(username)
    if db.exec(select(User).where(User.username == name)).first():
        raise ValueError("That username is taken")
    user = User(username=name, password_hash=NO_PASSWORD, is_admin=is_admin)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def free_username(db: Session, hint: str) -> str:
    """A valid, unused username close to `hint` (a GitHub login or an email's local part)."""
    base = re.sub(r"[^a-z0-9_.-]+", "-", hint.strip().lower()).strip("-.")[:28] or "reader"
    base = base if len(base) >= 3 else f"{base}-reader"
    name, n = base, 1
    while db.exec(select(User).where(User.username == name)).first():
        n += 1
        name = f"{base}-{n}"
    return name


def create_provider_user(db: Session, hint: str) -> User:
    user = User(username=free_username(db, hint), password_hash=NO_PASSWORD)
    db.add(user)
    db.flush()  # assigns the id; the caller commits with the identity
    return user


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_login(db: Session, user: User, days: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(Login(token_hash=_token_hash(token), user_id=user.id, expires_at=utcnow() + timedelta(days=days)))
    db.commit()
    return token


def end_login(db: Session, token: str | None) -> None:
    if token and (login := db.get(Login, _token_hash(token))):
        db.delete(login)
        db.commit()


def user_for_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    login = db.get(Login, _token_hash(token))
    if login is None or login.expires_at <= utcnow():
        return None
    return db.get(User, login.user_id)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = user_for_token(db, request.cookies.get(COOKIE))
    if user is None:
        raise HTTPException(401, "Sign in required")
    return user


def create_api_token(db: Session, user: User, name: str) -> tuple[str, ApiToken]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(token_hash=_token_hash(token), user_id=user.id, name=name)
    db.add(row)
    db.commit()
    db.refresh(row)
    return token, row


def user_for_api_token(db: Session, token: str) -> User | None:
    row = db.exec(select(ApiToken).where(ApiToken.token_hash == _token_hash(token))).first()
    if row is None:
        return None
    row.last_used_at = utcnow()
    db.add(row)
    db.commit()
    return db.get(User, row.user_id)


def export_user(request: Request, db: Session = Depends(get_db)) -> User:
    """The signed-in user, or the owner of the API token in `Authorization: Bearer`.

    Only the export endpoints accept tokens; everything else needs the cookie.
    """
    header = request.headers.get("authorization", "")
    if header:
        scheme, _, token = header.partition(" ")
        user = user_for_api_token(db, token.strip()) if scheme.lower() == "bearer" else None
        if user is None:
            raise HTTPException(401, "Invalid or revoked API token", headers={"WWW-Authenticate": "Bearer"})
        return user
    return current_user(request, db)


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Admins only")
    return user
