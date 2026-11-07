"""A user's own data: everything we hold about them, to export or delete.

Both work from the schema rather than a list of tables: every table with a
`user_id` column holds per-user data. A table added later is exported and
deleted without touching this file (tests check it).
"""

import json
import os
from datetime import datetime
from typing import Any

from sqlalchemy import Table, delete, select
from sqlmodel import Session, SQLModel

from .config import Settings
from .models import SCHEMA_VERSION, User, iso, utcnow

# Never exported: hashes of secrets, and sign-ins still in flight
SECRET_COLUMNS = {"password_hash", "token_hash", "state_hash", "verifier"}
SKIP_TABLES = {"oauth_attempts"}


def user_tables() -> list[Table]:
    return [t for t in SQLModel.metadata.sorted_tables if "user_id" in t.c and t.name != "users"]


def _plain(value: Any) -> Any:
    return iso(value) if isinstance(value, datetime) else value


def _row(row) -> dict:
    return {k: _plain(v) for k, v in row._mapping.items() if k not in SECRET_COLUMNS}


def export(db: Session, user: User) -> dict:
    out: dict[str, Any] = {
        "exported_at": iso(utcnow()),
        "schema_version": SCHEMA_VERSION,
        "user": {
            "id": user.id,
            "username": user.username,
            "is_admin": user.is_admin,
            "created_at": iso(user.created_at),
        },
    }
    for table in user_tables():
        if table.name in SKIP_TABLES:
            continue
        rows = db.connection().execute(select(table).where(table.c.user_id == user.id))
        out[table.name] = [_row(r) for r in rows]
    return out


def delete_user(db: Session, settings: Settings, user: User) -> dict[str, int]:
    """Deletes the user and every row of theirs, and their lines in feedback.log. Returns counts."""
    counts = {}
    conn = db.connection()
    for table in reversed(user_tables()):
        counts[table.name] = conn.execute(delete(table).where(table.c.user_id == user.id)).rowcount
    db.delete(user)
    db.commit()
    counts["feedback_log"] = purge_feedback_log(settings, user.id)
    return counts


def purge_feedback_log(settings: Settings, user_id: int) -> int:
    """Rewrites feedback.log without this user's events. Returns lines removed."""
    from .routes.feedback_stub import lock  # the stub appends to the same file

    path = settings.feedback_log
    with lock:
        if not path.exists():
            return 0
        kept, removed = [], 0
        for line in path.read_text().splitlines():
            if line.strip() and json.loads(line).get("user_id") == user_id:
                removed += 1
            else:
                kept.append(line)
        if removed:
            tmp = path.with_suffix(".tmp")
            tmp.write_text("".join(f"{line}\n" for line in kept))
            os.replace(tmp, path)
    return removed
