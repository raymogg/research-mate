from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import arxiv, auth
from app.config import Settings
from app.main import create_app


def fake_metadata(arxiv_id: str) -> arxiv.Metadata:
    return arxiv.Metadata(
        arxiv_id=arxiv_id,
        version="v2",
        title=f"Paper {arxiv_id}",
        authors=["Ada Lovelace", "Alan Turing"],
        abstract="An abstract.",
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 20, tzinfo=UTC),
    )


class Feedback:
    """Routes forwarded events back into the app's own stub, and can be switched off."""

    def __init__(self):
        self.client: TestClient | None = None
        self.down = False
        self.calls = 0

    def __call__(self, body: dict) -> list[str]:
        self.calls += 1
        if self.down:
            raise ConnectionError("stub down")
        resp = self.client.post("/feedback/events", json=body)
        resp.raise_for_status()
        return resp.json()["accepted"]


@pytest.fixture
def feedback():
    return Feedback()


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", static_dir=tmp_path / "no-static", invite_code="letmein")


PASSWORD = "correct horse"


@pytest.fixture
def app(settings, feedback, monkeypatch):
    monkeypatch.setattr(arxiv, "fetch_metadata", fake_metadata)
    app = create_app(settings, feedback_sender=feedback)
    # No `with` on clients: skip lifespan so the retry loop doesn't run during tests.
    feedback.client = TestClient(app)
    return app


@pytest.fixture
def make_client(app):
    """A client signed in as a new user."""

    def make(username: str, is_admin: bool = False) -> TestClient:
        with Session(app.state.engine) as db:
            auth.create_user(db, username, PASSWORD, is_admin=is_admin)
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 200
        return c

    return make


@pytest.fixture
def client(make_client):
    return make_client("alice", is_admin=True)


@pytest.fixture
def anon(app):
    return TestClient(app)


@pytest.fixture
def add_paper(client):
    def add(input: str = "https://arxiv.org/abs/2401.12345", num_pages: int | None = 10) -> dict:
        paper = client.post("/api/papers", json={"input": input}).json()
        if num_pages:
            paper = client.patch(f"/api/papers/{paper['key']}", json={"num_pages": num_pages}).json()
        return paper

    return add


def dwell(arxiv_id: str, session_id: str, page: int, seconds: float, event_id: str | None = None) -> dict:
    return {
        "event_id": event_id or str(uuid4()),
        "type": "page_dwell",
        "arxiv_id": arxiv_id,
        "session_id": session_id,
        "ts": datetime.now(UTC).isoformat(),
        "payload": {"page": page, "active_ms": int(seconds * 1000)},
    }
