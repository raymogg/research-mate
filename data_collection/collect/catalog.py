"""The shared paper catalog: every arXiv paper in the harvested categories, for suggestions.

Built here and pushed to the server as /data/catalog.db, next to app.db
(per-user libraries). Plain sqlite3: it is large, bulk-loaded, and read with
vectors rather than through an ORM. The server only reads it.

    papers        metadata from arXiv's OAI-PMH feed (see harvest.py)
    embeddings    SPECTER2 vectors, little-endian float16 (see embed.py)
    windows       which OAI-PMH date windows have been fully harvested
"""

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    arxiv_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,            -- JSON list of "Forenames Keyname"
    abstract TEXT NOT NULL,
    categories TEXT NOT NULL,         -- JSON list, primary first
    primary_category TEXT NOT NULL,
    created TEXT,                     -- date of v1, YYYY-MM-DD
    updated TEXT,                     -- date of the latest version, if any
    datestamp TEXT NOT NULL,          -- OAI-PMH datestamp (last metadata change)
    doi TEXT,
    journal_ref TEXT,
    license TEXT,
    harvested_at TEXT NOT NULL,
    -- NULL: no embedding yet; 'done': in embeddings. ('missing' and the attempt
    -- count are from fetching Semantic Scholar's vectors, no longer used.)
    embed_state TEXT,
    embed_checked_at TEXT,
    embed_attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS papers_created ON papers (created);
CREATE INDEX IF NOT EXISTS papers_primary ON papers (primary_category, created);
CREATE INDEX IF NOT EXISTS papers_embed ON papers (embed_state, created);

CREATE TABLE IF NOT EXISTS embeddings (
    arxiv_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,              -- e.g. specter2_proximity
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,             -- little-endian float16
    s2_paper_id TEXT,                 -- only for vectors from Semantic Scholar
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS windows (
    oai_set TEXT NOT NULL,
    start TEXT NOT NULL,              -- inclusive YYYY-MM-DD
    until TEXT NOT NULL,              -- inclusive YYYY-MM-DD
    records INTEGER NOT NULL,
    finished_at TEXT NOT NULL,
    PRIMARY KEY (oai_set, start, until)
);
"""


@dataclass
class CatalogPaper:
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]
    created: str | None
    updated: str | None
    datestamp: str
    doi: str | None = None
    journal_ref: str | None = None
    license: str | None = None


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Autocommit off by default; callers commit per batch. WAL so the app can read
    # while the worker writes.
    con = sqlite3.connect(path, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def upsert_papers(con: sqlite3.Connection, papers: Iterable[CatalogPaper]) -> int:
    """Insert or refresh metadata. Embedding state is kept: an updated abstract
    rarely moves the embedding enough to matter."""
    ts = now()
    rows = [
        (p.arxiv_id, p.title, json.dumps(p.authors), p.abstract, json.dumps(p.categories),
         p.categories[0] if p.categories else "", p.created, p.updated, p.datestamp,
         p.doi, p.journal_ref, p.license, ts)
        for p in papers
    ]
    con.executemany(
        """
        INSERT INTO papers (arxiv_id, title, authors, abstract, categories, primary_category, created,
                            updated, datestamp, doi, journal_ref, license, harvested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (arxiv_id) DO UPDATE SET
            title = excluded.title, authors = excluded.authors, abstract = excluded.abstract,
            categories = excluded.categories, primary_category = excluded.primary_category,
            created = excluded.created, updated = excluded.updated, datestamp = excluded.datestamp,
            doi = excluded.doi, journal_ref = excluded.journal_ref, license = excluded.license,
            harvested_at = excluded.harvested_at
        """,
        rows,
    )
    return len(rows)


def window_done(con: sqlite3.Connection, oai_set: str, start: str, until: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM windows WHERE oai_set = ? AND start = ? AND until = ?", (oai_set, start, until)
    ).fetchone()
    return row is not None


def mark_window(con: sqlite3.Connection, oai_set: str, start: str, until: str, records: int) -> None:
    con.execute(
        "INSERT OR REPLACE INTO windows (oai_set, start, until, records, finished_at) VALUES (?, ?, ?, ?, ?)",
        (oai_set, start, until, records, now()),
    )


def latest_window_end(con: sqlite3.Connection, oai_set: str) -> str | None:
    row = con.execute("SELECT max(until) FROM windows WHERE oai_set = ?", (oai_set,)).fetchone()
    return row[0]


def stats(con: sqlite3.Connection) -> dict:
    def one(sql):
        return con.execute(sql).fetchone()[0]

    by_state = dict(con.execute("SELECT coalesce(embed_state, 'never'), count(*) FROM papers GROUP BY 1").fetchall())
    windows = con.execute(
        "SELECT oai_set, count(*), min(start), max(until), sum(records) FROM windows GROUP BY oai_set"
    ).fetchall()
    return {
        "papers": one("SELECT count(*) FROM papers"),
        "created_range": list(con.execute("SELECT min(created), max(created) FROM papers").fetchone()),
        "embeddings": one("SELECT count(*) FROM embeddings"),
        "embed_state": by_state,
        "windows": {w[0]: {"count": w[1], "from": w[2], "until": w[3], "records": w[4]} for w in windows},
        "top_categories": dict(
            con.execute(
                "SELECT primary_category, count(*) FROM papers GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
            ).fetchall()
        ),
    }
