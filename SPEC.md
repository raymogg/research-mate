# arXiv Reader MVP — Spec

Status: agreed, ready to build · Last updated: 2026-10-30

## 1. Purpose

A single-user, local web app: paste an arXiv link, read the PDF in-app, and manage a personal library of papers with status, tags and a 1–5 rating. Every reading session produces implicit signals (time per page, total read time, return visits, completion) and explicit signals (rating, status, tags). These are stored locally and sent to a stubbed feedback API.

This is the observation layer for the wider research-taste project. Later, these signals become reward signals for a LoRA-tuned recommender. The reader should not need to change when that backend arrives: only the feedback endpoint URL changes.

**Principle: keep it as simple as possible.** When in doubt, leave it out.

## 2. Scope

### In scope

- Add a paper by pasting an arXiv URL or ID. Fetch its metadata and render the PDF in-app.
- Track active time per page, total active read time, return visits, and pages viewed.
- Set status to *Read* automatically when a paper is mostly read, and log that as a signal.
- Rate papers 1–5.
- Library view: every loaded paper with status, rating, tags (free text), read time and visits. Filter and sort.
- Persist everything locally so I can close the app and pick up later.
- Batch events and post them to a stub feedback API that logs them.
- Ship as one Docker image that runs locally.

### Out of scope (for now)

- Recommendations, seeding by research areas, the LoRA model, reward computation.
- Google/OAuth sign-in, password reset by email, sync between deployments, sign-in rate limiting.
- Non-arXiv sources, PDF uploads, annotations, highlights, notes.
- Full-text search, citation graphs, mobile layouts.

## 3. Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Idle cutoff and return-visit gap | 60 s idle pauses the clock. A session starting ≥ 30 min after the previous one ended is a return visit. Both are config values. |
| 2 | How *Read* is set | Automatically, when ≥ 80% of pages have been viewed. The change is logged as a `status_changed` event with `source: "auto"`. I can still override it by hand. |
| 3 | Tag structure | Plain free text, normalised (trimmed, lowercased). No kinds or prefixes. |
| 4 | Hosting | One Docker image, run locally or on one Fly.io machine with a volume (see `DEPLOY.md`). SQLite stays. |
| 5 | Explicit feedback | 1–5 rating per paper, part of the reading flow (see §4.4). |
| 6 | Users and auth | Username/password accounts in our own DB (stdlib scrypt hashes, HttpOnly session cookie). Sign-up needs `INVITE_CODE`; admins can also create accounts with the CLI. Each user has a separate library and event stream. Only admins see `/admin`. No external auth provider for now. |

## 4. User flows

### 4.1 Add a paper

1. Paste any of these into the add box: `https://arxiv.org/abs/2401.12345`, `https://arxiv.org/pdf/2401.12345v2`, `2401.12345`, `cs/0101001`.
2. The app normalises the input to an arXiv ID. Invalid input shows an inline error.
3. If the paper is new, the backend fetches metadata from the arXiv export API and saves it with status *Unread*. If it already exists, nothing is duplicated.
4. The reader opens.

### 4.2 Read a paper

1. Opening a paper starts a session. If status is *Unread*, it becomes *Reading* (`source: "auto"`).
2. The PDF renders page by page in a continuous scroll.
3. The clock runs only while the tab is visible **and** there has been input (scroll, mouse, key) within the idle cutoff. Time is credited to the page with the largest visible area.
4. A page counts as **viewed** once it has ≥ 5 s of cumulative active time.
5. When viewed pages / total pages ≥ 0.8 and the status is not already *Read*, the status becomes *Read* (`source: "auto"`) and the rating prompt appears (§4.4).
6. Closing the reader, navigating away or hiding the tab ends or pauses the session and flushes events.

### 4.3 Manage the library

- The home screen lists all papers. The default sort is last opened. Other sorts: date added, total read time, rating.
- Each row shows: title, first author + "et al.", arXiv ID, status, rating, tags, total read time, visits, pages viewed (e.g. `14/20`).
- Filter by status, by tag (one or more), and by rating (e.g. ≥ 4).
- Edit status, rating and tags inline. Tag input autocompletes from existing tags.

### 4.4 Rate a paper

- A 1–5 star control is always visible in the reader toolbar and on each library row. Clicking the current value clears it.
- When a paper is auto-marked *Read*, a small non-blocking prompt ("How useful was this paper?") appears in the reader. It can be dismissed and does not reappear for that paper.
- Every change emits a `rating_set` event, including clears (`to: null`).

## 5. Statuses

| Status | Set by | Meaning |
|---|---|---|
| Unread | default on add | Loaded, never opened |
| Reading | auto on first open, or manual | Opened, not finished |
| Read | auto at ≥ 80% pages viewed, or manual | Finished |
| Skimmed | manual | Looked through, not read properly |
| Abandoned | manual | Started and stopped on purpose |

Auto transitions only move forward (Unread → Reading → Read). They never overwrite a manual *Skimmed* or *Abandoned*. Once I set a status by hand, auto never downgrades it.

## 6. Metrics

All metrics are derived from raw events and sessions, never stored as separate counters that can drift.

| Metric | Definition |
|---|---|
| Active time per page | Sum of `page_dwell.active_ms` for that page |
| Total read time | Sum of active time across all pages and sessions |
| Visits | Number of sessions with `is_return_visit = true`, plus 1 for the first session |
| Pages viewed | Count of pages with ≥ 5 s cumulative active time |
| Completion | Pages viewed / `num_pages` |
| Furthest page | Highest page number with any active time |
| Last opened | `started_at` of the latest session |

## 7. Data model (SQLite)

```
users
  id              INTEGER PK
  username        TEXT UNIQUE     -- lowercase, 3-32 chars
  password_hash   TEXT            -- scrypt$salt$digest
  is_admin        BOOLEAN
  created_at      TIMESTAMP

logins                            -- one per signed-in browser
  token_hash      TEXT PK         -- SHA-256 of the cookie token
  user_id         INTEGER FK
  created_at      TIMESTAMP
  expires_at      TIMESTAMP

papers                            -- one row per paper per user's library
  user_id         INTEGER FK
  arxiv_id        TEXT            -- version-less, e.g. "2401.12345"; PK (user_id, arxiv_id)
  version         TEXT            -- e.g. "v2"
  title           TEXT
  authors         JSON            -- ["A. Author", ...]
  abstract        TEXT
  categories      JSON            -- ["cs.LG", ...]
  published_at    TIMESTAMP
  num_pages       INTEGER NULL    -- set when the PDF is first parsed
  status          TEXT            -- unread|reading|read|skimmed|abandoned
  status_source   TEXT            -- auto|manual
  rating          INTEGER NULL    -- 1..5
  rating_prompted BOOLEAN         -- prompt already shown
  added_at        TIMESTAMP
  last_opened_at  TIMESTAMP NULL

paper_tags
  user_id         INTEGER
  arxiv_id        TEXT
  tag             TEXT
  added_at        TIMESTAMP
  PK (user_id, arxiv_id, tag)

sessions
  session_id      TEXT PK         -- UUID, created by the server
  user_id         INTEGER
  arxiv_id        TEXT
  started_at      TIMESTAMP
  ended_at        TIMESTAMP NULL
  active_ms       INTEGER
  is_return_visit BOOLEAN

events
  event_id        TEXT PK         -- UUID, created by the client (idempotency key)
  user_id         INTEGER
  session_id      TEXT NULL
  arxiv_id        TEXT
  type            TEXT
  ts              TIMESTAMP
  payload         JSON
  reported_at     TIMESTAMP NULL  -- set when the feedback stub accepts it
```

Raw events are immutable and kept forever, so reward functions can be recomputed later. Paper metadata is copied into each user's row; a second user adding a known paper reuses it instead of calling arXiv. A database from before accounts is migrated on startup (`app/migrate.py`), and its rows go to an admin `owner` with no password.

## 8. Events

| Type | Payload | Emitted when |
|---|---|---|
| `paper_added` | `{ input }` | A new paper is saved |
| `paper_opened` | `{ is_return_visit }` | A session starts |
| `paper_closed` | `{ active_ms }` | A session ends |
| `page_dwell` | `{ page, active_ms }` | Focus leaves a page, or on flush with time pending |
| `status_changed` | `{ from, to, source }` | Any status change; `source` is `auto` or `manual` |
| `completion_reached` | `{ pages_viewed, num_pages }` | The ≥ 80% threshold is crossed (once per paper) |
| `rating_set` | `{ from, to, prompted }` | Rating set, changed or cleared; `prompted` is true if set from the prompt |
| `tag_added` / `tag_removed` | `{ tag }` | Tag change |

Every event shares this envelope:

```json
{
  "event_id": "7c1e9a52-…",
  "type": "page_dwell",
  "arxiv_id": "2401.12345",
  "session_id": "b3f0…",
  "ts": "2026-10-30T09:14:02Z",
  "payload": { "page": 4, "active_ms": 81250 }
}
```

### Delivery

- The client buffers events and posts them to `POST /api/events` every 15 s, on `visibilitychange` to hidden, and on `pagehide` (via `navigator.sendBeacon`).
- The backend stores them in `events` (ignoring duplicate `event_id`s), then forwards unreported events to the feedback endpoint. It sets `reported_at` on success and retries on the next flush if that fails.
- Result: each event reaches `feedback.log` exactly once, even if the stub was briefly down.

## 9. API

### Library API (`/api`)

| Method and path | Purpose |
|---|---|
| `POST /api/papers` | Body `{ input }`. Add by URL or ID; returns the paper (200 if it already exists, 201 if new) |
| `GET /api/papers?status=&tag=&min_rating=&sort=` | List papers with their metrics |
| `GET /api/papers/{id}` | One paper with metrics and per-page dwell |
| `PATCH /api/papers/{id}` | Body `{ status?, rating? }`. Manual updates (emit events server-side) |
| `GET /api/papers/{id}/pdf` | PDF proxied from arXiv and cached on disk |
| `PUT /api/papers/{id}/tags/{tag}` / `DELETE …` | Add or remove a tag |
| `GET /api/tags` | All tags with counts, for autocomplete |
| `POST /api/events` | Body `{ events: [...] }`. Client event batch |
| `POST /api/auth/signup` | Body `{ username, password, invite_code }`. Creates an account and signs in |
| `POST /api/auth/login` / `logout`, `GET /api/auth/me` | Session cookie `taste_login` |
| `GET /api/admin/...` | Admins only: `users`, `overview`, `events`, `sessions`, `papers`; `?user_id=` scopes to one user |

Every library route requires a signed-in user and only touches that user's rows; another user's paper is a 404.

### Feedback stub (`/feedback`)

This is the seam the future learning backend replaces. Its URL is set by `FEEDBACK_URL`.

| Method and path | Body / response |
|---|---|
| `POST /feedback/events` | `{ events: [{event_id, user_id, ...}] }` → `{ accepted: [event_id, ...] }`. Appends each event as one JSON line to `feedback.log`. Loopback callers only. |
| `GET /feedback/health` | `{ ok: true }` |

`user_id` is the numeric account id. Usernames are not forwarded.

## 10. Architecture

```
 Browser (React + PDF.js)          Backend (FastAPI)                 Storage / external
┌───────────────────────┐        ┌──────────────────────┐          ┌──────────────────────┐
│ Library view          │──────▶ │ Library API          │────────▶ │ SQLite (data volume) │
│ PDF reader            │──────▶ │ PDF proxy + cache    │────────▶ │ arXiv export API/PDF │
│ Event tracker (15 s)  │──────▶ │ Event store/forwarder│────────▶ │ Feedback stub        │
└───────────────────────┘        └──────────────────────┘          │  → feedback.log      │
                                                                    └──────────┬───────────┘
                                                                               ┆ later: same contract
                                                                    ┌──────────▼───────────┐
                                                                    │ Learning backend     │
                                                                    └──────────────────────┘
```

The browser talks only to our backend. arXiv PDFs can't be loaded cross-origin, so they go through the proxy.

### Stack

- **Frontend:** React, Vite, TypeScript. `pdfjs-dist` for rendering, `IntersectionObserver` for the page in view, the Page Visibility API for pausing.
- **Backend:** Python 3.12, FastAPI, SQLModel on SQLite, `httpx` for arXiv. Python keeps it next to the future PyTorch/LoRA code.
- **Stub:** a router in the same FastAPI app. `FEEDBACK_URL` defaults to `http://localhost:8000/feedback/events`.
- **Packaging:** one multi-stage Docker image. Node builds the frontend; FastAPI serves the static build and the API on port 8000. Data (`app.db`, `pdfs/`, `feedback.log`) lives in `/data`, mounted as a volume.

### Proposed layout

```
research-taste-project/
├── SPEC.md
├── README.md
├── Makefile               # make dev | make build | make run
├── Dockerfile
├── docker-compose.yml     # one service, ./data:/data
├── backend/
│   ├── pyproject.toml
│   └── app/
│       ├── main.py        # FastAPI app, static files
│       ├── config.py      # thresholds, paths, FEEDBACK_URL
│       ├── db.py, models.py
│       ├── arxiv.py       # ID parsing, metadata, PDF fetch
│       ├── routes/        # papers.py, events.py, tags.py, feedback_stub.py
│       └── metrics.py     # rollups + auto-status logic
│   └── tests/
└── frontend/
    ├── package.json
    └── src/
        ├── pages/         # Library.tsx, Reader.tsx
        ├── components/    # PdfView, RatingStars, TagInput, StatusSelect
        ├── tracking/      # dwell tracker, event buffer
        └── api.ts
```

## 11. Configuration

| Key | Default | Meaning |
|---|---|---|
| `IDLE_CUTOFF_S` | 60 | No input for this long pauses the clock |
| `RETURN_VISIT_GAP_MIN` | 30 | Minimum gap for a session to count as a return visit |
| `PAGE_VIEWED_MIN_S` | 5 | Active time for a page to count as viewed |
| `AUTO_READ_THRESHOLD` | 0.8 | Fraction of pages viewed that triggers auto *Read* |
| `FLUSH_INTERVAL_S` | 15 | Client event flush interval |
| `FEEDBACK_URL` | `http://localhost:8000/feedback/events` | Where events are forwarded |
| `DATA_DIR` | `./data` (`/data` in Docker) | SQLite db, PDF cache, `feedback.log` |
| `INVITE_CODE` | empty (`dev` via `make dev`) | Code required to sign up; empty closes sign-up |
| `COOKIE_SECURE` | `0` | `1` sends the login cookie over HTTPS only (production) |
| `LOGIN_DAYS` | 30 | How long a sign-in lasts |

The backend serves client-side thresholds via `GET /api/config`, so there is one source of truth.

## 12. Definition of done

- [ ] `docker compose up` starts the app at `http://localhost:8000`. `make dev` runs it with hot reload. README covers both.
- [ ] An abs link, a pdf link and a bare ID all open the same paper. Bad input shows an error.
- [ ] A 20-page paper renders and scrolls smoothly.
- [ ] Reading page 3 for about 2 minutes records 110–130 s on page 3. Hiding the tab or going idle for 60 s stops the clock.
- [ ] Reopening a paper an hour later adds a visit. Reopening after 5 minutes does not.
- [ ] Viewing 16 of 20 pages for ≥ 5 s each sets status to *Read* (`source: "auto"`) and shows the rating prompt once.
- [ ] A manually set *Abandoned* is not overwritten by auto *Read*.
- [ ] Rating set, changed and cleared each emit a `rating_set` event and show in the library.
- [ ] Status, rating and tags survive a browser refresh and a container restart.
- [ ] Filtering by tag, status and minimum rating returns exactly the matching papers.
- [ ] Every event appears exactly once in `feedback.log`, including after the stub was unavailable for a while.
- [ ] Backend tests cover arXiv ID parsing, metric rollups, auto-status rules and event idempotency.

## 13. Later (designed for, not built)

- Google sign-in, if more people join than can be handed accounts.
- Sign-in rate limiting, password reset.
- A `suggested` status and a `source` field on papers, so recommender proposals sit in the same library.
- The real learning backend replaces the stub behind `FEEDBACK_URL`.
