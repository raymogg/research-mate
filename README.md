# ResearchMate

An arXiv reader that records how each user reads: time per page, total read time, return visits, completion, ratings, status and tags. Events are stored in SQLite and forwarded to a stub feedback API (`feedback.log`), which the research-taste learning backend will replace later.

See [SPEC.md](SPEC.md) for the full spec and [DEPLOY.md](DEPLOY.md) for hosting on Fly.io.

## Run it (Docker)

```sh
docker compose up --build
```

Open http://localhost:8000. Your data lives in `./data` (`app.db`, cached `pdfs/`, `feedback.log`), so it survives restarts and rebuilds.

## Develop

Needs [uv](https://docs.astral.sh/uv/) and Node 20+.

```sh
make install   # once
make dev       # backend on :8000 (reload) + Vite on :5173
make test      # backend tests + frontend typecheck
```

Open http://localhost:5173 while developing. Dev data also goes to `./data`.

**Accounts.** Signing in is with Google or GitHub only (set up a dev OAuth app per provider, see DEPLOY.md, and run `GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... make dev`). The first sign-in creates the account. Each user's library and events are separate. Signed out, visitors can read the five starter papers in `backend/app/starter.py`, and nothing is recorded. From the terminal: `make user ARGS="list"`, `make user ARGS="admin alice"`, `make user ARGS="rename alice alicia"`. Only admins can see `/admin`. Data from before accounts existed belongs to the admin `owner`: sign in once, which makes a new empty account, then `make user ARGS="move-signins <new> owner"`.

## How it works

- **Add a paper:** paste an arXiv abs/pdf link or ID. The backend fetches metadata from the arXiv abstract page (export API as fallback) and proxies and caches the PDF (arXiv PDFs can't be loaded cross-origin).
- **Reading:** opening a paper starts a session. Time counts only while the tab is visible and you've interacted within the idle cutoff, and goes to the page with the largest visible area.
- **Return visits:** a session that starts 30+ minutes after the last activity on that paper counts as a return visit. Coming back to a tab hidden that long also starts a new session.
- **Auto status:** opening an unread paper marks it *Reading*. Viewing at least 80% of pages (5+ s each) marks it *Read* and asks for a 1–5 rating once. Auto changes never override *Skimmed* or *Abandoned*.
- **Questions:** after the first visit to a paper, a dialog asks how interesting, valuable and novel it was (1–5), whether it matched your level, plus optional reason chips. It appears on leaving the reader, or on the next visit if the tab was closed. Asked once per paper; skipping counts.
- **Events:** the browser buffers `page_dwell`, `paper_closed`, `text_copied`, `zoom_changed` and `impression` events and flushes every 15 s, when the tab is hidden, and when the page closes. Unsent events wait in localStorage. Everything else (`paper_added`, `paper_opened`, `status_changed`, `completion_reached`, `rating_set`, `paper_feedback`, `feedback_skipped`, `tag_added`/`tag_removed`) is emitted by the backend. Every event carries `schema_version` and the thresholds in force (`config`).
- **Feedback stub:** the backend forwards unreported events to `FEEDBACK_URL` (default: its own `/feedback/events`), which appends them to `feedback.log` as JSON lines. Events stay queued while the endpoint is down and are retried on later requests and every 60 s. Each event lands in the log exactly once.

## Inspecting captured data

- **Admin page:** http://localhost:5173/admin (dev) or http://localhost:8000/admin (Docker). Admins only. It shows users, totals, delivery health, every event type with its fields and counts, time per page for each paper, sessions, and a filterable event stream. The user picker scopes everything to one person.
- **Terminal:** `make stats` (summary), `make events` (last 30 events), `make export` (writes `users.csv`, `papers.csv`, `sessions.csv`, `page_dwell.csv`, `impressions.csv` (with the session each impression led to, if any) and `events.jsonl` to `data/exports/<timestamp>/`). In Docker: `docker compose exec reader python -m app.cli stats`.
- **Raw:** `sqlite3 data/app.db`, or `data/feedback.log` (one JSON event per line).

The event catalog (`EVENT_CATALOG` in `backend/app/events.py`) is the single description of what's captured; the admin page and CLI both read it.

## Paper catalog (for suggestions)

`data_collection/` builds `catalog.db` (arXiv metadata plus SPECTER2 embeddings) on a GPU machine and pushes it to the server's `/data`. See `data_collection/README.md`.

## Configuration

Environment variables (defaults in brackets): `IDLE_CUTOFF_S` (60), `RETURN_VISIT_GAP_MIN` (30), `PAGE_VIEWED_MIN_S` (5), `AUTO_READ_THRESHOLD` (0.8), `FLUSH_INTERVAL_S` (15), `FEEDBACK_URL` (`http://127.0.0.1:8000/feedback/events`), `DATA_DIR` (`./data`; `/data` in Docker), `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` and `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` (sign-in), `PUBLIC_URL`, `COOKIE_SECURE` (`0`; set `1` behind HTTPS), `LOGIN_DAYS` (30).

## API

All `/api` routes except `/api/config`, `/api/auth/*` and `/api/starter/*` need a signed-in user, and act on that user's data.

| Method and path | Purpose |
|---|---|
| `POST /api/auth/oauth/{google,github}/start` `{mode: login\|link}` | Where to send the browser to sign in; a first sign-in creates the account |
| `POST /api/auth/logout`, `GET /api/auth/me` | Session cookie |
| `GET /api/starter`, `GET /api/starter/{key}/pdf\|html` | Starter papers, open to visitors; nothing recorded |
| `POST /api/papers` `{input, source?, intent?}` | Add by arXiv URL or ID (201 new, 200 existing); `source` is where they found it, or `suggested` from the starter shelf |
| `GET /api/papers?status=&tag=&min_rating=&sort=` | List with metrics. Repeat `tag` to require several |
| `GET /api/papers/{key}` | One paper with per-page dwell |
| `PATCH /api/papers/{key}` | `{status?, rating?, prompted?, rating_prompted?, num_pages?}` |
| `POST /api/papers/{key}/sessions` | Start a reading session; optional body `{ context }` (SPEC §4.2) |
| `GET /api/papers/{key}/pdf` | Cached PDF |
| `PUT`/`DELETE /api/papers/{key}/tags/{tag}` | Add or remove a tag |
| `GET /api/tags` | Tags with counts |
| `POST /api/events` `{events}` | Client event batch |
| `GET /api/feedback/pending`, `POST /api/papers/{key}/feedback` | Post-visit questions: what's waiting, and answer or skip |
| `GET /api/config` | Thresholds for the client |
| `GET /api/admin/users`, `/overview`, `/events`, `/sessions`, `/papers` | Read-only views for admins; all but `users` take `?user_id=` |
| `POST /feedback/events`, `GET /feedback/health` | Feedback stub (POST from loopback only); each event carries `user_id` |

`key` is the arXiv ID with `/` replaced by `_` (old-style IDs like `cs/0101001` contain a slash).
