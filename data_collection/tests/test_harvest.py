"""Harvesting arXiv metadata over OAI-PMH."""

from datetime import date, timedelta

import httpx

from collect import catalog, harvest


def record(arxiv_id, datestamp="2026-11-02", created="2026-11-01", deleted=False):
    if deleted:
        return f"""<record><header status="deleted"><identifier>oai:arXiv.org:{arxiv_id}</identifier>
            <datestamp>{datestamp}</datestamp></header></record>"""
    return f"""<record><header><identifier>oai:arXiv.org:{arxiv_id}</identifier>
        <datestamp>{datestamp}</datestamp><setSpec>cs:cs:LG</setSpec></header>
      <metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/">
        <id>{arxiv_id}</id><created>{created}</created>
        <authors><author><keyname>Lovelace</keyname><forenames>Ada</forenames></author>
                 <author><keyname>Turing</keyname><forenames>Alan M.</forenames><suffix>Jr</suffix></author></authors>
        <title>A  title
          over lines</title>
        <categories>cs.LG stat.ML</categories>
        <doi>10.1/x</doi>
        <abstract>  An abstract.  </abstract>
      </arXiv></metadata></record>"""


def page(*records, token=None):
    tok = f"<resumptionToken cursor='0'>{token}</resumptionToken>" if token else "<resumptionToken cursor='0'/>"
    return f"""<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
        <ListRecords>{''.join(records)}{tok}</ListRecords></OAI-PMH>""".encode()


NO_RECORDS = b"""<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
    <error code="noRecordsMatch">none</error></OAI-PMH>"""


def no_sleep(_s):
    pass


def test_parse_page():
    papers, token = harvest.parse_page(page(record("2611.00001"), record("2611.00002", deleted=True), token="abc%3D1"))
    assert token == "abc%3D1"
    (p,) = papers
    assert (p.arxiv_id, p.title, p.abstract) == ("2611.00001", "A title over lines", "An abstract.")
    assert p.authors == ["Ada Lovelace", "Alan M. Turing Jr"]
    assert (p.categories, p.created, p.datestamp, p.doi) == (["cs.LG", "stat.ML"], "2026-11-01", "2026-11-02", "10.1/x")
    assert harvest.parse_page(page(record("2611.00003")))[1] is None
    assert harvest.parse_page(NO_RECORDS) == ([], None)


def test_plan_covers_every_day_once_settled():
    today = date(2026, 11, 15)
    windows = harvest.plan(today)
    assert windows[0] == (date(2026, 11, 13), today, False)  # the unsettled tail comes first
    assert windows[1] == (date(2026, 11, 12), date(2026, 11, 12), True)
    assert (date(2026, 10, 1), date(2026, 10, 31), True) in windows
    assert windows[-1][0] == harvest.EARLIEST
    covered = set()
    for start, until, _ in windows:
        d = start
        while d <= until:
            assert d not in covered
            covered.add(d)
            d += timedelta(days=1)
    assert len(covered) == (today - harvest.EARLIEST).days + 1


def test_plan_early_in_the_month():
    windows = harvest.plan(date(2026, 11, 1))
    assert windows[0] == (date(2026, 10, 30), date(2026, 11, 1), False)
    assert windows[1] == (date(2026, 10, 1), date(2026, 10, 29), True)


def test_harvest_follows_tokens_and_records_the_window(con):
    calls = []

    def handler(request: httpx.Request):
        calls.append(dict(request.url.params))
        if "resumptionToken" in request.url.params:
            return httpx.Response(200, content=page(record("2611.00002")))
        if len(calls) == 1:
            return httpx.Response(503, headers={"retry-after": "1"})
        return httpx.Response(200, content=page(record("2611.00001"), token="t%3D1"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    n = harvest.harvest_window(con, client, "cs", date(2026, 10, 1), date(2026, 10, 31), True, sleep=no_sleep)
    assert n == 2
    assert calls[1]["from"] == "2026-10-01" and calls[1]["set"] == "cs"
    assert calls[2] == {"verb": "ListRecords", "resumptionToken": "t%3D1"}
    assert catalog.window_done(con, "cs", "2026-10-01", "2026-10-31")
    assert catalog.stats(con)["papers"] == 2

    # The same records again only refresh metadata
    harvest.harvest_window(con, client, "stat", date(2026, 10, 1), date(2026, 10, 31), True, sleep=no_sleep)
    assert catalog.stats(con)["papers"] == 2


def test_run_once_skips_finished_windows(con):
    seen = []

    def handler(request):
        seen.append((request.url.params.get("set"), request.url.params.get("from")))
        return httpx.Response(200, content=NO_RECORDS)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    today = date(2026, 11, 15)
    assert harvest.run_once(con, client, ["cs", "stat"], today=today, max_windows=4, sleep=no_sleep) == 4
    seen.clear()
    harvest.run_once(con, client, ["cs", "stat"], today=today, max_windows=4, sleep=no_sleep)
    # The tail is fetched again; the two settled days already done are not
    assert seen == [("cs", "2026-11-13"), ("stat", "2026-11-13"), ("cs", "2026-11-11"), ("stat", "2026-11-11")]


def test_run_once_fetches_the_backlog_then_stops(con, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.params.get("from"))
        return httpx.Response(200, content=NO_RECORDS)

    monkeypatch.setattr(harvest, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(harvest, "EARLIEST", date.today().replace(day=1) - timedelta(days=40))
    harvest.run(con, ["cs"], once=True, sleep=no_sleep)
    assert harvest.pending(con, ["cs"]) == [w for w in harvest.pending(con, ["cs"]) if not w[3]]  # only the tail left
    assert len(seen) == len(set(seen))  # nothing fetched twice
