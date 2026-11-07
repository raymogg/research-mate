"""Harvest arXiv metadata into the catalog over OAI-PMH (https://oaipmh.arxiv.org/oai).

Work is split into date windows on the OAI datestamp (when a record last
changed), so a restart only repeats the window it was in:

- whole calendar months for history, newest first, back to arXiv's earliest
  datestamp (2005-09-16; older papers carry later datestamps, so they're covered);
- single days for the current month;
- the last few days, which may still change, are fetched every pass and never
  marked done.

Upserts are idempotent, so overlapping windows and sets cost only bandwidth.

`run` loops forever: the recent days every few hours, and in between one
backlog window at a time until history is complete.
"""

import logging
import sqlite3
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import httpx

from . import catalog
from .catalog import CatalogPaper

log = logging.getLogger(__name__)

OAI_URL = "https://oaipmh.arxiv.org/oai"
USER_AGENT = "researchmate-collect/0.1 (+https://researchmate.org)"
EARLIEST = date(2005, 9, 16)
UNSETTLED_DAYS = 2  # windows ending this close to today may still change
RECHECK_S = 3 * 3600  # how often to fetch the recent days again
PAGE_DELAY_S = 3  # between requests, to be polite
TIMEOUT_S = 120

_NS = {"o": "http://www.openarchives.org/OAI/2.0/", "a": "http://arxiv.org/OAI/arXiv/"}


class HarvestError(Exception):
    pass


def _text(el: ET.Element | None, path: str) -> str | None:
    found = el.find(path, _NS) if el is not None else None
    if found is None or found.text is None:
        return None
    return " ".join(found.text.split())


def parse_page(xml: bytes) -> tuple[list[CatalogPaper], str | None]:
    """Records and the resumption token (None on the last page)."""
    root = ET.fromstring(xml)
    if (err := root.find("o:error", _NS)) is not None:
        if err.get("code") == "noRecordsMatch":
            return [], None
        raise HarvestError(f"{err.get('code')}: {err.text}")
    papers = []
    for rec in root.iterfind("o:ListRecords/o:record", _NS):
        header = rec.find("o:header", _NS)
        if header is None or header.get("status") == "deleted":
            continue
        meta = rec.find("o:metadata/a:arXiv", _NS)
        arxiv_id = _text(meta, "a:id")
        title = _text(meta, "a:title")
        if not arxiv_id or not title:
            continue
        authors = []
        for a in meta.iterfind("a:authors/a:author", _NS):
            parts = [_text(a, "a:forenames"), _text(a, "a:keyname"), _text(a, "a:suffix")]
            authors.append(" ".join(p for p in parts if p))
        papers.append(
            CatalogPaper(
                arxiv_id=arxiv_id,
                title=title,
                authors=authors,
                abstract=_text(meta, "a:abstract") or "",
                categories=(_text(meta, "a:categories") or "").split(),
                created=_text(meta, "a:created"),
                updated=_text(meta, "a:updated"),
                datestamp=_text(header, "o:datestamp") or "",
                doi=_text(meta, "a:doi"),
                journal_ref=_text(meta, "a:journal-ref"),
                license=_text(meta, "a:license"),
            )
        )
    token_el = root.find("o:ListRecords/o:resumptionToken", _NS)
    token = token_el.text.strip() if token_el is not None and token_el.text and token_el.text.strip() else None
    return papers, token


def _get(client: httpx.Client, params: dict, sleep=time.sleep) -> bytes:
    for attempt in range(8):
        try:
            resp = client.get(OAI_URL, params=params)
        except httpx.HTTPError as e:
            wait = min(30 * 2**attempt, 600)
            log.warning("OAI-PMH request failed (%s); retrying in %ss", e, wait)
            sleep(wait)
            continue
        if resp.status_code == 503:  # arXiv's flow control: come back after Retry-After
            wait = int(resp.headers.get("retry-after", "30") or 30)
            log.info("OAI-PMH busy; retrying in %ss", wait)
            sleep(wait)
            continue
        if resp.status_code >= 500:
            sleep(min(30 * 2**attempt, 600))
            continue
        resp.raise_for_status()
        return resp.content
    raise HarvestError(f"gave up on {params}")


def fetch_window(
    client: httpx.Client, oai_set: str, start: date, until: date, sleep=time.sleep
) -> Iterator[list[CatalogPaper]]:
    """Pages of records whose datestamp falls in [start, until]."""
    params = {"verb": "ListRecords", "metadataPrefix": "arXiv", "set": oai_set,
              "from": start.isoformat(), "until": until.isoformat()}
    while True:
        papers, token = parse_page(_get(client, params, sleep))
        yield papers
        if not token:
            return
        sleep(PAGE_DELAY_S)
        params = {"verb": "ListRecords", "resumptionToken": token}


def _month_end(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def plan(today: date) -> list[tuple[date, date, bool]]:
    """Windows to harvest, most useful first: (start, until, settled).

    Settled windows are recorded once harvested and skipped from then on."""
    settled_until = today - timedelta(days=UNSETTLED_DAYS + 1)
    windows = [(settled_until + timedelta(days=1), today, False)]
    month_start = today.replace(day=1)
    d = settled_until
    while d >= month_start:  # this month: one window per settled day
        windows.append((d, d, True))
        d -= timedelta(days=1)
    m = month_start
    while m > EARLIEST:  # earlier months, newest first
        end = m - timedelta(days=1)
        m = end.replace(day=1)
        if end > settled_until:  # month ended inside the unsettled tail
            windows.append((m, settled_until, True))
        else:
            windows.append((max(m, EARLIEST), end, True))
    return windows


def harvest_window(
    con: sqlite3.Connection, client: httpx.Client, oai_set: str, start: date, until: date, settled: bool,
    sleep=time.sleep,
) -> int:
    n = 0
    for papers in fetch_window(client, oai_set, start, until, sleep):
        n += catalog.upsert_papers(con, papers)
        con.commit()
    if settled:
        catalog.mark_window(con, oai_set, start.isoformat(), until.isoformat(), n)
        con.commit()
    log.info("harvested %s %s..%s: %d records", oai_set, start, until, n)
    return n


def pending(con: sqlite3.Connection, sets: list[str], today: date | None = None) -> list[tuple]:
    """(set, start, until, settled) still to fetch, in plan order. The unsettled
    tail is always included."""
    today = today or datetime.now(UTC).date()
    return [
        (s, start, until, settled)
        for start, until, settled in plan(today)
        for s in sets
        if not (settled and catalog.window_done(con, s, start.isoformat(), until.isoformat()))
    ]


def run_once(
    con: sqlite3.Connection, client: httpx.Client, sets: list[str], today: date | None = None,
    max_windows: int | None = None, stop=lambda: False, sleep=time.sleep,
) -> int:
    """Fetch pending windows in order. Returns the number fetched."""
    fetched = 0
    for oai_set, start, until, settled in pending(con, sets, today):
        if stop() or (max_windows is not None and fetched >= max_windows):
            break
        harvest_window(con, client, oai_set, start, until, settled, sleep)
        fetched += 1
        sleep(PAGE_DELAY_S)
    return fetched


def make_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def run(con: sqlite3.Connection, sets: list[str], once: bool = False, stop=lambda: False, sleep=time.sleep) -> None:
    """Harvest until stopped. With `once`, fetch what's pending and return."""
    tail_at = -float("inf")  # fetch the recent days first
    with make_client() as client:
        while not stop():
            todo = pending(con, sets)
            backlog = [w for w in todo if w[3]]
            tail_due = time.monotonic() - tail_at >= RECHECK_S
            for oai_set, start, until, settled in ([w for w in todo if not w[3]] if tail_due else backlog[:1]):
                harvest_window(con, client, oai_set, start, until, settled, sleep)
                sleep(PAGE_DELAY_S)
            if tail_due:
                tail_at = time.monotonic()
            if backlog:
                continue
            if once:
                return
            log.info("history complete; checking for new papers again in %.0f min",
                     (RECHECK_S - (time.monotonic() - tail_at)) / 60)
            sleep(max(0.0, RECHECK_S - (time.monotonic() - tail_at)))
