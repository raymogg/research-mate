"""Stand-in for the future learning backend. Appends events to feedback.log as JSON lines."""

import json
import threading

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..models import iso, utcnow

router = APIRouter(prefix="/feedback")

_lock = threading.Lock()


class FeedbackBatch(BaseModel):
    events: list[dict]  # each carries its own user_id


LOCAL = {"127.0.0.1", "::1", "localhost", "testclient"}


def _load_seen(path) -> set[str]:
    seen = set()
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                seen.add(json.loads(line)["event_id"])
    return seen


@router.post("/events")
def receive(body: FeedbackBatch, request: Request):
    # Only our own forwarder may write here; the app is reachable from the internet.
    if request.client is None or request.client.host not in LOCAL:
        raise HTTPException(403)
    state = request.app.state
    path = state.settings.feedback_log
    accepted = []
    with _lock:
        if state.feedback_seen is None:
            state.feedback_seen = _load_seen(path)
        seen = state.feedback_seen
        path.parent.mkdir(parents=True, exist_ok=True)
        received_at = iso(utcnow())
        with path.open("a") as f:
            for e in body.events:
                event_id = e.get("event_id")
                if not event_id:
                    continue
                if event_id not in seen:  # exactly once, even if a batch is resent
                    f.write(json.dumps({**e, "received_at": received_at}) + "\n")
                    seen.add(event_id)
                accepted.append(event_id)
    return {"accepted": accepted}


@router.get("/health")
def health():
    return {"ok": True}
