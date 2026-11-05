from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

STATUSES = ("unread", "reading", "read", "skimmed", "abandoned")

# Bump when the meaning or shape of any event changes, and note what changed:
#   1: original events (rows from before versioning)
#   2: every event records the thresholds in force (`config`); page_dwell adds
#      `band_ms`; new types text_copied, paper_feedback, feedback_skipped
#   3: new types impression (no session) and zoom_changed; paper_added adds
#      `source`; paper_opened adds `context` (how the user got there, device,
#      viewport, local time)
#   4: paper_added adds `intent` ("open", or "later" when saved without opening)
SCHEMA_VERSION = 4

# none: never asked (papers first opened before feedback existed);
# pending: opened once, waiting for answers; done / skipped: asked
FEEDBACK_STATES = ("none", "pending", "done", "skipped")

# Where the user found a paper they added (asked, optional). "suggested" is
# reserved for papers our own recommender puts in the library.
DISCOVERY_SOURCES = ("colleague", "social", "search", "citation", "feed", "other")

# Why a paper was added: to open it now, or saved to the read-later list. A
# saved paper isn't asked about until its first open, like any other.
ADD_INTENTS = ("open", "later")


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None) -> str | None:
    """UTC timestamp as ISO 8601 with a Z suffix."""
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)  # stored lowercased
    password_hash: str  # "!" when the account signs in only through Google or GitHub
    is_admin: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Login(SQLModel, table=True):
    """A signed-in browser. The cookie holds the token; we keep only its hash."""

    __tablename__ = "logins"

    token_hash: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime


class Identity(SQLModel, table=True):
    """A Google or GitHub account that signs in as a user."""

    __tablename__ = "identities"

    provider: str = Field(primary_key=True)  # "google" or "github"
    subject: str = Field(primary_key=True)  # the provider's stable account id
    user_id: int = Field(foreign_key="users.id", index=True)
    email: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime = Field(default_factory=utcnow)


class OAuthAttempt(SQLModel, table=True):
    """A sign-in sent to a provider and not yet back. Used once, then deleted."""

    __tablename__ = "oauth_attempts"

    state_hash: str = Field(primary_key=True)
    provider: str
    mode: str  # "login", "signup" or "link"
    verifier: str  # PKCE code verifier
    user_id: int | None = None  # who is linking, for mode "link"
    expires_at: datetime


class ApiToken(SQLModel, table=True):
    """A personal key for the export API. Shown once; we keep only its hash."""

    __tablename__ = "api_tokens"

    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(unique=True, index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    name: str
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime | None = None


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
    feedback_state: str = Field(default="none", sa_column_kwargs={"server_default": "none"})
    source: str | None = None  # one of DISCOVERY_SOURCES, or None if not given (v3+)
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
    # How the session started and on what screen; see SessionContext. None before v3.
    context: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class Event(SQLModel, table=True):
    __tablename__ = "events"

    event_id: str = Field(primary_key=True)  # idempotency key
    user_id: int = Field(index=True)
    session_id: str | None = Field(default=None, index=True)
    arxiv_id: str = Field(index=True)
    type: str = Field(index=True)
    ts: datetime
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    schema_version: int = Field(default=SCHEMA_VERSION, sa_column_kwargs={"server_default": "1"})
    # Thresholds that shaped this event (idle cutoff, viewed-page minimum, ...), so
    # old events stay interpretable after the settings change. None before v2.
    config: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    reported_at: datetime | None = Field(default=None, index=True)
