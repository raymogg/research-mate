"""arXiv ID parsing, metadata lookup, and PDF and HTML download."""

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
from urllib.parse import urljoin

import httpx

log = logging.getLogger(__name__)

ABS_BASE = "https://arxiv.org/abs"
ABS_TIMEOUT_S = 10
EXPORT_API = "https://export.arxiv.org/api/query"
EXPORT_TIMEOUT_S = 15
PDF_BASE = "https://arxiv.org/pdf"
HTML_BASE = "https://arxiv.org/html"
HTML_TIMEOUT_S = 30
USER_AGENT = "taste-reader/0.1 (personal research reader)"
CHUNK = 256 * 1024

_NEW_ID = r"\d{4}\.\d{4,5}"
_OLD_ID = r"[a-z][a-z\-]*(?:\.[A-Z]{2})?/\d{7}"
# The ID, then nothing, ".pdf", or more path (e.g. an HTML figure: 2606.12683v1/x1.png)
_ID_RE = re.compile(rf"^(?P<id>{_NEW_ID}|{_OLD_ID})(?P<version>v\d+)?(?:\.pdf|/.*)?$")
# Everything that can come before an ID: arXiv's own pages (abs, pdf, html, ...)
# on any of its hosts, its mirrors, and the sites people share arXiv papers from
_PREFIXES = (
    r"(?:[a-z0-9-]+\.)*arxiv\.org/(?:abs|pdf|html|format|src|e-print|ps|dvi|view)/",  # arxiv, export, ar5iv.labs
    r"ar5iv\.org/(?:abs|html)/",
    r"xxx\.lanl\.gov/(?:abs|pdf)/",
    r"(?:www\.)?alphaxiv\.org/(?:abs|overview|pdf)/",
    r"(?:huggingface\.co|hf\.co)/papers/",
    r"(?:dx\.)?doi\.org/10\.48550/arxiv\.",
    r"10\.48550/arxiv\.",  # a bare arXiv DOI
    r"arxiv:\s*",
)
_URL_RE = re.compile(rf"^(?:https?://)?(?:{'|'.join(_PREFIXES)})(?P<rest>.+)$", re.I)

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


# --- HTML -----------------------------------------------------------------
#
# arXiv renders most papers to HTML with LaTeXML (https://arxiv.org/html/<id>).
# We keep the paper (its <article>) and arXiv's stylesheets, and drop the site
# around it and every script. The document is served from our own origin with
# a policy that blocks scripts, so the reader can measure what's on screen.

THEME = "__THEME__"  # replaced with light or dark when served

_SCRIPT_RE = re.compile(r"<(script|iframe|object|embed|form)\b.*?</\1\s*>|<(?:script|iframe|object|embed)\b[^>]*/?>", re.I | re.S)
_HANDLER_RE = re.compile(r"""\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.I)
_URL_ATTR_RE = re.compile(r"""(\s(?:src|href)\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
_STYLESHEET_RE = re.compile(r"<link\b[^>]*rel=[\"']?stylesheet[^>]*>", re.I)
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
_LINK_OUT_RE = re.compile(r"""<a\b(?=[^>]*\shref="https?:)(?![^>]*\starget=)""", re.I)

_SITE_CSS = ("design-system", "header-footer")  # these style arXiv's site around the paper
_READER_CSS = """
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; }
.ltx_page_main { margin: 0 auto; }
.ltx_page_content { padding: 8px 16px 64px; }
img, svg, video { max-width: 100%; height: auto; }
.ltx_equation, .ltx_tabular, .ltx_table, .ltx_figure, table { max-width: 100%; overflow-x: auto; }
"""


class NoHtml(ArxivNotFound):
    """arXiv has no HTML for this paper (it couldn't convert the source, or it's PDF-only)."""


def fetch_html(arxiv_id: str, version: str | None) -> str:
    """The reader document for a paper, built from arXiv's HTML. Raises NoHtml or ArxivError.

    Tries the version asked for, then the latest: arXiv doesn't have HTML for
    every old version.
    """
    urls = [f"{HTML_BASE}/{arxiv_id}{version}"] if version else []
    urls.append(f"{HTML_BASE}/{arxiv_id}")
    for url in urls:
        try:
            resp = _get(url, HTML_TIMEOUT_S)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                continue
            raise ArxivError(f"arXiv HTML request failed: {e}") from e
        except httpx.HTTPError as e:
            raise ArxivError(f"arXiv HTML request failed: {e}") from e
        return reader_document(resp.text, str(resp.url))
    raise NoHtml(arxiv_id)


def reader_document(page: str, url: str) -> str:
    """arXiv's HTML page cut down to the paper. Raises NoHtml if it holds no paper."""
    start = page.find('<article class="ltx_document')
    end = page.rfind("</article>")
    if start < 0 or end < start:
        raise NoHtml(url)
    head = page[:start]
    article = page[start:end + len("</article>")]
    title = re.search(r"<title>(.*?)</title>", head, re.S)
    sheets = []
    for tag in _STYLESHEET_RE.findall(head):
        href = _HREF_RE.search(tag)
        if href and not any(site in href.group(1) for site in _SITE_CSS):
            sheets.append(urljoin(url, html.unescape(href.group(1))))
    article = _HANDLER_RE.sub("", _SCRIPT_RE.sub("", article))

    def absolute(m: re.Match) -> str:
        value = html.unescape(m.group(3)).strip()
        if value.startswith("#"):
            return m.group(0)  # same-document links keep working
        if value.lower().startswith(("javascript:", "vbscript:")):
            value = "#"
        elif not value.lower().startswith(("data:", "mailto:")):
            value = urljoin(url, value)
        return f'{m.group(1)}"{html.escape(value)}"'

    article = _URL_ATTR_RE.sub(absolute, article)
    article = _LINK_OUT_RE.sub('<a target="_blank" rel="noopener noreferrer"', article)
    links = "".join(f'<link rel="stylesheet" href="{html.escape(h)}">' for h in sheets)
    return (
        f'<!DOCTYPE html><html lang="en" data-theme="{THEME}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title.group(1).strip() if title else ''}</title>{links}<style>{_READER_CSS}</style></head>"
        f'<body><div class="ltx_page_main"><div class="ltx_page_content">{article}</div></div></body></html>'
    )
