# Data collection: the paper catalog

Builds `catalog.db`, the papers suggestions are drawn from, and pushes it to
the Fly app. It runs entirely on the Ubuntu GPU machine (RTX PRO 5000), as
three separate processes:

| Command | What it does | Runs |
|---|---|---|
| `make fetch` | Harvests arXiv metadata (cs, stat, eess) over OAI-PMH, newest first back to 2005, then fetches the recent days again every 3 hours | Always |
| `make embed` | Computes SPECTER2 embeddings on the GPU for papers without one, newest first, then checks for new papers every minute | Always |
| `make push` | Takes a compact snapshot and installs it on the Fly app as `/data/catalog.db` | Daily, or by hand |

fetch and embed write to the same `data/catalog.db` (SQLite in WAL mode), so
they run side by side. Stopping either is safe at any point: fetch repeats only
the date window it was in, and embed saves every 1,024 papers.

```
arXiv OAI-PMH ──fetch──▶ data/catalog.db ◀──embed── SPECTER2 on the GPU
                              │
                            push (snapshot, gzip, sftp, integrity check, swap)
                              ▼
                     Fly: /data/catalog.db  (the server only reads it)
```

## Setup (once)

You need:
- an NVIDIA driver with CUDA 12.8 support (570 or later; check with `nvidia-smi`), which Blackwell cards require;
- [uv](https://docs.astral.sh/uv/).

```sh
# from the Mac, copy this directory over
scp -r data_collection <user>@<gpu-box>:~/taste-collect

# on the GPU box
curl -LsSf https://astral.sh/uv/install.sh | sh     # if uv isn't installed
cd ~/taste-collect
make setup        # installs torch (CUDA 12.8 wheels), transformers, adapters; prints the GPU
make bench        # papers/s and hours per 1M papers; first run downloads the model (~450 MB)
make test
```

`EMBED_BATCH` (default 128) sets how many papers go through the GPU at once.
On a 48 GB card, try `EMBED_BATCH=512 make bench` and keep whatever is fastest.

**For pushing,** install flyctl and sign in once:

```sh
curl -L https://fly.io/install.sh | sh
fly auth login                       # or, for unattended pushes, use a deploy token:
fly tokens create deploy -a taste-reader          # (run anywhere you're signed in)
echo "FLY_API_TOKEN=<token>" | sudo tee /etc/taste-collect.env && sudo chmod 600 /etc/taste-collect.env
```

**Optional head start.** The Mac already has about 150k papers (July to
November) in `data/catalog.db` at the project root, with no embeddings. Copy it
to `~/taste-collect/data/catalog.db` before the first fetch, and fetch will
skip the months that are done. Without it, fetch redoes them in about 10
minutes.

## Run

To watch it in two terminals (or tmux):

```sh
make fetch        # "harvested cs 2026-07-01..2026-07-31: 19808 records", about 2 min per month
make embed        # "embedded 1024 (… /s); N waiting, ~M min"
make stats        # papers, embeddings, months done, size
make push
```

As services, so they survive logouts and reboots:

```sh
make services                                        # writes the units to /etc/systemd/system (sudo)
sudo systemctl enable --now taste-fetch taste-embed taste-push.timer
journalctl -u taste-fetch -u taste-embed -f          # follow both
systemctl list-timers taste-push                     # next push (daily, 05:00)
sudo systemctl start taste-push                      # push now
```

## How long

- **fetch:** the full history (about 1M papers) takes about 4–6 hours. arXiv's
  feed is the limit, and we wait 3 s between requests to be polite.
- **embed:** runs as fast as the GPU allows; `make bench` gives the number. It
  starts on the newest papers, which fetch gets first, and catches up as fetch
  goes back in time.
- **push:** about 3 GB of catalog at 1M papers, roughly 2 GB once compressed.
  The upload time depends on your connection.

## What push does

1. `VACUUM INTO` takes a compact, consistent copy, safe while fetch and embed
   are running.
2. gzip compresses it.
3. It wakes the Fly machine and checks the volume has room for the upload plus
   the unpacked copy. If not, it stops and tells you how to extend the volume.
4. It uploads with `fly ssh sftp`. A small script on the server then unpacks
   the copy and runs SQLite's `quick_check`, and only then swaps it in as
   `/data/catalog.db`. A failed upload or check leaves the old catalog as it was.

On Fly's free trial a machine is stopped after 5 minutes, which is less than a
full-size upload takes. Add a card to the Fly account before the first big push.

## Details

- **Data:**
  - Papers: OAI-PMH `arXiv` format, which has the title, authors, abstract,
    categories (primary first), v1 date, DOI and journal ref.
  - Windows: whole months for history, single days for the current month.
    The last 3 days are fetched again on each pass and never marked done.
  - Sets: `CATALOG_SETS=cs,stat,eess`. A paper cross-listed in two sets is
    stored once.
- **Model:**
  - `allenai/specter2_base` with the proximity adapter (`allenai/specter2`).
    Input is title [SEP] abstract, up to 512 tokens. Output is the [CLS] vector:
    768 dimensions, stored as float16 with `model = specter2_proximity`.
  - It's the model Semantic Scholar serves. On 200 papers, the median cosine
    between our vector and theirs was 0.998.
  - Runs in float16 on CUDA.
- **Re-embedding with another model:**
  - Change `BASE`, `ADAPTER` and `MODEL_NAME` in `collect/embed.py`.
  - Clear the old vectors:
    `sqlite3 data/catalog.db "DELETE FROM embeddings; UPDATE papers SET embed_state = NULL"`
  - Run `make embed`.
- **Size:** about 1.5 KB of metadata plus 1.5 KB of vector per paper. The Fly
  volume is 10 GB, and a push briefly needs the upload plus the unpacked copy
  on top of the old catalog.
- **Testing on a Mac:** everything runs there too; embed uses Apple's GPU at
  ~40–90 papers/s. `DATA_DIR=/tmp/x make fetch` keeps a test catalog separate.
