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
        assert paper.feedback_state == "none"  # opened before the post-visit questions existed
        assert db.get(Event, "e1").schema_version == 1
        assert db.get(PaperTag, (1, "2106.09685", "peft"))
        assert db.get(ReadingSession, "s1").user_id == 1
        event = db.get(Event, "e1")
        assert event.user_id == 1 and event.payload == {"page": 1, "active_ms": 9000} and event.reported_at


def _sql(path, name: str) -> str:
    return sqlite3.connect(path).execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]


def test_user_ids_are_never_reused(tmp_path):
    path = tmp_path / "app.db"
    make_engine(f"sqlite:///{path}")
    # Recreate users the way it was before AUTOINCREMENT, with three accounts
    old = sqlite3.connect(path)
    old.executescript("""
        DROP INDEX ix_users_username; DROP TABLE users;
        CREATE TABLE users (id INTEGER NOT NULL, username VARCHAR NOT NULL, password_hash VARCHAR NOT NULL,
          is_admin BOOLEAN NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id));
        CREATE UNIQUE INDEX ix_users_username ON users (username);
        INSERT INTO users VALUES (1, 'a', '!', 1, '2026-11-01'), (2, 'b', '!', 0, '2026-11-01'),
          (3, 'c', '!', 0, '2026-11-01');
    """)
    old.close()

    engine = make_engine(f"sqlite:///{path}")
    make_engine(f"sqlite:///{path}")  # second start is a no-op
    assert "AUTOINCREMENT" in _sql(path, "users")
    assert "REFERENCES users" in _sql(path, "api_tokens")  # other tables still point at the new table
    assert _sql(path, "ix_users_username").startswith("CREATE UNIQUE INDEX")
    with Session(engine) as db:
        assert [u.username for u in db.exec(select(User).order_by(User.id))] == ["a", "b", "c"]
        db.delete(db.get(User, 3))
        db.commit()
        db.add(User(username="d", password_hash="!"))
        db.commit()
        assert db.exec(select(User).where(User.username == "d")).one().id == 4
