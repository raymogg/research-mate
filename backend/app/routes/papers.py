from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import arxiv, events, metrics
from ..auth import current_user
from ..db import get_db
from ..models import ADD_INTENTS, DISCOVERY_SOURCES, STATUSES, Paper, PaperTag, User, iso
from .common import schedule_forward

router = APIRouter(prefix="/api")


def serialize(paper: Paper, tags: list[str], r: metrics.Rollup, min_s: int, detail: bool = False) -> dict:
    viewed = r.pages_viewed(min_s)
    out = {
        "arxiv_id": paper.arxiv_id,
        "key": arxiv.to_key(paper.arxiv_id),
        "version": paper.version,
        "title": paper.title,
        "authors": paper.authors,
        "categories": paper.categories,
        "published_at": iso(paper.published_at),
        "num_pages": paper.num_pages,
        "status": paper.status,
        "status_source": paper.status_source,
        "rating": paper.rating,
        "rating_prompted": paper.rating_prompted,
        "feedback_state": paper.feedback_state,
        "source": paper.source,
        "added_at": iso(paper.added_at),
        "last_opened_at": iso(paper.last_opened_at),
        "tags": sorted(tags),
        "metrics": {
            "total_ms": r.total_ms(),
            "visits": r.visits,
            "pages_viewed": viewed,
            "completion": r.completion(min_s, paper.num_pages),
            "furthest_page": r.furthest_page(),
        },
    }
    if detail:
        out["abstract"] = paper.abstract
        out["metrics"]["page_ms"] = {str(p): ms for p, ms in sorted(r.page_ms.items())}
        out["metrics"]["section_ms"] = dict(r.section_ms)
    return out


def tags_by_paper(db: Session, user_id: int, arxiv_ids: list[str] | None = None) -> dict[str, list[str]]:
    q = select(PaperTag).where(PaperTag.user_id == user_id)
    if arxiv_ids is not None:
        q = q.where(PaperTag.arxiv_id.in_(arxiv_ids))
    out: dict[str, list[str]] = {}
    for pt in db.exec(q):
        out.setdefault(pt.arxiv_id, []).append(pt.tag)
    return out


def paper_out(request: Request, db: Session, paper: Paper, detail: bool = True) -> dict:
    tags = tags_by_paper(db, paper.user_id, [paper.arxiv_id]).get(paper.arxiv_id, [])
    return serialize(
        paper, tags, metrics.rollup(db, paper.user_id, paper.arxiv_id), request.app.state.settings.page_viewed_min_s, detail
    )


def get_paper(db: Session, user: User, key: str) -> Paper:
    paper = db.get(Paper, (user.id, arxiv.from_key(key)))
    if paper is None:
        raise HTTPException(404, "Paper not in library")
    return paper


class AddPaper(BaseModel):
    input: str = Field(min_length=1, max_length=500)
    # Where they found it, if they said. "suggested": added from our starter shelf.
    source: Literal[DISCOVERY_SOURCES + ("suggested",)] | None = None
    intent: Literal[ADD_INTENTS] = "open"  # "later": saved to read later, not opened now


@router.post("/papers")
def add_paper(
    body: AddPaper,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    parsed = arxiv.parse_id(body.input)
    if parsed is None:
        raise HTTPException(400, "Not an arXiv link or ID. Try e.g. https://arxiv.org/abs/2401.12345")
    if existing := db.get(Paper, (user.id, parsed.arxiv_id)):
        if body.source and existing.source is None:
            existing.source = body.source
            db.add(existing)
            db.commit()
        return paper_out(request, db, existing)
    # Someone else already has it: reuse their metadata instead of asking arXiv again.
    if known := db.exec(select(Paper).where(Paper.arxiv_id == parsed.arxiv_id)).first():
        paper = Paper(
            user_id=user.id,
            **known.model_dump(include={"arxiv_id", "version", "title", "authors", "abstract", "categories",
                                        "published_at", "num_pages"}),
        )
        if parsed.version:
            paper.version = parsed.version
    else:
        try:
            meta = arxiv.fetch_metadata(parsed.arxiv_id)
        except arxiv.ArxivNotFound:
            raise HTTPException(404, f"arXiv has no paper {parsed.arxiv_id}")
        except arxiv.ArxivError as e:
            raise HTTPException(502, str(e))
        paper = Paper(
            user_id=user.id,
            arxiv_id=parsed.arxiv_id,
            version=parsed.version or meta.version,
            title=meta.title,
            authors=meta.authors,
            abstract=meta.abstract,
            categories=meta.categories,
            published_at=meta.published_at,
        )
    paper.source = body.source
    db.add(paper)
    metrics.record_event(
        db, user.id, paper.arxiv_id, "paper_added", {"input": body.input, "source": body.source, "intent": body.intent}
    )
    db.commit()
    schedule_forward(background, request)
    response.status_code = 201
    return paper_out(request, db, paper)


SortKey = Literal["last_opened", "added", "read_time", "rating"]


@router.get("/papers")
def list_papers(
    request: Request,
    db: Session = Depends(get_db),
    status: list[str] = Query(default=[]),
    tag: list[str] = Query(default=[]),
    min_rating: int | None = Query(default=None, ge=1, le=5),
    later: bool = False,  # only papers never opened: the read-later list
    sort: SortKey = "last_opened",
    user: User = Depends(current_user),
):
    min_s = request.app.state.settings.page_viewed_min_s
    papers = db.exec(select(Paper).where(Paper.user_id == user.id)).all()
    tags = tags_by_paper(db, user.id)
    rolls = metrics.rollups(db, user.id)
    wanted_tags = {t.strip().lower() for t in tag}
    rows = []
    for p in papers:
        if later and p.last_opened_at is not None:
            continue
        if status and p.status not in status:
            continue
        if min_rating is not None and (p.rating or 0) < min_rating:
            continue
        if not wanted_tags <= set(tags.get(p.arxiv_id, [])):
            continue
        rows.append((p, rolls.get((user.id, p.arxiv_id), metrics.Rollup())))

    never = datetime.min.replace(tzinfo=UTC)
    sorters = {
        "last_opened": lambda x: (x[0].last_opened_at or never, x[0].added_at),
        "added": lambda x: x[0].added_at,
        "read_time": lambda x: x[1].total_ms(),
        "rating": lambda x: (x[0].rating or 0, x[0].last_opened_at or never),
    }
    rows.sort(key=sorters[sort], reverse=True)
    return [serialize(p, tags.get(p.arxiv_id, []), r, min_s) for p, r in rows]


@router.get("/papers/{key}")
def read_paper(key: str, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return paper_out(request, db, get_paper(db, user, key))


class UpdatePaper(BaseModel):
    status: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    prompted: bool = False  # rating came from the post-completion prompt
    rating_prompted: bool | None = None
    num_pages: int | None = Field(default=None, ge=1, le=10_000)


@router.patch("/papers/{key}")
def update_paper(
    key: str,
    body: UpdatePaper,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    paper = get_paper(db, user, key)
    settings = request.app.state.settings
    fields = body.model_fields_set
    if "status" in fields and body.status is not None:
        if body.status not in STATUSES:
            raise HTTPException(400, f"status must be one of {', '.join(STATUSES)}")
        metrics.set_status(db, paper, body.status, "manual")
    if "rating" in fields and body.rating != paper.rating:
        metrics.record_event(
            db, user.id, paper.arxiv_id, "rating_set", {"from": paper.rating, "to": body.rating, "prompted": body.prompted}
        )
        paper.rating = body.rating
    if body.rating_prompted:
        paper.rating_prompted = True
    if body.num_pages and body.num_pages != paper.num_pages:
        paper.num_pages = body.num_pages
        metrics.check_completion(db, settings, paper)
    db.add(paper)
    db.commit()
    schedule_forward(background, request)
    return paper_out(request, db, paper)


class OpenSession(BaseModel):
    context: dict | None = None  # see events.SessionContext; checked leniently


@router.post("/papers/{key}/sessions", status_code=201)
def open_session(
    key: str,
    request: Request,
    background: BackgroundTasks,
    body: OpenSession | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    paper = get_paper(db, user, key)
    context = events.session_context(body.context if body else None)
    session = metrics.start_session(db, request.app.state.settings, paper, context)
    db.add(paper)
    db.commit()
    schedule_forward(background, request)
    return {
        "session_id": session.session_id,
        "is_return_visit": session.is_return_visit,
        "paper": paper_out(request, db, paper),
    }


@router.get("/papers/{key}/pdf")
def paper_pdf(key: str, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    paper = get_paper(db, user, key)
    return serve_pdf(request.app.state.settings, paper.arxiv_id, paper.version)


def serve_pdf(settings, arxiv_id: str, version: str | None):
    """A paper's PDF from the cache, or streamed from arXiv into it."""
    path = settings.pdf_dir / f"{arxiv.to_key(arxiv_id)}{version or ''}.pdf"
    if path.exists():
        return FileResponse(path, media_type="application/pdf", headers={"X-Pdf-Source": "cache"})
    try:
        size, body = arxiv.stream_pdf(arxiv_id, version, path)
    except arxiv.ArxivError as e:
        raise HTTPException(502, str(e))
    headers = {"X-Pdf-Source": "arxiv"}
    if size:
        headers["Content-Length"] = str(size)
    return StreamingResponse(body, media_type="application/pdf", headers=headers)


# The reader document runs no scripts at all. It may load arXiv's stylesheets,
# fonts and images, and be framed only by our own pages.
HTML_POLICY = "; ".join([
    "default-src 'none'",
    "script-src 'none'",
    "style-src 'unsafe-inline' https://arxiv.org https://static.arxiv.org https://use.typekit.net https://p.typekit.net",
    "font-src https: data:",
    "img-src https: data:",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'self'",
])


@router.get("/papers/{key}/html")
def paper_html(
    key: str,
    request: Request,
    theme: Literal["light", "dark"] = "light",
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """The paper as arXiv HTML, cut down for the reader. 404 if arXiv has no HTML for it."""
    paper = get_paper(db, user, key)
    return serve_html(request.app.state.settings, paper.arxiv_id, paper.version, theme)


def serve_html(settings, arxiv_id: str, version: str | None, theme: str):
    path = settings.html_dir / f"{arxiv.to_key(arxiv_id)}{version or ''}.html"
    if path.exists():
        doc, source = path.read_text(), "cache"
    else:
        try:
            doc = arxiv.fetch_html(arxiv_id, version)
        except arxiv.NoHtml:
            raise HTTPException(404, "arXiv has no HTML version of this paper")
        except arxiv.ArxivError as e:
            raise HTTPException(502, str(e))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(doc)
        tmp.replace(path)
        source = "arxiv"
    return HTMLResponse(
        doc.replace(arxiv.THEME, theme, 1),
        headers={
            "Content-Security-Policy": HTML_POLICY,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=86400",
            "X-Html-Source": source,
        },
    )
