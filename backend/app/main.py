import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from .config import Settings
from .db import make_engine
from .events import Sender, http_sender
from .routes import admin, auth, events, feedback_stub, me, oauth, paper_feedback, papers, starter, tags
from .routes.common import forward_now

logging.basicConfig(level=logging.INFO)
RETRY_INTERVAL_S = 60


def create_app(settings: Settings | None = None, feedback_sender: Sender | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def retry_loop():
            # Catch events left unreported while the feedback endpoint was down.
            while True:
                await asyncio.to_thread(forward_now, app)
                await asyncio.sleep(RETRY_INTERVAL_S)

        task = asyncio.create_task(retry_loop())
        yield
        task.cancel()


    app = FastAPI(title="ResearchMate", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = make_engine(settings.db_url)
    app.state.feedback_sender = feedback_sender or http_sender(settings)
    app.state.feedback_seen = None

    canonical = urlsplit(settings.public_url)
    if canonical.hostname:

        @app.middleware("http")
        async def canonical_host(request: Request, call_next):
            # One host for everyone: session and OAuth cookies only work on the host that set them.
            # Health checks reach the machine by its own address and aren't redirected.
            host = (request.url.hostname or "").lower()
            if host != canonical.hostname and (host == f"www.{canonical.hostname}" or host.endswith(".fly.dev")):
                target = request.url.replace(scheme=canonical.scheme, netloc=canonical.netloc)
                return RedirectResponse(str(target), status_code=308)
            return await call_next(request)

    app.include_router(auth.router)
    app.include_router(oauth.router)
    app.include_router(me.router)
    app.include_router(papers.router)
    app.include_router(starter.router)
    app.include_router(tags.router)
    app.include_router(paper_feedback.router)
    app.include_router(events.router)
    app.include_router(feedback_stub.router)
    app.include_router(admin.router)

    static = settings.static_dir.resolve()
    if static.is_dir():

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "feedback/")):
                raise HTTPException(404)
            file = (static / path).resolve()
            if path and file.is_file() and file.is_relative_to(static):
                return FileResponse(file)
            return FileResponse(static / "index.html")

    return app

