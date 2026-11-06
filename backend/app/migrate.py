"""Schema upgrades that `create_all` can't do on its own.

v1 -> v2 (multi-user): every row gains a user_id. Existing data goes to a new
admin account "owner" with no usable password; set one with
`python -m app.cli user passwd owner`.

New columns on existing tables are added in place, using the column's
server default for existing rows.

`users` is rebuilt with AUTOINCREMENT if it was made without it, so ids of
deleted accounts aren't reused.
"""

import logging

from sqlalchemy import Engine, MetaData, text
from sqlalchemy.schema import CreateTable
from sqlmodel import SQLModel

log = logging.getLogger(__name__)

LEGACY_USERNAME = "owner"
_PER_USER = ("papers", "paper_tags", "sessions", "events")


def _columns(conn, table: str) -> list[str]:
    return [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]


def migrate(engine: Engine) -> None:
    _to_multi_user(engine)
    _add_columns(engine)
    _users_autoincrement(engine)


def _drop_indexes(conn, table: str) -> None:
    for (name,) in conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=:t AND sql IS NOT NULL"), {"t": table}
    ).all():
        conn.execute(text(f'DROP INDEX "{name}"'))


def _users_autoincrement(engine: Engine) -> None:
    # SQLite can't add AUTOINCREMENT to a table, so: build a new one, copy, swap.
    # Renaming the old table first would repoint other tables' foreign keys at it.
    with engine.begin() as conn:
        sql = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")).scalar()
        if not sql or "AUTOINCREMENT" in sql.upper():
            return
        log.warning("rebuilding users with AUTOINCREMENT")
        users = SQLModel.metadata.tables["users"]
        cols = ", ".join(c for c in _columns(conn, "users") if c in users.c)
        _drop_indexes(conn, "users")
        conn.execute(CreateTable(users.to_metadata(MetaData(), name="_new_users")))
        conn.execute(text(f"INSERT INTO _new_users ({cols}) SELECT {cols} FROM users"))
        conn.execute(text("DROP TABLE users"))
        conn.execute(text("ALTER TABLE _new_users RENAME TO users"))
        for index in users.indexes:
            index.create(conn)


def _add_columns(engine: Engine) -> None:
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            existing = _columns(conn, table.name)
            if not existing:
                continue  # create_all makes it
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"
                if col.server_default is not None:
                    ddl += f" NOT NULL DEFAULT '{col.server_default.arg}'"
                log.warning("adding column %s.%s", table.name, col.name)
                conn.execute(text(ddl))


def _to_multi_user(engine: Engine) -> None:
    with engine.begin() as conn:
        cols = _columns(conn, "papers")
        if not cols or "user_id" in cols:
            return
        log.warning("migrating single-user database: existing data goes to user %r", LEGACY_USERNAME)
        old_cols = {t: _columns(conn, t) for t in _PER_USER}
        for t in _PER_USER:
            _drop_indexes(conn, t)
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
