"""Questions asked once after the first visit to a paper.

Opening a paper for the first time marks it `pending`. The browser asks on
leaving the reader, or on the next visit to the site if the tab was closed.
"""

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session, select

from .. import metrics
from ..auth import current_user
from ..db import get_db
from ..events import LEVELS, REASONS
from ..models import Paper, User
from .common import schedule_forward
from .papers import get_paper, paper_out

router = APIRouter(prefix="/api")

Score = Field(default=None, ge=1, le=5)
MAX_SHOWN_MS = 24 * 60 * 60 * 1000


class Answers(BaseModel):
    interest: int | None = Score
    value: int | None = Score
    novelty: int | None = Score
    level: Literal[LEVELS] | None = None  # type: ignore[valid-type]
    reasons: list[Literal[REASONS]] = Field(default=[], max_length=len(REASONS))  # type: ignore[valid-type]
    # Set to dismiss instead of answering.
    skipped: Literal["skip", "didnt_read"] | None = None
    context: Literal["after_reading", "next_visit"]
    shown_ms: int = Field(ge=0, le=MAX_SHOWN_MS)

    @model_validator(mode="after")
    def answered_or_skipped(self):
        answered = any(v is not None for v in (self.interest, self.value, self.novelty, self.level)) or self.reasons
        if bool(answered) == bool(self.skipped):
            raise ValueError("give at least one answer, or skip")
        return self


@router.get("/feedback/pending")
def pending(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Papers waiting for answers, most recently opened first."""
    papers = db.exec(
        select(Paper)
        .where(Paper.user_id == user.id, Paper.feedback_state == "pending")
        .order_by(Paper.last_opened_at.desc())
    ).all()
    return [paper_out(request, db, p, detail=False) for p in papers]


@router.post("/papers/{key}/feedback")
def answer(
    key: str,
    body: Answers,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    paper = get_paper(db, user, key)
    if paper.feedback_state != "pending":
        raise HTTPException(409, "Already answered")
    if body.skipped:
        metrics.record_event(
            db, user.id, paper.arxiv_id, "feedback_skipped",
            {"why": body.skipped, "context": body.context, "shown_ms": body.shown_ms},
        )
        paper.feedback_state = "skipped"
    else:
        metrics.record_event(
            db, user.id, paper.arxiv_id, "paper_feedback",
            body.model_dump(include={"interest", "value", "novelty", "level", "reasons", "context", "shown_ms"}),
        )
        paper.feedback_state = "done"
    db.add(paper)
    db.commit()
    schedule_forward(background, request)
    return paper_out(request, db, paper, detail=False)
