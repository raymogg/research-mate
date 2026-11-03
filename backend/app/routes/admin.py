from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import Session

from .. import admin
from ..auth import admin_user
from ..db import get_db

router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin_user)])


@router.get("/overview")
def overview(request: Request, db: Session = Depends(get_db), user_id: int | None = None):
    return admin.overview(db, request.app.state.settings, user_id)


@router.get("/users")
def users(db: Session = Depends(get_db)):
    return admin.users(db)


@router.get("/events")
def events(
    db: Session = Depends(get_db),
    user_id: int | None = None,
    type: str | None = None,
    arxiv_id: str | None = None,
    session_id: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
):
    return admin.recent_events(db, user_id, type, arxiv_id, session_id, offset, limit)


@router.get("/sessions")
def sessions(
    db: Session = Depends(get_db),
    user_id: int | None = None,
    arxiv_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
):
    return admin.sessions(db, user_id, arxiv_id, limit)


@router.get("/papers")
def papers(request: Request, db: Session = Depends(get_db), user_id: int | None = None):
    return admin.reading_by_paper(db, request.app.state.settings, user_id)
