"""arXiv ID parsing, metadata lookup and PDF download."""

import html
import itertools
import logging
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

ABS_BASE = "https://arxiv.org/abs"
ABS_TIMEOUT_S = 10
EXPORT_API = "https://export.arxiv.org/api/query"
EXPORT_TIMEOUT_S = 15
PDF_BASE = "https://arxiv.org/pdf"
USER_AGENT = "taste-reader/0.1 (personal research reader)"
CHUNK = 256 * 1024

_NEW_ID = r"\d{4}\.\d{4,5}"
_OLD_ID = r"[a-z][a-z\-]*(?:\.[A-Z]{2})?/\d{7}"
_ID_RE = re.compile(rf"^(?P<id>{_NEW_ID}|{_OLD_ID})(?P<version>v\d+)?$")
_URL_RE = re.compile(r"^(?:https?://)?(?:www\.|export\.)?arxiv\.org/(?:abs|pdf)/(?P<rest>.+)$", re.I)

_ATOM = {"a": "http://www.w3.org/2005/Atom"}


class ArxivError(Exception):
    pass


class ArxivNotFound(ArxivError):
    pass


@dataclass
class ParsedId:
    arxiv_id: str
    version: str | None


@dataclass
class Metadata:
    arxiv_id: str
    version: str | None
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]
    published_at: datetime | None


def parse_id(raw: str) -> ParsedId | None:
    """Normalise an arXiv URL or ID to (id, version). Returns None if unrecognised."""
    s = raw.strip()
    if m := _URL_RE.match(s):
        s = m.group("rest")
    s = s.split("?")[0].split("#")[0].rstrip("/")
    s = re.sub(r"\.pdf$", "", s, flags=re.I)
    s = re.sub(r"^arxiv:", "", s, flags=re.I)
    m = _ID_RE.match(s)
    if not m:
        return None
    return ParsedId(m.group("id"), m.group("version"))


def to_key(arxiv_id: str) -> str:
    """URL-safe key: old-style IDs contain '/', which can't sit in a path segment."""
    return arxiv_id.replace("/", "_")


def from_key(key: str) -> str:
    return key.replace("_", "/")


def _text(el: ET.Element, path: str) -> str:
    found = el.find(path, _ATOM)
    return " ".join((found.text or "").split()) if found is not None else ""


def parse_feed(xml: str, arxiv_id: str) -> Metadata:
    root = ET.fromstring(xml)
    entry = root.find("a:entry", _ATOM)
    # Unknown IDs come back as an empty feed or as an "Error" entry whose id isn't an abs URL.
    parsed = parse_id(_text(entry, "a:id")) if entry is not None else None
    if entry is None or parsed is None:
        raise ArxivNotFound(arxiv_id)
    published = _text(entry, "a:published")
    return Metadata(
        arxiv_id=arxiv_id,
        version=parsed.version,
        title=_text(entry, "a:title"),
        authors=[_text(a, "a:name") for a in entry.findall("a:author", _ATOM)],
        abstract=_text(entry, "a:summary"),
        categories=[c.get("term", "") for c in entry.findall("a:category", _ATOM)],
        published_at=(
            datetime.fromisoformat(published).astimezone(UTC) if published else None
        ),
    )


def parse_abs_page(html_text: str, arxiv_id: str) -> Metadata:
    """Metadata from an abstract page's `citation_*` meta tags (the ones Google Scholar reads)."""
    meta = _MetaTags()
    meta.feed(html_text)
    title = meta.first("citation_title")
    if not title:
        raise ArxivError(f"arXiv abstract page for {arxiv_id} has no citation tags")
    versions = [int(v) for v in re.findall(r"\[v(\d+)\]", html_text)]
    subjects = re.search(r'class="tablecell subjects">(.*?)</td>', html_text, re.S)
    date = meta.first("citation_date")
    return Metadata(
        arxiv_id=arxiv_id,
        version=f"v{max(versions)}" if versions else None,
        title=" ".join(title.split()),
        # "Bahdanau, Dzmitry" -> "Dzmitry Bahdanau", as the export API gives them
        authors=[" ".join(reversed([p.strip() for p in a.split(",", 1)])) for a in meta.all("citation_author")],
        abstract=" ".join(meta.first("citation_abstract").split()),
        categories=re.findall(r"\(([a-z\-]+(?:\.[A-Za-z\-]+)?)\)", subjects.group(1)) if subjects else [],
        published_at=datetime.strptime(date, "%Y/%m/%d").replace(tzinfo=UTC) if date else None,
    )


class _MetaTags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("name") and a.get("content") is not None:
            self.tags.append((a["name"], html.unescape(a["content"])))

    def all(self, name: str) -> list[str]:
        return [v for k, v in self.tags if k == name]

    def first(self, name: str) -> str:
        return next(iter(self.all(name)), "")


def _get(url: str, timeout: float, **params) -> httpx.Response:
    resp = httpx.get(url, params=params or None, headers={"User-Agent": USER_AGENT}, timeout=timeout,
                     follow_redirects=True)
    resp.raise_for_status()
    return resp


def fetch_metadata(arxiv_id: str) -> Metadata:
    """Title, authors and so on, from the abstract page, or the export API if that fails.

    The abstract page answers in milliseconds; the export API is rate limited and
    can take tens of seconds or answer 429, so it is only the fallback.
    """
    try:
        return parse_abs_page(_get(f"{ABS_BASE}/{arxiv_id}", ABS_TIMEOUT_S).text, arxiv_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise ArxivNotFound(arxiv_id) from e
        first = e
    except (httpx.HTTPError, ArxivError) as e:
        first = e
    log.warning("arXiv abstract page failed for %s (%s); trying the export API", arxiv_id, first)
    try:
        return parse_feed(_get(EXPORT_API, EXPORT_TIMEOUT_S, id_list=arxiv_id).text, arxiv_id)
    except httpx.HTTPError as e:
        raise ArxivError(f"arXiv didn't answer ({first}; export API: {e}). Try again in a minute.") from e


def stream_pdf(arxiv_id: str, version: str | None, dest: Path) -> tuple[int | None, Iterator[bytes]]:
    """Start fetching a PDF from arXiv. Returns (size if known, chunks).

    The chunks are passed on to the browser as they arrive, so it can show
    progress, and are written to `dest` at the same time. Each download writes
    its own temp file and only a complete PDF replaces `dest`, so a dropped
    connection or a second download of the same paper never leaves a bad cache.
    """
    url = f"{PDF_BASE}/{arxiv_id}{version or ''}"
    client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)
    try:
        resp = client.send(client.build_request("GET", url), stream=True)
        resp.raise_for_status()
        chunks = resp.iter_bytes(CHUNK)
        first = next(chunks, b"")
    except httpx.HTTPError as e:
        client.close()
        raise ArxivError(f"arXiv PDF request failed: {e}") from e
    if not first.startswith(b"%PDF"):
        client.close()
        raise ArxivError("arXiv did not return a PDF")
    encoded = resp.headers.get("content-encoding", "identity") != "identity"
    size = None if encoded else int(resp.headers.get("content-length", 0)) or None

    def body() -> Iterator[bytes]:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".part")
        written = 0
        try:
            with os.fdopen(fd, "wb") as f:
                for chunk in itertools.chain([first], chunks):
                    f.write(chunk)
                    written += len(chunk)
                    yield chunk
            if size is None or written == size:
                os.replace(tmp, dest)
        finally:
            resp.close()
            client.close()
            Path(tmp).unlink(missing_ok=True)  # no-op once replaced

    return size, body()
