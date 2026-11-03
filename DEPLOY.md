# Deploying

One Fly.io machine runs the existing Docker image. A 1 GB volume holds `/data`
(`app.db`, `pdfs/`, `feedback.log`). SQLite stays: one machine and a handful of
users is well within what it handles. Expect roughly $2–5/month: the machine
sleeps when idle, and the volume costs about $0.15/GB.

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

Fly also snapshots the volume daily and keeps snapshots for 5 days
(`fly volumes snapshots list <vol-id>`).

## Notes

- **Cookies:** login cookies are HttpOnly, SameSite=Lax and HTTPS-only (`COOKIE_SECURE=1`). They last 30 days (`LOGIN_DAYS`).
- **Feedback stub:** `/feedback/events` only accepts the app's own loopback calls, so outsiders can't write to `feedback.log`.
- **Brute-force protection:** there is no rate limit on sign-in yet. scrypt makes each guess slowish. Add a limit before sharing widely.
- **Scaling:** don't scale past one machine. Each machine would get its own volume and its own SQLite file.
