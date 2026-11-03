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
    ],
)
def test_parse_id_accepts(raw, arxiv_id, version):
    parsed = parse_id(raw)
    assert parsed is not None
    assert (parsed.arxiv_id, parsed.version) == (arxiv_id, version)


@pytest.mark.parametrize(
    "raw", ["", "hello", "https://example.com/abs/2401.12345", "2401.123", "24011.12345", "https://arxiv.org/list/cs.LG"]
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

