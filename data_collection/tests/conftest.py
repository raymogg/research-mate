import pytest

from collect import catalog


@pytest.fixture
def con(tmp_path):
    c = catalog.connect(tmp_path / "catalog.db")
    yield c
    c.close()


def add(con, *ids_created):
    catalog.upsert_papers(con, [
        catalog.CatalogPaper(arxiv_id=i, title=f"title {i}", authors=[], abstract=f"abstract {i}",
                             categories=["cs.LG"], created=c, updated=None, datestamp=c)
        for i, c in ids_created
    ])
    con.commit()
