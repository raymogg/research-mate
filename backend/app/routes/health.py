"""Health for Fly's checks and outside uptime monitors. Public; says nothing about users.

- 200 when the app can read its database; 503 when it can't. Fly stops routing
  to a machine that fails, so only a real outage fails it.
- `disk_ok` turns false when the data volume is nearly full. That doesn't fail
  the check (the site still works); watch it with a keyword monitor.
"""

import shutil

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session

from ..db import get_db

router = APIRouter(prefix="/api")

MIN_FREE_SHARE = 0.10  # warn below 10% free


@router.get("/health")
def health(request: Request, db: Session = Depends(get_db)):
    try:
        db.exec(text("SELECT 1 FROM users LIMIT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    usage = shutil.disk_usage(request.app.state.settings.data_dir)
    free = usage.free / usage.total if usage.total else 0
    body = {
        "ok": db_ok,
        "db_ok": db_ok,
        "disk_ok": free >= MIN_FREE_SHARE,
        "disk_free_pct": round(free * 100, 1),
    }
    return JSONResponse(body, status_code=200 if db_ok else 503, headers={"Cache-Control": "no-store"})
