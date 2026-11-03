"""Client event ingest and forwarding to the feedback endpoint."""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from . import metrics
from .config import Settings
from .models import Event, Paper, ReadingSession, iso, utcnow

log = logging.getLogger(__name__)

# Every event type we capture: where it comes from, what it means, its payload fields.
# The admin page and `app.cli` read this, so keep it in step with what is emitted.
EVENT_CATALOG: dict[str, dict] = {
    "paper_added": {
        "source": "server",
        "description": "A new paper was saved to the library",
        "payload": {"input": "the exact text pasted (URL or ID)"},
    },
    "paper_opened": {
        "source": "server",
        "description": "A reading session started",
        "payload": {"is_return_visit": "true if the last activity on this paper was ≥ RETURN_VISIT_GAP_MIN ago"},
    },
    "page_dwell": {
        "source": "client",
        "description": "Active reading time on one page (tab visible, input within IDLE_CUTOFF_S)",
        "payload": {"page": "1-based page number", "active_ms": "active time since the last report for this page"},
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
    "tag_added": {"source": "server", "description": "Tag added to a paper", "payload": {"tag": "normalised tag"}},
    "tag_removed": {"source": "server", "description": "Tag removed from a paper", "payload": {"tag": "normalised tag"}},
}

# Only these come from the browser; everything else is emitted server-side.
CLIENT_EVENT_TYPES = {t for t, spec in EVENT_CATALOG.items() if spec["source"] == "client"}
MAX_DWELL_MS = 60 * 60 * 1000
FORWARD_BATCH = 500

Sender = Callable[[dict], list[str]]


class ClientEvent(BaseModel):
    event_id: str = Field(min_length=8, max_length=64)
    type: str
    arxiv_id: str
    session_id: str
    ts: datetime
    payload: dict[str, Any] = {}


def _as_utc(ts: datetime) -> datetime:
    return ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)


def _valid(e: ClientEvent) -> bool:
    if e.type not in CLIENT_EVENT_TYPES:
        return False
    if e.type == "page_dwell":
        page, ms = e.payload.get("page"), e.payload.get("active_ms")
        return (
            isinstance(page, int) and page >= 1 and isinstance(ms, int) and 0 < ms <= MAX_DWELL_MS
        )
    return True


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
        session = db.get(ReadingSession, e.session_id)
        if session is None or session.user_id != user_id or session.arxiv_id != e.arxiv_id:
            log.warning("dropping event %s for unknown session %s", e.event_id, e.session_id)
            continue
        ts = _as_utc(e.ts)
        if metrics.record_event(db, user_id, e.arxiv_id, e.type, e.payload, e.session_id, ts, e.event_id) is None:
            continue  # duplicate resend
        stored += 1
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
