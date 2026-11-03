import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import func
from sqlmodel import Session, select

from .. import metrics
from ..auth import current_user
from ..db import get_db
from ..models import PaperTag, User
from .common import schedule_forward
from .papers import get_paper, paper_out

router = APIRouter(prefix="/api")


def normalise(tag: str) -> str:
    name = re.sub(r"\s+", " ", tag).strip().lower()
    if not name or len(name) > 64:
        raise HTTPException(400, "Tags must be 1-64 characters")
    return name


@router.get("/tags")
def list_tags(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.exec(
        select(PaperTag.tag, func.count())
        .where(PaperTag.user_id == user.id)
        .group_by(PaperTag.tag).order_by(func.count().desc(), PaperTag.tag)
    ).all()
    return [{"name": name, "count": n} for name, n in rows]


@router.put("/papers/{key}/tags/{tag}")
def add_tag(
    key: str,
    tag: str,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    paper = get_paper(db, user, key)
    name = normalise(tag)
    if db.get(PaperTag, (user.id, paper.arxiv_id, name)) is None:
        db.add(PaperTag(user_id=user.id, arxiv_id=paper.arxiv_id, tag=name))
        metrics.record_event(db, user.id, paper.arxiv_id, "tag_added", {"tag": name})
    db.commit()
    schedule_forward(background, request)
    return paper_out(request, db, paper)


@router.delete("/papers/{key}/tags/{tag}")
def remove_tag(
    key: str,
    tag: str,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    paper = get_paper(db, user, key)
    name = normalise(tag)
    if link := db.get(PaperTag, (user.id, paper.arxiv_id, name)):
        db.delete(link)
        metrics.record_event(db, user.id, paper.arxiv_id, "tag_removed", {"tag": name})
    db.commit()
    schedule_forward(background, request)
    return paper_out(request, db, paper)
