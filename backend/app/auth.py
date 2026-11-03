"""Username/password accounts, invite-code sign-up and cookie logins.

Passwords are hashed with stdlib scrypt. The login cookie holds a random token;
the database stores only its SHA-256, so a leaked database can't be replayed.
"""

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from .db import get_db
from .models import Login, User, utcnow

COOKIE = "taste_login"
USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
MIN_PASSWORD = 8
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    return hmac.compare_digest(actual.hex(), digest)


# Compared against when the username doesn't exist, so timing doesn't reveal which usernames do.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def normalise_username(username: str) -> str:
    name = username.strip().lower()
    if not USERNAME_RE.match(name):
        raise ValueError("Usernames are 3-32 characters: letters, digits, '.', '_' or '-'")
    return name


def check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Passwords need at least {MIN_PASSWORD} characters")


def create_user(db: Session, username: str, password: str, is_admin: bool = False) -> User:
    """Raises ValueError for a bad or taken username or a weak password."""
    name = normalise_username(username)
    check_new_password(password)
    if db.exec(select(User).where(User.username == name)).first():
        raise ValueError("That username is taken")
    user = User(username=name, password_hash=hash_password(password), is_admin=is_admin)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.exec(select(User).where(User.username == username.strip().lower())).first()
    ok = check_password(password, user.password_hash if user else _DUMMY_HASH)
    return user if user and ok else None


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


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Admins only")
    return user
