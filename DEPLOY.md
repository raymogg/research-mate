# Deploying

One Fly.io machine (shared CPU, 2 GB RAM) runs the existing Docker image. It
stays on, so pages never wait for a cold start; a deploy restarts it for
10–20 s, and the browser buffers events meanwhile. A 10 GB volume holds
`/data` (`app.db`, `catalog.db`, `pdfs/`, `feedback.log`). SQLite stays: one
machine and a handful of users is well within what it handles. Expect roughly
$13–15/month: about $11–13 for the machine and $1.50 for the volume.

Why Fly: it runs the Dockerfile unchanged, gives HTTPS on `*.fly.dev`, and has
persistent volumes with daily snapshots. Railway works much the same way if you
prefer it. Render needs a paid plan for a disk.

## First deploy

```sh
brew install flyctl
fly auth login

# Edit fly.toml first: set a unique `app` name and your nearest `primary_region`.
fly apps create <app-name>
fly volumes create taste_data --size 1 --region <region>
# Set up Google and GitHub sign-in (below) first: they're the only ways in.
make deploy                                            # fly deploy --ha=false (one machine: SQLite)
```

Then sign in on the site (that creates your account) and make yourself an
admin: `make prod-user ARGS="admin <you>"`.

Open `https://<app-name>.fly.dev`.

**Bringing your local reading data along.** Do this instead of the last
command above, before anyone signs up. Uploading replaces the server's
database:

```sh
fly ssh sftp shell        # then: put data/app.db /data/app.db
fly machine restart
# Local data belongs to the admin "owner". Sign in on the site once (that makes
# a new, empty account), then hand its sign-in to owner:
make prod-user ARGS="move-signins <new-username> owner"
make prod-user ARGS="rename owner <you>"
```

## Custom domain

The app is served at `https://researchmate.org`. `PUBLIC_URL` in `fly.toml`
must match it. Requests to `www.researchmate.org` and `*.fly.dev` get a
permanent redirect to it, because session and OAuth cookies only work on one host.

Fly issues the certificates (`fly certs add researchmate.org` and
`fly certs add www.researchmate.org`, already done). DNS is at GoDaddy:

| Type  | Name  | Value                     |
|-------|-------|---------------------------|
| A     | `@`   | `66.241.124.163`          |
| AAAA  | `@`   | `2a09:8280:1::1e1:4a04:0` |
| CNAME | `www` | `taste-reader.fly.dev`    |

The Fly app (and its volume, `taste_data`) keeps its original name, `taste-reader`,
from before the rename to ResearchMate. Renaming it would mean a new app, a data
move and a new CNAME, and users never see it.

Check with `fly certs check researchmate.org` (and `www.researchmate.org`)
until both say Issued. `fly ips list` shows the addresses if they change.

## Sign in with Google and GitHub

Each provider needs an OAuth app. Its callback URL is
`$PUBLIC_URL/api/auth/oauth/<provider>/callback`; `PUBLIC_URL` is set in
`fly.toml`. A provider's button appears once both of its secrets are set.

**Google:**
1. In Google Cloud console, go to APIs & Services → OAuth consent screen.
2. Choose External, and add the app name, a support email and the scopes
   `openid`, `email` and `profile`. These basic scopes need no Google review.
   Use `https://researchmate.org/privacy` as the privacy policy link.
3. Publish the app. While it's "Testing", only listed test users can sign in.
4. Go to Credentials → Create credentials → OAuth client ID → Web application.
5. Under authorised redirect URIs, add
   `https://researchmate.org/api/auth/oauth/google/callback`.
6. Run `fly secrets set GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=...`

**GitHub:**
1. Go to Settings → Developer settings → OAuth Apps → New OAuth App.
2. Set the homepage to `https://researchmate.org` and the callback to
   `https://researchmate.org/api/auth/oauth/github/callback`.
3. Run `fly secrets set GITHUB_CLIENT_ID=... GITHUB_CLIENT_SECRET=...`

**Local testing:** create a second OAuth app per provider, with the callback
on `http://localhost:5173`. Then run
`GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... make dev`; `make backend` sets
`PUBLIC_URL` for you.

**How it works:**
- Google and GitHub are the only ways to sign in; there are no passwords.
  The first sign-in with an account creates a ResearchMate account. Sign-up
  is open to anyone.
- Once signed in, users can link the other provider from `/profile`.
- Accounts are matched on the provider's account id, never on email.
- Accounts from before this (they had passwords) can't sign in until they
  get a provider. The user signs in once, which makes a new empty account,
  and then you run `make prod-user ARGS="move-signins <new> <old>"`.
- Visitors who aren't signed in can read the five starter papers
  (`backend/app/starter.py`). Nothing is recorded for them.

## Your data: the profile page

`/profile` shows each user:
- their sign-ins;
- a summary of what's stored;
- every recorded event.

From there they can download everything as one JSON file, create API tokens
for the export, and delete their account. Deleting removes every row with
their `user_id` and their lines in `feedback.log`. It's immediate apart from
the backups below.

The export API is `GET /api/me/export` with
`Authorization: Bearer trk_...`. Tokens only work on that endpoint, and it has
no user parameter, so a token can only ever fetch its owner's data.

## Accounts

- **Sign-up:** open. Anyone who signs in with Google or GitHub gets an
  account.
- **Account commands:** `ARGS="list"`, `ARGS="rename alice alicia"`,
  `ARGS="admin alice"` / `ARGS="admin alice --revoke"`, and
  `ARGS="move-signins <new> <old>"` (see above).

Only admins see `/admin` and its API.

## Looking at the data

- **`/admin` on the site:** shows everything, with a per-user filter.
- **`make prod-stats`:** the same summary as `make stats`, run on the server.
- **`make prod-backup`:** copies a consistent snapshot of `app.db` to
  `./backups/`. Point the local tools at it, e.g.
  `cd backend && DATA_DIR=../backups/x uv run python -m app.cli export`
  (rename the file to `app.db` inside that dir first).

## Backups

Fly takes a snapshot of the volume once a day and keeps it for 14 days, set
by `snapshot_retention` in `fly.toml`. The existing volume was changed with
`fly volumes update <vol-id> --snapshot-retention 14`. If you change the
number, also set `BACKUP_RETENTION_DAYS`. The profile page tells users that
deleted data stays in backups for that long.

- **List snapshots:** `fly volumes snapshots list vol_v3g8j96o22ew9214`
- **Snapshot before something risky:** `fly volumes snapshots create vol_v3g8j96o22ew9214`
- **Restore:**
  1. Create a new volume from a snapshot:
     `fly volumes create taste_data --snapshot-id <vs_...> --region syd --size 10`.
  2. Point the machine at it:
     `fly machine update <machine-id> --mount-volume <new vol id>:/data`
     (or destroy the old volume and redeploy).
  3. Check the data, then delete the old volume.

A snapshot is taken while the app runs. SQLite recovers from it the way it
recovers from a power cut, so it's safe. `make prod-backup` gives you a copy
on your laptop as well.

## The paper catalog

`/data/catalog.db` holds the arXiv papers and embeddings behind suggestions.
It's built on the GPU machine and uploaded from there by `data_collection/`
(`make push`, or a daily systemd timer). See `data_collection/README.md`. The
server only reads it. A push swaps in the new file only after it passes an
integrity check.

## Notes

- **Cookies:** login cookies are HttpOnly, SameSite=Lax and HTTPS-only (`COOKIE_SECURE=1`). They last 30 days (`LOGIN_DAYS`).
- **Feedback stub:** `/feedback/events` only accepts the app's own loopback calls, so outsiders can't write to `feedback.log`.
- **Brute-force protection:** there is no rate limit on sign-in yet. scrypt makes each guess slowish. Add a limit before sharing widely.
- **Scaling:** don't scale past one machine. Each machine would get its own volume and its own SQLite file.
