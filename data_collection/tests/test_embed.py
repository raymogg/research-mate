"""Embedding catalog papers (with a stand-in for the model)."""

import numpy as np

from collect import catalog, embed

from .conftest import add


class FakeModel:
    name, dim, batch = "fake", 4, 2

    def __init__(self):
        self.calls = []

    def embed(self, papers):
        self.calls.append([t for t, _ in papers])
        return np.array([[len(t), len(a), 0, 1] for t, a in papers], dtype=np.float32)


def test_newest_first_in_chunks(con):
    add(con, ("2401.00001", "2024-01-01"), ("2611.00001", "2026-11-01"), ("2611.00002", "2026-11-02"))
    model = FakeModel()
    assert embed.step(con, model, chunk=2) == 2
    assert model.calls == [["title 2611.00002", "title 2611.00001"]]
    assert embed.waiting(con) == 1
    assert embed.run(con, model, once=True) == 1
    rows = {r["arxiv_id"]: r for r in con.execute("SELECT * FROM embeddings")}
    assert set(rows) == {"2401.00001", "2611.00001", "2611.00002"}
    assert (rows["2401.00001"]["model"], rows["2401.00001"]["dim"]) == ("fake", 4)
    assert list(embed.unpack(rows["2401.00001"]["vector"])) == [16, 19, 0, 1]
    assert catalog.stats(con)["embed_state"] == {"done": 3}


def test_new_papers_are_picked_up(con):
    model, waits = FakeModel(), []
    add(con, ("2611.00001", "2026-11-01"))

    def sleep(s):
        waits.append(s)
        add(con, ("2611.00009", "2026-11-03"))  # the harvester adds one while we wait

    stops = iter([False, False, False, True])
    assert embed.run(con, model, stop=lambda: next(stops), sleep=sleep) == 2
    assert waits == [embed.IDLE_S]


def test_semantic_scholar_leftovers_are_embedded(con):
    add(con, ("2611.00001", "2026-11-01"))
    con.execute("UPDATE papers SET embed_state = 'missing'")
    assert embed.run(con, FakeModel(), once=True) == 1
