# Taste Reader

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

**Accounts.** Every page needs a signed-in user, and each user's library and events are separate. Sign up on the sign-in page with the invite code (`dev` locally; set `INVITE_CODE` to change it). Or create accounts from the terminal: `make user ARGS="add alice"`, `make user ARGS="list"`, `make user ARGS="passwd alice"`, `make user ARGS="admin alice"`. Only admins can see `/admin`. Data from before accounts existed belongs to the admin `owner`; give it a password with `make user ARGS="passwd owner"` (and `ARGS="rename owner <you>"` if you like).

## How it works

- **Add a paper:** paste an arXiv abs/pdf link or ID. The backend fetches metadata from the arXiv export API and proxies and caches the PDF (arXiv PDFs can't be loaded cross-origin).
- **Reading:** opening a paper starts a session. Time counts only while the tab is visible and you've interacted within the idle cutoff, and goes to the page with the largest visible area.
- **Return visits:** a session that starts 30+ minutes after the last activity on that paper counts as a return visit. Coming back to a tab hidden that long also starts a new session.
- **Auto status:** opening an unread paper marks it *Reading*. Viewing at least 80% of pages (5+ s each) marks it *Read* and asks for a 1–5 rating once. Auto changes never override *Skimmed* or *Abandoned*.
- **Events:** the browser buffers `page_dwell` and `paper_closed` events and flushes every 15 s, when the tab is hidden, and when the page closes. Unsent events wait in localStorage. Everything else (`paper_added`, `paper_opened`, `status_changed`, `completion_reached`, `rating_set`, `tag_added`/`tag_removed`) is emitted by the backend.
- **Feedback stub:** the backend forwards unreported events to `FEEDBACK_URL` (default: its own `/feedback/events`), which appends them to `feedback.log` as JSON lines. Events stay queued while the endpoint is down and are retried on later requests and every 60 s. Each event lands in the log exactly once.

## Inspecting captured data

- **Admin page:** http://localhost:5173/admin (dev) or http://localhost:8000/admin (Docker). Admins only. It shows users, totals, delivery health, every event type with its fields and counts, time per page for each paper, sessions, and a filterable event stream. The user picker scopes everything to one person.
- **Terminal:** `make stats` (summary), `make events` (last 30 events), `make export` (writes `users.csv`, `papers.csv`, `sessions.csv`, `page_dwell.csv` and `events.jsonl` to `data/exports/<timestamp>/`). In Docker: `docker compose exec reader python -m app.cli stats`.
- **Raw:** `sqlite3 data/app.db`, or `data/feedback.log` (one JSON event per line).

The event catalog (`EVENT_CATALOG` in `backend/app/events.py`) is the single description of what's captured; the admin page and CLI both read it.

## Configuration

Environment variables (defaults in brackets): `IDLE_CUTOFF_S` (60), `RETURN_VISIT_GAP_MIN` (30), `PAGE_VIEWED_MIN_S` (5), `AUTO_READ_THRESHOLD` (0.8), `FLUSH_INTERVAL_S` (15), `FEEDBACK_URL` (`http://localhost:8000/feedback/events`), `DATA_DIR` (`./data`; `/data` in Docker), `INVITE_CODE` (empty = sign-up closed; `make dev` uses `dev`), `COOKIE_SECURE` (`0`; set `1` behind HTTPS), `LOGIN_DAYS` (30).

## API

All `/api` routes except `/api/config` and `/api/auth/*` need a signed-in user, and act on that user's data.

| Method and path | Purpose |
|---|---|
| `POST /api/auth/signup` `{username, password, invite_code}` | Create an account and sign in |
| `POST /api/auth/login` `{username, password}`, `POST /api/auth/logout`, `GET /api/auth/me` | Session cookie |
| `POST /api/papers` `{input}` | Add by arXiv URL or ID (201 new, 200 existing) |
| `GET /api/papers?status=&tag=&min_rating=&sort=` | List with metrics. Repeat `tag` to require several |
| `GET /api/papers/{key}` | One paper with per-page dwell |
| `PATCH /api/papers/{key}` | `{status?, rating?, prompted?, rating_prompted?, num_pages?}` |
| `POST /api/papers/{key}/sessions` | Start a reading session |
| `GET /api/papers/{key}/pdf` | Cached PDF |
| `PUT`/`DELETE /api/papers/{key}/tags/{tag}` | Add or remove a tag |
| `GET /api/tags` | Tags with counts |
| `POST /api/events` `{events}` | Client event batch |
| `GET /api/config` | Thresholds for the client |
| `GET /api/admin/users`, `/overview`, `/events`, `/sessions`, `/papers` | Read-only views for admins; all but `users` take `?user_id=` |
| `POST /feedback/events`, `GET /feedback/health` | Feedback stub (POST from loopback only); each event carries `user_id` |

`key` is the arXiv ID with `/` replaced by `_` (old-style IDs like `cs/0101001` contain a slash).
