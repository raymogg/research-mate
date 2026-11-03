"""Schema upgrades that `create_all` can't do on its own.

v1 -> v2 (multi-user): every row gains a user_id. Existing data goes to a new
admin account "owner" with no usable password; set one with
`python -m app.cli user passwd owner`.
"""

import logging

from sqlalchemy import Engine, text
from sqlmodel import SQLModel

log = logging.getLogger(__name__)

LEGACY_USERNAME = "owner"
_PER_USER = ("papers", "paper_tags", "sessions", "events")


def _columns(conn, table: str) -> list[str]:
    return [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]


def migrate(engine: Engine) -> None:
    with engine.begin() as conn:
        cols = _columns(conn, "papers")
        if not cols or "user_id" in cols:
            return
        log.warning("migrating single-user database: existing data goes to user %r", LEGACY_USERNAME)
        old_cols = {t: _columns(conn, t) for t in _PER_USER}
        for t in _PER_USER:
            for (name,) in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=:t AND sql IS NOT NULL"),
                {"t": t},
            ).all():
                conn.execute(text(f'DROP INDEX "{name}"'))
            conn.execute(text(f"ALTER TABLE {t} RENAME TO _old_{t}"))
        conn.execute(text("DROP TABLE IF EXISTS tags"))
        SQLModel.metadata.create_all(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, username, password_hash, is_admin, created_at) "
                "VALUES (1, :name, '!', 1, CURRENT_TIMESTAMP)"
            ),
            {"name": LEGACY_USERNAME},
        )
        for t in _PER_USER:
            cols = ", ".join(old_cols[t])
            conn.execute(text(f"INSERT INTO {t} (user_id, {cols}) SELECT 1, {cols} FROM _old_{t}"))
            conn.execute(text(f"DROP TABLE _old_{t}"))
