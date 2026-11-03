from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, Field
from sqlmodel import Session

from ..auth import current_user
from ..db import get_db
from ..events import ClientEvent, ingest
from ..models import Paper, User
from .common import schedule_forward
from .papers import paper_out

router = APIRouter(prefix="/api")


class EventBatch(BaseModel):
    events: list[ClientEvent] = Field(max_length=1000)


@router.post("/events")
def post_events(
    body: EventBatch,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    stored, affected = ingest(db, request.app.state.settings, user.id, body.events)
    schedule_forward(background, request)
    # Return fresh state so the reader can react, e.g. show the rating prompt after auto-Read.
    papers = [paper_out(request, db, p, detail=False) for a in sorted(affected) if (p := db.get(Paper, (user.id, a)))]
    return {"stored": stored, "papers": papers}


@router.get("/config")
def client_config(request: Request):
    return request.app.state.settings.client_config()
