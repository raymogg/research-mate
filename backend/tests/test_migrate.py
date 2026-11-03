import sqlite3

from sqlmodel import Session, select

from app.db import make_engine
from app.models import Event, Paper, PaperTag, ReadingSession, User

V1 = """
CREATE TABLE papers (arxiv_id VARCHAR PRIMARY KEY, version VARCHAR, title VARCHAR NOT NULL, authors JSON,
  abstract VARCHAR NOT NULL, categories JSON, published_at DATETIME, num_pages INTEGER, status VARCHAR NOT NULL,
  status_source VARCHAR NOT NULL, rating INTEGER, rating_prompted BOOLEAN NOT NULL, added_at DATETIME NOT NULL,
  last_opened_at DATETIME);
CREATE TABLE tags (name VARCHAR PRIMARY KEY);
CREATE TABLE paper_tags (arxiv_id VARCHAR, tag VARCHAR, added_at DATETIME NOT NULL, PRIMARY KEY (arxiv_id, tag));
CREATE TABLE sessions (session_id VARCHAR PRIMARY KEY, arxiv_id VARCHAR NOT NULL, started_at DATETIME NOT NULL,
  ended_at DATETIME, active_ms INTEGER NOT NULL, is_return_visit BOOLEAN NOT NULL);
CREATE INDEX ix_sessions_arxiv_id ON sessions (arxiv_id);
CREATE TABLE events (event_id VARCHAR PRIMARY KEY, session_id VARCHAR, arxiv_id VARCHAR NOT NULL, type VARCHAR NOT NULL,
  ts DATETIME NOT NULL, payload JSON, reported_at DATETIME);
CREATE INDEX ix_events_type ON events (type);
INSERT INTO papers VALUES ('2106.09685', 'v2', 'LoRA', '[]', '', '[]', NULL, 26, 'reading', 'auto', 4, 0,
  '2026-10-30 05:26:38', NULL);
INSERT INTO tags VALUES ('peft');
INSERT INTO paper_tags VALUES ('2106.09685', 'peft', '2026-10-30 05:27:00');
INSERT INTO sessions VALUES ('s1', '2106.09685', '2026-10-30 05:27:00', NULL, 9000, 0);
INSERT INTO events VALUES ('e1', 's1', '2106.09685', 'page_dwell', '2026-10-30 05:27:09',
  '{"page": 1, "active_ms": 9000}', '2026-10-30 05:27:10');
"""


def test_v1_database_moves_to_owner(tmp_path):
    path = tmp_path / "app.db"
    sqlite3.connect(path).executescript(V1)
    engine = make_engine(f"sqlite:///{path}")
    make_engine(f"sqlite:///{path}")  # second start is a no-op
    with Session(engine) as db:
        (owner,) = db.exec(select(User)).all()
        assert owner.username == "owner" and owner.is_admin and owner.id == 1
        paper = db.get(Paper, (1, "2106.09685"))
        assert paper.rating == 4 and paper.num_pages == 26
        assert db.get(PaperTag, (1, "2106.09685", "peft"))
        assert db.get(ReadingSession, "s1").user_id == 1
        event = db.get(Event, "e1")
        assert event.user_id == 1 and event.payload == {"page": 1, "active_ms": 9000} and event.reported_at
