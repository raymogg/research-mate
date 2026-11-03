"""Client event ingest and forwarding to the feedback endpoint."""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from . import metrics
from .config import Settings
from .config import EVENT_CONFIG_KEYS
from .models import DISCOVERY_SOURCES, SCHEMA_VERSION, Event, Paper, ReadingSession, iso, utcnow

log = logging.getLogger(__name__)

LEVELS = ("too_basic", "just_right", "too_advanced")
REASONS = ("off_topic", "poorly_written", "not_now", "great_method", "great_idea")
# Lists a paper can be shown in, and why a paper was put in one. "library" is the
# user's own list in the order they chose; the others are for the recommender.
LISTS = ("library", "suggested")
PURPOSES = ("library", "exploit", "explore", "elicit", "curriculum")
VIA = ("library", "pasted", "direct", "resume")

# Every event type we capture: where it comes from, what it means, its payload fields.
# The admin page and `app.cli` read this, so keep it in step with what is emitted.
EVENT_CATALOG: dict[str, dict] = {
    "paper_added": {
        "source": "server",
        "description": "A new paper was saved to the library",
        "payload": {
            "input": "the exact text pasted (URL or ID)",
            "source": f"where they found it: one of {', '.join(DISCOVERY_SOURCES)}, or null if not given (v3+)",
        },
    },
    "paper_opened": {
        "source": "server",
        "description": "A reading session started",
        "payload": {
            "is_return_visit": "true if the last activity on this paper was ≥ RETURN_VISIT_GAP_MIN ago",
            "context": "how the session started and on what screen, or null (v3+): "
            f"via.kind ({', '.join(VIA)}), via.slate_id and via.position when opened from a list, "
            "device (phone, tablet, desktop), viewport and screen [w, h] in CSS px, dpr, "
            "local_hour 0-23, weekday 1-7 (Monday = 1), tz, tz_offset_min",
        },
    },
    "impression": {
        "source": "client",
        "description": "A paper was on screen in a list: at least half its row visible for 1 s, "
        "counted once per paper per slate. Has no session (v3+)",
        "payload": {
            "list": f"which list: {', '.join(LISTS)}",
            "slate_id": "one load of the list; reopening a paper from it carries the same id in paper_opened",
            "position": "1-based position in the list",
            "slate_size": "papers in the list",
            "filters": "the sort and filters the list was shown with",
            "policy": "what ordered the list, e.g. library:last_opened or a model version",
            "purpose": f"why it was shown: {', '.join(PURPOSES)}",
            "score": "the policy's score for the paper, or null",
            "propensity": "probability the policy shows this paper here; 1 for a fixed order",
        },
    },
    "zoom_changed": {
        "source": "client",
        "description": "The reader's zoom changed with its buttons (v3+)",
        "payload": {"from": "previous zoom (1 = fit width)", "to": "new zoom", "page": "page in view"},
    },
    "page_dwell": {
        "source": "client",
        "description": "Active reading time on one page (tab visible, input within IDLE_CUTOFF_S)",
        "payload": {
            "page": "1-based page number",
            "active_ms": "active time since the last report for this page",
            "band_ms": "10 numbers: active ms each tenth of the page (top to bottom) was on screen. "
            "Several bands are usually visible at once, so they sum to more than active_ms (v2+)",
        },
    },
    "text_copied": {
        "source": "client",
        "description": "Text was copied from the PDF. Only the length is kept, never the text (v2+)",
        "payload": {"page": "page the selection starts on, or null", "chars": "characters copied"},
    },
    "paper_closed": {
        "source": "client",
        "description": "A reading session ended (left the reader, closed the tab, or came back after the gap)",
        "payload": {"active_ms": "the browser's total active time for the session"},
    },
    "status_changed": {
        "source": "server",
        "description": "Reading status changed",
        "payload": {"from": "previous status", "to": "new status", "source": "auto or manual"},
    },
    "completion_reached": {
        "source": "server",
        "description": "Pages viewed crossed AUTO_READ_THRESHOLD (once per paper)",
        "payload": {"pages_viewed": "pages with ≥ PAGE_VIEWED_MIN_S active time", "num_pages": "pages in the PDF"},
    },
    "rating_set": {
        "source": "server",
        "description": "Rating set, changed or cleared",
        "payload": {"from": "previous rating", "to": "1-5, or null when cleared", "prompted": "true if set from the post-completion prompt"},
    },
    "paper_feedback": {
        "source": "server",
        "description": "Answers to the questions asked after the first visit to a paper (v2+)",
        "payload": {
            "interest": "1-5 how interesting, or null",
            "value": "1-5 how valuable, or null",
            "novelty": "1-5 how novel, or null",
            "level": f"one of {', '.join(LEVELS)}, or null",
            "reasons": f"chips picked, any of {', '.join(REASONS)}",
            "context": "after_reading (asked on leaving the reader) or next_visit (tab was closed; asked on return)",
            "shown_ms": "time between showing the questions and answering",
        },
    },
    "feedback_skipped": {
        "source": "server",
        "description": "The post-visit questions were dismissed (v2+)",
        "payload": {
            "why": "skip (no answer) or didnt_read (said they didn't really read it)",
            "context": "as for paper_feedback",
            "shown_ms": "as for paper_feedback",
        },
    },
    "tag_added": {"source": "server", "description": "Tag added to a paper", "payload": {"tag": "normalised tag"}},
    "tag_removed": {"source": "server", "description": "Tag removed from a paper", "payload": {"tag": "normalised tag"}},
}

# Only these come from the browser; everything else is emitted server-side.
CLIENT_EVENT_TYPES = {t for t, spec in EVENT_CATALOG.items() if spec["source"] == "client"}
MAX_DWELL_MS = 60 * 60 * 1000
BANDS = 10
MAX_COPY_CHARS = 1_000_000
FORWARD_BATCH = 500
# Client events that happen outside a reading session.
SESSIONLESS_TYPES = {"impression"}

Sender = Callable[[dict], list[str]]


class ClientEvent(BaseModel):
    event_id: str = Field(min_length=8, max_length=64)
    type: str
    arxiv_id: str
    session_id: str | None = None  # None only for SESSIONLESS_TYPES
    ts: datetime
    payload: dict[str, Any] = {}
    # Events queued by a browser from before versioning carry neither field.
    schema_version: int = Field(default=1, ge=1, le=SCHEMA_VERSION)
    config: dict[str, Any] | None = None


def _as_utc(ts: datetime) -> datetime:
    return ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)


def _int(v: Any, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _num(v: Any, lo: float, hi: float) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and lo <= v <= hi


def _str(v: Any, lo: int, hi: int) -> bool:
    return isinstance(v, str) and lo <= len(v) <= hi


def _valid(e: ClientEvent) -> bool:
    if e.type not in CLIENT_EVENT_TYPES:
        return False
    if e.type == "page_dwell":
        bands = e.payload.get("band_ms")
        return (
            _int(e.payload.get("page"), 1, 100_000)
            and _int(e.payload.get("active_ms"), 1, MAX_DWELL_MS)
            and (
                bands is None
                or (isinstance(bands, list) and len(bands) == BANDS and all(_int(b, 0, MAX_DWELL_MS) for b in bands))
            )
        )
    if e.type == "text_copied":
        page = e.payload.get("page")
        return (page is None or _int(page, 1, 100_000)) and _int(e.payload.get("chars"), 1, MAX_COPY_CHARS)
    if e.type == "impression":
        p = e.payload
        filters, score, propensity = p.get("filters", {}), p.get("score"), p.get("propensity")
        return (
            p.get("list") in LISTS
            and _str(p.get("slate_id"), 8, 64)
            and _int(p.get("slate_size"), 1, 100_000)
            and _int(p.get("position"), 1, p["slate_size"])
            and _str(p.get("policy"), 1, 100)
            and p.get("purpose") in PURPOSES
            and (score is None or _num(score, -1e9, 1e9))
            and (propensity is None or (_num(propensity, 0, 1) and propensity > 0))
            and isinstance(filters, dict)
            and len(filters) <= 10
        )
    if e.type == "zoom_changed":
        p = e.payload
        return _num(p.get("from"), 0.1, 10) and _num(p.get("to"), 0.1, 10) and _int(p.get("page"), 1, 100_000)
    return True


def _client_config(e: ClientEvent) -> dict | None:
    """The thresholds the browser says it used: known numeric keys only."""
    if not e.config:
        return None
    out = {
        k: v for k, v in e.config.items()
        if k in EVENT_CONFIG_KEYS and isinstance(v, int | float) and not isinstance(v, bool)
    }
    return out or None


class Via(BaseModel):
    kind: Literal[VIA]
    slate_id: str | None = Field(default=None, min_length=8, max_length=64)
    position: int | None = Field(default=None, ge=1, le=100_000)


class SessionContext(BaseModel):
    """What the browser says about how a reading session started (D24). Every field is optional."""

    via: Via | None = None
    device: Literal["phone", "tablet", "desktop"] | None = None
    viewport: tuple[int, int] | None = None
    screen: tuple[int, int] | None = None
    dpr: float | None = Field(default=None, ge=0.25, le=10)
    local_hour: int | None = Field(default=None, ge=0, le=23)
    weekday: int | None = Field(default=None, ge=1, le=7)
    tz: str | None = Field(default=None, max_length=64)
    tz_offset_min: int | None = Field(default=None, ge=-840, le=840)

    @field_validator("viewport", "screen")
    @classmethod
    def _size(cls, v):
        if v is not None and not all(0 <= n <= 20_000 for n in v):
            raise ValueError("size out of range")
        return v


def session_context(raw: dict | None) -> dict | None:
    """The valid parts of a session context, or None. Bad context never stops a session opening."""
    if not raw:
        return None
    try:
        return SessionContext.model_validate(raw).model_dump(exclude_none=True) or None
    except ValidationError as exc:
        log.warning("ignoring invalid session context: %s", exc.errors()[:3])
        return None


def ingest(db: Session, settings: Settings, user_id: int, events: list[ClientEvent]) -> tuple[int, set[str]]:
    """Store one user's client events idempotently. Returns (stored count, affected paper ids)."""
    try:
        return _ingest(db, settings, user_id, events)
    except IntegrityError:
        # The same batch arrived twice at once (e.g. the reader's final flush and the
        # page-hide flush) and the other request stored it first. Redo the batch:
        # everything already stored is now skipped as a duplicate.
        db.rollback()
        return _ingest(db, settings, user_id, events)


def _ingest(db: Session, settings: Settings, user_id: int, events: list[ClientEvent]) -> tuple[int, set[str]]:
    stored, affected = 0, set()
    for e in events:
        if not _valid(e):
            log.warning("dropping invalid event %s (%s)", e.event_id, e.type)
            continue
        if e.type in SESSIONLESS_TYPES:
            session = None
            if e.session_id is not None or db.get(Paper, (user_id, e.arxiv_id)) is None:
                log.warning("dropping %s %s for a paper not in the library", e.type, e.event_id)
                continue
        else:
            session = db.get(ReadingSession, e.session_id) if e.session_id else None
            if session is None or session.user_id != user_id or session.arxiv_id != e.arxiv_id:
                log.warning("dropping event %s for unknown session %s", e.event_id, e.session_id)
                continue
        ts = _as_utc(e.ts)
        event = metrics.record_event(
            db, user_id, e.arxiv_id, e.type, e.payload, e.session_id, ts, e.event_id, _client_config(e)
        )
        if event is None:
            continue  # duplicate resend
        event.schema_version = e.schema_version
        stored += 1
        if session is None:
            continue  # impressions don't change any paper
        affected.add(e.arxiv_id)
        if e.type == "page_dwell":
            session.active_ms += e.payload["active_ms"]
        elif e.type == "paper_closed":
            session.ended_at = ts
        db.add(session)
    for arxiv_id in affected:
        if paper := db.get(Paper, (user_id, arxiv_id)):
            metrics.check_completion(db, settings, paper)
            db.add(paper)
    db.commit()
    return stored, affected


def http_sender(settings: Settings) -> Sender:
    def send(body: dict) -> list[str]:
        resp = httpx.post(settings.feedback_url, json=body, timeout=10)
        resp.raise_for_status()
        return resp.json()["accepted"]

    return send


_forward_lock = threading.Lock()


def forward_pending(engine: Engine, settings: Settings, send: Sender) -> int:
    """Send unreported events to the feedback endpoint. Returns how many were accepted."""
    total = 0
    with _forward_lock, Session(engine) as db:
        while True:
            batch = db.exec(
                select(Event).where(Event.reported_at.is_(None)).order_by(Event.ts).limit(FORWARD_BATCH)
            ).all()
            if not batch:
                break
            body = {
                "events": [
                    {
                        "event_id": e.event_id,
                        "user_id": e.user_id,
                        "type": e.type,
                        "arxiv_id": e.arxiv_id,
                        "session_id": e.session_id,
                        "ts": iso(e.ts),
                        "payload": e.payload,
                        "schema_version": e.schema_version,
                        "config": e.config,
                    }
                    for e in batch
                ],
            }
            try:
                accepted = set(send(body))
            except Exception as exc:  # noqa: BLE001 - any failure means retry later
                log.warning("feedback endpoint unavailable, will retry: %s", exc)
                break
            now = utcnow()
            for e in batch:
                if e.event_id in accepted:
                    e.reported_at = now
                    db.add(e)
            db.commit()
            total += len(accepted)
            if len(accepted) < len(batch):
                break  # avoid spinning on events the endpoint keeps rejecting
    return total
