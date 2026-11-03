from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

STATUSES = ("unread", "reading", "read", "skimmed", "abandoned")


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None) -> str | None:
    """UTC timestamp as ISO 8601 with a Z suffix."""
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)  # stored lowercased
    password_hash: str
    is_admin: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Login(SQLModel, table=True):
    """A signed-in browser. The cookie holds the token; we keep only its hash."""

    __tablename__ = "logins"

    token_hash: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime


class Paper(SQLModel, table=True):
    """One paper in one user's library. Metadata is copied per user to keep queries simple."""

    __tablename__ = "papers"

    user_id: int = Field(foreign_key="users.id", primary_key=True)
    arxiv_id: str = Field(primary_key=True)  # version-less, e.g. "2401.12345"
    version: str | None = None  # e.g. "v2"
    title: str
    authors: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    abstract: str = ""
    categories: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    published_at: datetime | None = None
    num_pages: int | None = None
    status: str = "unread"
    status_source: str = "auto"
    rating: int | None = None
    rating_prompted: bool = False
    added_at: datetime = Field(default_factory=utcnow)
    last_opened_at: datetime | None = None


class PaperTag(SQLModel, table=True):
    __tablename__ = "paper_tags"

    user_id: int = Field(primary_key=True)
    arxiv_id: str = Field(primary_key=True)
    tag: str = Field(primary_key=True)
    added_at: datetime = Field(default_factory=utcnow)


class ReadingSession(SQLModel, table=True):
    __tablename__ = "sessions"

    session_id: str = Field(primary_key=True)
    user_id: int = Field(index=True)
    arxiv_id: str = Field(index=True)
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
    active_ms: int = 0
    is_return_visit: bool = False


class Event(SQLModel, table=True):
    __tablename__ = "events"

    event_id: str = Field(primary_key=True)  # idempotency key
    user_id: int = Field(index=True)
    session_id: str | None = Field(default=None, index=True)
    arxiv_id: str = Field(index=True)
    type: str = Field(index=True)
    ts: datetime
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    reported_at: datetime | None = Field(default=None, index=True)
