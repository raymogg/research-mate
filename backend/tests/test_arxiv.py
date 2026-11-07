import pytest

from app import arxiv
from app.arxiv import ArxivNotFound, from_key, parse_feed, parse_id, to_key


@pytest.mark.parametrize(
    "raw, arxiv_id, version",
    [
        ("https://arxiv.org/abs/2401.12345", "2401.12345", None),
        ("https://arxiv.org/pdf/2401.12345", "2401.12345", None),
        ("https://arxiv.org/pdf/2401.12345v2.pdf", "2401.12345", "v2"),
        ("http://export.arxiv.org/abs/2401.12345v3?context=cs", "2401.12345", "v3"),
        ("arxiv.org/abs/1706.03762/", "1706.03762", None),
        ("2401.12345", "2401.12345", None),
        ("  arXiv:2401.1234v1 ", "2401.1234", "v1"),
        ("cs/0101001", "cs/0101001", None),
        ("https://arxiv.org/abs/hep-th/9901001v2", "hep-th/9901001", "v2"),
        ("math.AG/0101001", "math.AG/0101001", None),
        # every arXiv page type
        ("https://arxiv.org/html/2606.12683v1", "2606.12683", "v1"),
        ("https://arxiv.org/html/2606.12683v1/", "2606.12683", "v1"),
        ("https://arxiv.org/html/2606.12683v1/#S3.SS2", "2606.12683", "v1"),
        ("https://arxiv.org/html/2606.12683v1/x1.png", "2606.12683", "v1"),
        ("https://arxiv.org/html/2606.12683", "2606.12683", None),
        ("https://arxiv.org/format/2401.12345", "2401.12345", None),
        ("https://arxiv.org/src/2401.12345v1", "2401.12345", "v1"),
        ("https://arxiv.org/e-print/2401.12345", "2401.12345", None),
        ("https://arxiv.org/ps/hep-th/9901001v1", "hep-th/9901001", "v1"),
        ("https://arxiv.org/pdf/hep-th/9901001v1.pdf", "hep-th/9901001", "v1"),
        ("https://arxiv.org/html/hep-th/9901001", "hep-th/9901001", None),
        ("HTTPS://ARXIV.ORG/ABS/2401.12345", "2401.12345", None),
        ("https://browse.arxiv.org/abs/2401.12345", "2401.12345", None),
        ("0704.0001", "0704.0001", None),
        # mirrors and sites that link to arXiv papers
        ("https://ar5iv.labs.arxiv.org/html/1706.03762", "1706.03762", None),
        ("https://ar5iv.org/abs/1706.03762", "1706.03762", None),
        ("https://www.alphaxiv.org/abs/2401.12345v2", "2401.12345", "v2"),
        ("https://huggingface.co/papers/2401.12345", "2401.12345", None),
        ("https://doi.org/10.48550/arXiv.2401.12345", "2401.12345", None),
        ("10.48550/arXiv.2401.12345", "2401.12345", None),
        ("xxx.lanl.gov/abs/hep-th/9901001", "hep-th/9901001", None),
        ("arXiv: 2401.12345", "2401.12345", None),
    ],
)
def test_parse_id_accepts(raw, arxiv_id, version):
    parsed = parse_id(raw)
    assert parsed is not None
    assert (parsed.arxiv_id, parsed.version) == (arxiv_id, version)


@pytest.mark.parametrize(
    "raw",
    [
        "", "hello", "https://example.com/abs/2401.12345", "2401.123", "24011.12345", "https://arxiv.org/list/cs.LG",
        "https://arxiv.org/html/", "https://arxiv.org/abs/2401.12345vx", "https://huggingface.co/models/2401.12345",
        "https://evilarxiv.org/abs/2401.12345", "https://arxiv.org.evil.com/abs/2401.12345",
    ],
)
def test_parse_id_rejects(raw):
    assert parse_id(raw) is None


def test_key_round_trip():
    for arxiv_id in ("2401.12345", "cs/0101001", "math.AG/0101001"):
        assert from_key(to_key(arxiv_id)) == arxiv_id
    assert "/" not in to_key("cs/0101001")


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <published>2017-06-12T17:57:34Z</published>
    <title>Attention Is All
      You Need</title>
    <summary>  The dominant sequence transduction models...  </summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <category term="cs.CL"/><category term="cs.LG"/>
  </entry>
</feed>"""

ERROR_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>http://arxiv.org/api/errors#incorrect_id_format_for_9999.99999</id><title>Error</title></entry>
</feed>"""


def test_parse_feed():
    meta = parse_feed(FEED, "1706.03762")
    assert meta.title == "Attention Is All You Need"
    assert meta.version == "v7"
    assert meta.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert meta.categories == ["cs.CL", "cs.LG"]
    assert meta.abstract == "The dominant sequence transduction models..."
    assert meta.published_at.year == 2017


@pytest.mark.parametrize("xml", [ERROR_FEED, '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'])
def test_parse_feed_not_found(xml):
    with pytest.raises(ArxivNotFound):
        parse_feed(xml, "9999.99999")



def test_parse_id_strips_tracking_parameters():
    url = ("https://arxiv.org/abs/1409.0473?fbclid=IwAR0exampleFakeClickId0000000000000000000000000000"
           "-not-a-real-id_aem_FAKE000000000000000000")
    assert parse_id(url).arxiv_id == "1409.0473"
    assert parse_id("arxiv.org/pdf/1409.0473v2.pdf?utm_source=x#page=3").version == "v2"


ABS_PAGE = """<html><head>
<meta name="citation_title" content="Neural Machine Translation by Jointly
  Learning to Align and Translate" />
<meta name="citation_author" content="Bahdanau, Dzmitry" />
<meta name="citation_author" content="Cho, Kyunghyun" />
<meta name="citation_date" content="2014/09/01" />
<meta name="citation_abstract" content="Neural machine translation &amp; more." />
</head><body>
<td class="tablecell subjects"><span class="primary-subject">Computation and Language (cs.CL)</span>;
 Machine Learning (cs.LG); Machine Learning (stat.ML)</td>
<strong>[v1]</strong> Mon, 1 Sep 2014 <strong>[v7]</strong> Thu, 19 May 2016
</body></html>"""


def test_parse_abs_page():
    from app.arxiv import parse_abs_page

    m = parse_abs_page(ABS_PAGE, "1409.0473")
    assert m.title == "Neural Machine Translation by Jointly Learning to Align and Translate"
    assert m.authors == ["Dzmitry Bahdanau", "Kyunghyun Cho"]
    assert m.abstract == "Neural machine translation & more."
    assert m.categories == ["cs.CL", "cs.LG", "stat.ML"]
    assert m.version == "v7"
    assert m.published_at.year == 2014


def _response(url, status, text=""):
    import httpx

    return httpx.Response(status, text=text, request=httpx.Request("GET", url))


def test_fetch_falls_back_to_export_api(monkeypatch):
    import httpx


    calls = []

    def fake_get(url, timeout, **params):
        calls.append(url)
        if url.startswith(arxiv.ABS_BASE):
            raise httpx.ReadTimeout("slow")
        return _response(url, 200, FEED)

    monkeypatch.setattr(arxiv, "_get", fake_get)
    assert arxiv.fetch_metadata("1706.03762").title.startswith("Attention")
    assert calls == [f"{arxiv.ABS_BASE}/1706.03762", arxiv.EXPORT_API]


def test_fetch_unknown_id_and_both_down(monkeypatch):


    def missing(url, timeout, **params):
        resp = _response(url, 404)
        resp.raise_for_status()

    monkeypatch.setattr(arxiv, "_get", missing)
    with pytest.raises(ArxivNotFound):
        arxiv.fetch_metadata("2501.99999")

    def limited(url, timeout, **params):
        resp = _response(url, 429)
        resp.raise_for_status()

    monkeypatch.setattr(arxiv, "_get", limited)
    with pytest.raises(arxiv.ArxivError, match="Try again in a minute"):
        arxiv.fetch_metadata("1409.0473")
