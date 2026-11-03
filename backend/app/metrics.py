"""Rollups derived from raw events, plus the auto-status rules.

Nothing here keeps its own counters: every number is recomputed from `events`
and `sessions`, so it can never drift from the raw data.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, text
from sqlmodel import Session, select

from .config import Settings
from .models import Event, Paper, ReadingSession, utcnow

# Auto transitions only move forward, and never touch skimmed/abandoned.
_AUTO_FORWARD = {"unread": {"reading", "read"}, "reading": {"read"}}


@dataclass
class Rollup:
    page_ms: dict[int, int] = field(default_factory=dict)
    sessions: int = 0
    return_visits: int = 0

    def total_ms(self) -> int:
        return sum(self.page_ms.values())

    def pages_viewed(self, min_s: int) -> int:
        return sum(1 for ms in self.page_ms.values() if ms >= min_s * 1000)

    def furthest_page(self) -> int | None:
        return max((p for p, ms in self.page_ms.items() if ms > 0), default=None)

    @property
    def visits(self) -> int:
        return self.return_visits + 1 if self.sessions else 0


def rollups(
    db: Session, user_id: int | None, arxiv_ids: list[str] | None = None
) -> dict[tuple[int, str], Rollup]:
    """Rollups keyed by (user_id, arxiv_id). `None` means all users / all papers."""
    db.flush()  # raw SQL below bypasses autoflush
    out: dict[tuple[int, str], Rollup] = {}
    params: dict = {}
    where = ""
    if user_id is not None:
        where += " AND user_id = :user_id"
        params["user_id"] = user_id
    if arxiv_ids is not None:
        if not arxiv_ids:
            return out
        where += f" AND arxiv_id IN ({', '.join(f':id{i}' for i in range(len(arxiv_ids)))})"
        params |= {f"id{i}": a for i, a in enumerate(arxiv_ids)}

    dwell = db.connection().execute(
        text(
            f"""
            SELECT user_id, arxiv_id,
                   CAST(json_extract(payload, '$.page') AS INTEGER) AS page,
                   SUM(json_extract(payload, '$.active_ms')) AS ms
            FROM events
            WHERE type = 'page_dwell' {where}
            GROUP BY user_id, arxiv_id, page
            """
        ),
        params,
    )
    for uid, arxiv_id, page, ms in dwell:
        out.setdefault((uid, arxiv_id), Rollup()).page_ms[page] = int(ms or 0)

    visits = db.connection().execute(
        text(
            f"""
            SELECT user_id, arxiv_id, COUNT(*), SUM(is_return_visit)
            FROM sessions
            WHERE 1 = 1 {where}
            GROUP BY user_id, arxiv_id
            """
        ),
        params,
    )
    for uid, arxiv_id, n, returns in visits:
        r = out.setdefault((uid, arxiv_id), Rollup())
        r.sessions, r.return_visits = n, int(returns or 0)
    return out


def rollup(db: Session, user_id: int, arxiv_id: str) -> Rollup:
    return rollups(db, user_id, [arxiv_id]).get((user_id, arxiv_id), Rollup())


def record_event(
    db: Session,
    user_id: int,
    arxiv_id: str,
    type: str,
    payload: dict,
    session_id: str | None = None,
    ts: datetime | None = None,
    event_id: str | None = None,
    config: dict | None = None,
) -> Event | None:
    """Store one event. Returns None if `event_id` was already stored.

    `config` is the thresholds the event was produced under; it defaults to the
    server's current ones.
    """
    event_id = event_id or str(uuid4())
    if db.get(Event, event_id) is not None:
        return None
    event = Event(
        event_id=event_id,
        user_id=user_id,
        arxiv_id=arxiv_id,
        type=type,
        payload=payload,
        session_id=session_id,
        ts=ts or utcnow(),
        config=config or db.info.get("event_config"),
    )
    db.add(event)
    return event


def set_status(db: Session, paper: Paper, to: str, source: str) -> bool:
    """Change status and log it. Auto changes only move forward."""
    if paper.status == to:
        if source == "manual":
            paper.status_source = "manual"
        return False
    if source == "auto" and to not in _AUTO_FORWARD.get(paper.status, set()):
        return False
    record_event(db, paper.user_id, paper.arxiv_id, "status_changed", {"from": paper.status, "to": to, "source": source})
    paper.status, paper.status_source = to, source
    return True


def last_activity(db: Session, user_id: int, arxiv_id: str) -> datetime | None:
    last_event = db.exec(
        select(func.max(Event.ts)).where(
            Event.user_id == user_id,
            Event.arxiv_id == arxiv_id,
            Event.type.in_(("paper_opened", "page_dwell", "paper_closed")),
        )
    ).one()
    last_end = db.exec(
        select(func.max(ReadingSession.ended_at)).where(
            ReadingSession.user_id == user_id, ReadingSession.arxiv_id == arxiv_id
        )
    ).one()
    return max((t for t in (last_event, last_end) if t is not None), default=None)


def start_session(db: Session, settings: Settings, paper: Paper, context: dict | None = None) -> ReadingSession:
    now = utcnow()
    last = last_activity(db, paper.user_id, paper.arxiv_id)
    is_return = last is not None and now - last >= timedelta(minutes=settings.return_visit_gap_min)
    session = ReadingSession(
        session_id=str(uuid4()),
        user_id=paper.user_id,
        arxiv_id=paper.arxiv_id,
        started_at=now,
        is_return_visit=is_return,
        context=context,
    )
    db.add(session)
    record_event(
        db,
        paper.user_id,
        paper.arxiv_id,
        "paper_opened",
        {"is_return_visit": is_return, "context": context},
        session.session_id,
        now,
    )
    paper.last_opened_at = now
    if last is None and paper.feedback_state == "none":
        paper.feedback_state = "pending"  # first visit: ask about it once they leave
    set_status(db, paper, "reading", "auto")
    return session


def check_completion(db: Session, settings: Settings, paper: Paper) -> None:
    """Emit `completion_reached` once, and auto-mark Read when allowed."""
    if not paper.num_pages:
        return
    viewed = rollup(db, paper.user_id, paper.arxiv_id).pages_viewed(settings.page_viewed_min_s)
    if viewed / paper.num_pages < settings.auto_read_threshold:
        return
    already = db.exec(
        select(Event.event_id).where(
            Event.user_id == paper.user_id, Event.arxiv_id == paper.arxiv_id, Event.type == "completion_reached"
        )
    ).first()
    if already:
        return
    record_event(
        db, paper.user_id, paper.arxiv_id, "completion_reached", {"pages_viewed": viewed, "num_pages": paper.num_pages}
    )
    set_status(db, paper, "read", "auto")
