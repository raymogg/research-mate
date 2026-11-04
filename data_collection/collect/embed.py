"""SPECTER2 embeddings for catalog papers, computed on this machine's GPU.

Model: allenai/specter2_base with the proximity adapter (allenai/specter2), as
in the model card. Input is title [SEP] abstract, at most 512 tokens; the
embedding is the [CLS] vector, 768 floats. It's the model Semantic Scholar
serves as `specter_v2`: on 200 papers our vectors had a median cosine of 0.998
to theirs.

`run` embeds papers without a vector, newest first, then checks every minute
for papers the harvester has added.
"""

import logging
import sqlite3
import time

import numpy as np

from . import catalog

log = logging.getLogger(__name__)

BASE = "allenai/specter2_base"
ADAPTER = "allenai/specter2"
MODEL_NAME = "specter2_proximity"
MAX_TOKENS = 512
CHUNK = 1024  # papers read and saved together
IDLE_S = 60


class Specter2:
    def __init__(self, device: str | None = None, batch: int = 128):
        import torch
        from adapters import AutoAdapterModel
        from transformers import AutoTokenizer

        self.torch = torch
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        self.device = device
        # Half precision on CUDA roughly doubles throughput; [CLS] vectors match
        # float32 to about 1e-3, far below what ranking can notice
        self.dtype = torch.float16 if device == "cuda" else torch.float32
        self.batch = batch
        self.tokenizer = AutoTokenizer.from_pretrained(BASE)
        model = AutoAdapterModel.from_pretrained(BASE)
        model.load_adapter(ADAPTER, source="hf", load_as="specter2", set_active=True)
        self.model = model.to(device, dtype=self.dtype).eval()
        self.dim = model.config.hidden_size
        self.name = MODEL_NAME

    def text(self, title: str, abstract: str) -> str:
        return " ".join(title.split()) + self.tokenizer.sep_token + " ".join((abstract or "").split())

    def embed(self, papers: list[tuple[str, str]]) -> np.ndarray:
        texts = [self.text(t, a) for t, a in papers]
        # Sorted by length so each forward pass pads little
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        out = np.empty((len(texts), self.dim), dtype=np.float32)
        with self.torch.inference_mode():
            for start in range(0, len(order), self.batch):
                idx = order[start:start + self.batch]
                inputs = self.tokenizer(
                    [texts[i] for i in idx], padding=True, truncation=True, max_length=MAX_TOKENS,
                    return_tensors="pt", return_token_type_ids=False,
                ).to(self.device)
                out[idx] = self.model(**inputs).last_hidden_state[:, 0, :].float().cpu().numpy()
        return out


def pack(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype="<f2").tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f2").astype(np.float32)


def waiting(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM papers WHERE embed_state IS NULL OR embed_state = 'missing'").fetchone()[0]


def step(con: sqlite3.Connection, model, chunk: int = CHUNK) -> int:
    """Embed and save one chunk of papers without a vector, newest first. Returns how many."""
    rows = con.execute(
        "SELECT arxiv_id, title, abstract FROM papers WHERE embed_state IS NULL OR embed_state = 'missing' "
        "ORDER BY created DESC LIMIT ?",
        (chunk,),
    ).fetchall()
    if not rows:
        return 0
    vecs = model.embed([(r["title"], r["abstract"]) for r in rows])
    ts = catalog.now()
    con.executemany(
        "INSERT OR REPLACE INTO embeddings (arxiv_id, model, dim, vector, s2_paper_id, fetched_at) "
        "VALUES (?, ?, ?, ?, NULL, ?)",
        [(r["arxiv_id"], model.name, vecs.shape[1], pack(v), ts) for r, v in zip(rows, vecs, strict=True)],
    )
    con.executemany(
        "UPDATE papers SET embed_state = 'done', embed_checked_at = ? WHERE arxiv_id = ?",
        [(ts, r["arxiv_id"]) for r in rows],
    )
    con.commit()
    return len(rows)


def run(con: sqlite3.Connection, model, once: bool = False, stop=lambda: False, sleep=time.sleep) -> int:
    """Embed until stopped (with `once`, until nothing is waiting). Returns papers embedded."""
    done, t0, idle = 0, time.monotonic(), False
    while not stop():
        n = step(con, model)
        if n == 0:
            if once:
                break
            if not idle:
                log.info("all papers embedded (%d this run); checking for new ones every minute", done)
            idle = True
            sleep(IDLE_S)
            continue
        idle = False
        done += n
        rate = done / max(time.monotonic() - t0, 1e-9)
        left = waiting(con)
        log.info("embedded %d (%d this run, %.0f/s); %d waiting, ~%.0f min", n, done, rate, left, left / rate / 60)
    return done


def bench(model, n: int = 4000) -> float:
    rng = np.random.default_rng(0)
    words = ["model", "learning", "graph", "neural", "network", "attention", "transformer", "data", "robust", "training", "method"]
    papers = [(" ".join(rng.choice(words, 10)), " ".join(rng.choice(words, int(rng.integers(120, 260)))))
              for _ in range(n)]
    model.embed(papers[:model.batch])  # warm up
    t0 = time.perf_counter()
    model.embed(papers)
    return n / (time.perf_counter() - t0)
