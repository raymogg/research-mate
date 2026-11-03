"""Read-only views over everything we capture, shared by the admin API and the CLI."""

from pathlib import Path

from sqlalchemy import func
from sqlmodel import Session, col, select

from . import metrics
from .config import Settings
from .events import EVENT_CATALOG
from .models import STATUSES, Event, Paper, PaperTag, ReadingSession, User, iso


def _size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as f:
        return sum(1 for _ in f)


def _usernames(db: Session) -> dict[int, str]:
    return dict(db.exec(select(User.id, User.username)).all())


def users(db: Session) -> list[dict]:
    """Every account with a summary of what it has produced."""
    papers = dict(db.exec(select(Paper.user_id, func.count()).group_by(Paper.user_id)).all())
    events = {
        uid: (n, last)
        for uid, n, last in db.exec(
            select(Event.user_id, func.count(), func.max(Event.ts)).group_by(Event.user_id)
        ).all()
    }
    active: dict[int, int] = {}
    for (uid, _), r in metrics.rollups(db, None).items():
        active[uid] = active.get(uid, 0) + r.total_ms()
    return [
        {
            "id": u.id,
            "username": u.username,
            "is_admin": u.is_admin,
            "created_at": iso(u.created_at),
            "papers": papers.get(u.id, 0),
            "events": events.get(u.id, (0, None))[0],
            "last_event_ts": iso(events.get(u.id, (0, None))[1]),
            "total_active_ms": active.get(u.id, 0),
        }
        for u in db.exec(select(User).order_by(User.id)).all()
    ]


def overview(db: Session, settings: Settings, user_id: int | None = None) -> dict:
    """Totals for one user, or everyone when `user_id` is None."""

    def scoped(q, model):
        return q.where(model.user_id == user_id) if user_id is not None else q

    papers = db.exec(scoped(select(Paper), Paper)).all()
    by_status = {s: 0 for s in STATUSES}
    for p in papers:
        by_status[p.status] += 1

    counts = {
        t: (n, last)
        for t, n, last in db.exec(
            scoped(select(Event.type, func.count(), func.max(Event.ts)), Event).group_by(Event.type)
        ).all()
    }
    first_ts, last_ts = db.exec(scoped(select(func.min(Event.ts), func.max(Event.ts)), Event)).one()
    unreported, oldest_unreported = db.exec(
        scoped(select(func.count(), func.min(Event.ts)), Event).where(col(Event.reported_at).is_(None))
    ).one()
    n_sessions, n_returns = db.exec(
        scoped(
            select(func.count(), func.coalesce(func.sum(ReadingSession.is_return_visit), 0)), ReadingSession
        )
    ).one()
    rolls = metrics.rollups(db, user_id)
    pdfs = list(settings.pdf_dir.glob("*.pdf")) if settings.pdf_dir.exists() else []

    return {
        "totals": {
            "papers": len(papers),
            "papers_by_status": by_status,
            "rated": sum(1 for p in papers if p.rating is not None),
            "users": len(db.exec(select(User.id)).all()),
            "tags": len(db.exec(scoped(select(PaperTag.tag).distinct(), PaperTag)).all()),
            "sessions": n_sessions,
            "return_visits": int(n_returns),
            "total_active_ms": sum(r.total_ms() for r in rolls.values()),
            "events": sum(n for n, _ in counts.values()),
            "unreported_events": unreported,
            "oldest_unreported_ts": iso(oldest_unreported),
            "first_event_ts": iso(first_ts),
            "last_event_ts": iso(last_ts),
        },
        "event_types": [
            {
                "type": t,
                **spec,
                "count": counts.get(t, (0, None))[0],
                "last_ts": iso(counts.get(t, (0, None))[1]),
            }
            for t, spec in EVENT_CATALOG.items()
        ],
        "storage": {
            "data_dir": str(settings.data_dir.resolve()),
            "db_bytes": _size(settings.data_dir / "app.db"),
            "pdf_count": len(pdfs),
            "pdf_bytes": sum(_size(p) for p in pdfs),
            "feedback_log": str(settings.feedback_log.resolve()),
            "feedback_log_lines": _line_count(settings.feedback_log),
            "feedback_log_bytes": _size(settings.feedback_log),
        },
        "settings": {**settings.client_config(), "feedback_url": settings.feedback_url},
    }


def event_row(e: Event, titles: dict[str, str], names: dict[int, str]) -> dict:
    return {
        "event_id": e.event_id,
        "user_id": e.user_id,
        "username": names.get(e.user_id),
        "type": e.type,
        "arxiv_id": e.arxiv_id,
        "title": titles.get(e.arxiv_id),
        "session_id": e.session_id,
        "ts": iso(e.ts),
        "payload": e.payload,
        "reported_at": iso(e.reported_at),
    }


def recent_events(
    db: Session,
    user_id: int | None = None,
    type: str | None = None,
    arxiv_id: str | None = None,
    session_id: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> list[dict]:
    q = select(Event).order_by(col(Event.ts).desc(), col(Event.event_id)).offset(offset).limit(limit)
    if user_id is not None:
        q = q.where(Event.user_id == user_id)
    if type:
        q = q.where(Event.type == type)
    if arxiv_id:
        q = q.where(Event.arxiv_id == arxiv_id)
    if session_id:
        q = q.where(Event.session_id == session_id)
    titles = dict(db.exec(select(Paper.arxiv_id, Paper.title)).all())
    names = _usernames(db)
    return [event_row(e, titles, names) for e in db.exec(q)]


def sessions(db: Session, user_id: int | None = None, arxiv_id: str | None = None, limit: int = 50) -> list[dict]:
    q = select(ReadingSession).order_by(col(ReadingSession.started_at).desc()).limit(limit)
    if user_id is not None:
        q = q.where(ReadingSession.user_id == user_id)
    if arxiv_id:
        q = q.where(ReadingSession.arxiv_id == arxiv_id)
    rows = db.exec(q).all()
    titles = dict(db.exec(select(Paper.arxiv_id, Paper.title)).all())
    names = _usernames(db)
    ids = [s.session_id for s in rows]
    event_counts = dict(
        db.exec(
            select(Event.session_id, func.count()).where(col(Event.session_id).in_(ids)).group_by(Event.session_id)
        ).all()
    )
    pages = dict(
        db.exec(
            select(Event.session_id, func.count(func.distinct(func.json_extract(Event.payload, "$.page"))))
            .where(col(Event.session_id).in_(ids), Event.type == "page_dwell")
            .group_by(Event.session_id)
        ).all()
    )
    return [
        {
            "session_id": s.session_id,
            "user_id": s.user_id,
            "username": names.get(s.user_id),
            "arxiv_id": s.arxiv_id,
            "title": titles.get(s.arxiv_id),
            "started_at": iso(s.started_at),
            "ended_at": iso(s.ended_at),
            "active_ms": s.active_ms,
            "is_return_visit": s.is_return_visit,
            "events": event_counts.get(s.session_id, 0),
            "pages_touched": pages.get(s.session_id, 0),
        }
        for s in rows
    ]


def reading_by_paper(db: Session, settings: Settings, user_id: int | None = None) -> list[dict]:
    """Each (user, paper) with its full per-page dwell, most read first."""
    rolls = metrics.rollups(db, user_id)
    names = _usernames(db)
    q = select(Paper)
    if user_id is not None:
        q = q.where(Paper.user_id == user_id)
    out = []
    for p in db.exec(q).all():
        r = rolls.get((p.user_id, p.arxiv_id), metrics.Rollup())
        out.append(
            {
                "user_id": p.user_id,
                "username": names.get(p.user_id),
                "arxiv_id": p.arxiv_id,
                "title": p.title,
                "status": p.status,
                "status_source": p.status_source,
                "rating": p.rating,
                "num_pages": p.num_pages,
                "total_ms": r.total_ms(),
                "visits": r.visits,
                "pages_viewed": r.pages_viewed(settings.page_viewed_min_s),
                "page_ms": {str(page): ms for page, ms in sorted(r.page_ms.items())},
                "added_at": iso(p.added_at),
                "last_opened_at": iso(p.last_opened_at),
            }
        )
    out.sort(key=lambda x: x["total_ms"], reverse=True)
    return out
