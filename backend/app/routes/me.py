"""The profile page: your account, sign-ins, API tokens and the data we hold.

`GET /api/me/export` is the one endpoint that also takes an API token
(`Authorization: Bearer trk_...`). It takes no user id: it always answers with
the caller's own data.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from .. import account, auth
from ..db import get_db
from ..models import ApiToken, Event, Identity, Paper, ReadingSession, User, iso, utcnow
from .auth import user_out

router = APIRouter(prefix="/api/me")

MAX_TOKENS = 10


def _identities(db: Session, user: User) -> list[Identity]:
    return list(db.exec(select(Identity).where(Identity.user_id == user.id).order_by(Identity.created_at)))


@router.get("")
def profile(request: Request, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    def count(model) -> int:
        return db.exec(select(func.count()).select_from(model).where(model.user_id == user.id)).one()

    first, last = db.exec(select(func.min(Event.ts), func.max(Event.ts)).where(Event.user_id == user.id)).one()
    active_ms = db.exec(
        select(func.coalesce(func.sum(ReadingSession.active_ms), 0)).where(ReadingSession.user_id == user.id)
    ).one()
    tokens = db.exec(select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at)).all()
    return {
        **user_out(user),
        "created_at": iso(user.created_at),
        "has_password": auth.has_password(user),
        "identities": [
            {"provider": i.provider, "email": i.email, "created_at": iso(i.created_at), "last_used_at": iso(i.last_used_at)}
            for i in _identities(db, user)
        ],
        "tokens": [
            {"id": t.id, "name": t.name, "created_at": iso(t.created_at), "last_used_at": iso(t.last_used_at)}
            for t in tokens
        ],
        "data": {
            "papers": count(Paper),
            "sessions": count(ReadingSession),
            "events": count(Event),
            "active_ms": active_ms,
            "first_event_ts": iso(first),
            "last_event_ts": iso(last),
        },
        "backup_retention_days": request.app.state.settings.backup_retention_days,
    }


@router.get("/events")
def my_events(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(auth.current_user),
):
    """Your recorded events, newest first, with paper titles."""
    rows = db.exec(
        select(Event)
        .where(Event.user_id == user.id)
        .order_by(Event.ts.desc(), Event.event_id)
        .offset(offset)
        .limit(limit)
    ).all()
    titles = dict(db.exec(select(Paper.arxiv_id, Paper.title).where(Paper.user_id == user.id)).all())
    return [
        {
            "event_id": e.event_id,
            "type": e.type,
            "arxiv_id": e.arxiv_id,
            "title": titles.get(e.arxiv_id),
            "session_id": e.session_id,
            "ts": iso(e.ts),
            "payload": e.payload,
        }
        for e in rows
    ]


@router.get("/export")
def export(db: Session = Depends(get_db), user: User = Depends(auth.export_user)):
    """Everything we hold about you, as one JSON document."""
    body = json.dumps(account.export(db, user), indent=1)
    filename = f"researchmate-{user.username}-{utcnow():%Y-%m-%d}.json"
    return Response(
        body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


class NewToken(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@router.post("/tokens", status_code=201)
def create_token(body: NewToken, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    """A new export API token. The token itself is in this response only."""
    have = db.exec(select(func.count()).select_from(ApiToken).where(ApiToken.user_id == user.id)).one()
    if have >= MAX_TOKENS:
        raise HTTPException(400, f"You have {MAX_TOKENS} tokens already; revoke one first")
    token, row = auth.create_api_token(db, user, body.name.strip())
    return {"id": row.id, "name": row.name, "created_at": iso(row.created_at), "last_used_at": None, "token": token}


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(token_id: int, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    row = db.get(ApiToken, token_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "No such token")
    db.delete(row)
    db.commit()


@router.delete("/identities/{provider}", status_code=204)
def unlink(provider: str, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    identities = _identities(db, user)
    identity = next((i for i in identities if i.provider == provider), None)
    if identity is None:
        raise HTTPException(404, f"No {provider} sign-in on this account")
    if len(identities) == 1 and not auth.has_password(user):
        raise HTTPException(400, "This is your only way to sign in; link another one first")
    db.delete(identity)
    db.commit()


class DeleteAccount(BaseModel):
    confirm: str = Field(max_length=64)  # the username, typed out


@router.delete("", status_code=204)
def delete_account(
    body: DeleteAccount,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(auth.current_user),
):
    """Deletes the account and all its data. There's no undo (backups expire on their own)."""
    if body.confirm.strip().lower() != user.username:
        raise HTTPException(400, "Type your username to confirm")
    account.delete_user(db, request.app.state.settings, user)
    response.delete_cookie(auth.COOKIE)
