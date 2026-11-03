"""arXiv ID parsing, metadata lookup and PDF download."""

import itertools
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

EXPORT_API = "https://export.arxiv.org/api/query"
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


def fetch_metadata(arxiv_id: str) -> Metadata:
    try:
        resp = httpx.get(
            EXPORT_API,
            params={"id_list": arxiv_id},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise ArxivError(f"arXiv metadata request failed: {e}") from e
    return parse_feed(resp.text, arxiv_id)


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
