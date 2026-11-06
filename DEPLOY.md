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
fly secrets set INVITE_CODE="$(openssl rand -hex 6)"   # note the code: friends need it to sign up
make deploy                                            # fly deploy --ha=false (one machine: SQLite)

make prod-user ARGS="add <you> --admin"                # prompts for a password
```

Open `https://<app-name>.fly.dev`.

**Bringing your local reading data along.** Do this instead of the last
command above, before anyone signs up. Uploading replaces the server's
database:

```sh
fly ssh sftp shell        # then: put data/app.db /data/app.db
fly machine restart
make prod-user ARGS="passwd owner"   # local data belongs to the admin "owner"
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
- Signing in with a provider creates an account only from "Create an
  account", and only with the invite code. Otherwise an unknown Google or
  GitHub account is turned away.
- Existing password accounts link Google or GitHub from their profile page,
  `/profile`, and can then sign in either way.
- Accounts are matched on the provider's account id, never on email.

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

- **Self sign-up:** share the URL and the invite code. Sign-up is closed when
  `INVITE_CODE` is unset. To rotate the code, run
  `fly secrets set INVITE_CODE=<new>`; existing accounts keep working.
- **Making accounts yourself:** run `make prod-user ARGS="add alice"` and send
  them the password.
- **Other account commands:** `ARGS="list"`, `ARGS="passwd alice"` (also signs
  them out everywhere), `ARGS="admin alice"` / `ARGS="admin alice --revoke"`.

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
