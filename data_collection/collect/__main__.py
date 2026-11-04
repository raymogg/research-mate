"""Build the paper catalog behind suggestions, and push it to the Fly app.

    uv run python -m collect fetch [--once]      # arXiv metadata, newest first, then new papers every 3 h
    uv run python -m collect embed [--once]      # SPECTER2 vectors on the GPU, newest first
    uv run python -m collect stats               # progress
    uv run python -m collect bench [--n 8000]    # GPU throughput
    uv run python -m collect push [--app NAME]   # snapshot -> Fly /data/catalog.db

fetch and embed are separate processes and can run at the same time; both
write to DATA_DIR/catalog.db.

Environment: DATA_DIR (./data), CATALOG_SETS (cs,stat,eess), FLY_APP
(taste-reader), EMBED_BATCH (128, papers per GPU forward pass).
"""

import argparse
import json
import logging
import os
from pathlib import Path

from . import catalog


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m collect", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    fe = sub.add_parser("fetch", help="harvest arXiv metadata")
    fe.add_argument("--once", action="store_true", help="fetch what's pending, then exit")
    em = sub.add_parser("embed", help="compute SPECTER2 embeddings")
    em.add_argument("--once", action="store_true", help="embed what's waiting, then exit")
    em.add_argument("--device", help="cuda, mps or cpu (default: the best available)")
    sub.add_parser("stats", help="papers, embeddings and months harvested")
    be = sub.add_parser("bench", help="measure embedding throughput")
    be.add_argument("--n", type=int, default=8000)
    pu = sub.add_parser("push", help="upload the catalog to the Fly app")
    pu.add_argument("--app", default=os.environ.get("FLY_APP", "taste-reader"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    data_dir = Path(os.environ.get("DATA_DIR", "./data"))
    db = data_dir / "catalog.db"
    batch = int(os.environ.get("EMBED_BATCH", "128"))

    try:
        if args.cmd == "fetch":
            from . import harvest

            sets = [s.strip() for s in os.environ.get("CATALOG_SETS", "cs,stat,eess").split(",") if s.strip()]
            logging.info("fetching %s into %s", ",".join(sets), db)
            harvest.run(catalog.connect(db), sets, once=args.once)
        elif args.cmd == "embed":
            from . import embed

            model = embed.Specter2(args.device, batch)
            logging.info("loaded %s on %s (%s); embedding into %s", model.name, model.device, model.dtype, db)
            embed.run(catalog.connect(db), model, once=args.once)
        elif args.cmd == "bench":
            from . import embed

            model = embed.Specter2(batch=batch)
            rate = embed.bench(model, args.n)
            print(f"{rate:.0f} papers/s on {model.device} ({model.dtype}, batch {batch}); "
                  f"1M papers in about {1e6 / rate / 3600:.1f} h")
        elif args.cmd == "stats":
            print(json.dumps(catalog.stats(catalog.connect(db)), indent=2))
            size = sum(p.stat().st_size for p in data_dir.glob("catalog.db*"))
            print(f"catalog.db on disk: {size / 2**30:.2f} GB")
        else:
            from . import push

            if not db.exists():
                raise SystemExit(f"No catalog at {db}. Run fetch first (or set DATA_DIR).")
            push.push(db, args.app, workdir=data_dir)
    except KeyboardInterrupt:
        logging.info("stopped; the window or batch in progress is done again next time")


if __name__ == "__main__":
    main()
