"""Inspect and export captured data from the terminal.

    python -m app.cli stats [-u NAME]      # what's been captured so far
    python -m app.cli events -n 20 -t page_dwell [-u NAME]
    python -m app.cli export [--out DIR]   # CSV/JSONL for notebooks
    python -m app.cli user add NAME [--admin]   # prompts for a password
    python -m app.cli user list | passwd NAME | rename NAME NEW | admin NAME [--revoke]

Reads DATA_DIR like the server, so it works while the server is running.
"""

import argparse
import csv
import getpass
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, select

from . import admin, auth
from .config import Settings
from .db import make_engine
from .models import Event, Login, Paper, PaperTag, User, iso


def fmt_ms(ms: int) -> str:
    s = round(ms / 1000)
    return f"{s // 3600}h {s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m {s % 60:02d}s"


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def find_user(db: Session, name: str) -> User:
    user = db.exec(select(User).where(User.username == name.strip().lower())).first()
    if user is None:
        raise SystemExit(f"No user {name!r}. See `python -m app.cli user list`.")
    return user


def stats(db: Session, settings: Settings, user_id: int | None = None) -> None:
    o = admin.overview(db, settings, user_id)
    t, st = o["totals"], o["storage"]
    statuses = ", ".join(f"{n} {s}" for s, n in t["papers_by_status"].items() if n)
    print(f"Data dir   {st['data_dir']}")
    print(f"Users      {t['users']}" + (" (showing one)" if user_id is not None else ""))
    print(f"Papers     {t['papers']} ({statuses or 'none'}); {t['rated']} rated; {t['tags']} distinct tags")
    print(f"Sessions   {t['sessions']} ({t['return_visits']} return visits); active reading {fmt_ms(t['total_active_ms'])}")
    print(f"Events     {t['events']} stored, first {t['first_event_ts'] or '-'}, last {t['last_event_ts'] or '-'}")
    pending = t["unreported_events"]
    print(f"Delivery   {'all delivered' if not pending else f'{pending} pending since {t['oldest_unreported_ts']}'}"
          f"; feedback.log has {st['feedback_log_lines']} lines")
    print(f"Storage    db {fmt_bytes(st['db_bytes'])}, {st['pdf_count']} PDFs {fmt_bytes(st['pdf_bytes'])}, "
          f"log {fmt_bytes(st['feedback_log_bytes'])}")
    print("\nEvent types")
    for e in o["event_types"]:
        print(f"  {e['type']:<19}{e['source']:<8}{e['count']:>6}   {e['description']}")
    print("\nReading by paper")
    for p in admin.reading_by_paper(db, settings, user_id):
        pages = f"{p['pages_viewed']}/{p['num_pages'] or '?'}"
        rating = f"{p['rating']}★" if p["rating"] else "-"
        print(f"  {p['username'] or '?':<12}{fmt_ms(p['total_ms']):>8}  {p['visits']:>2} visits  {pages:>7} pages  {rating:>2}  "
              f"{p['status']:<9} {p['title'][:60]}")


def events(db: Session, n: int, type: str | None, user_id: int | None = None) -> None:
    for e in reversed(admin.recent_events(db, user_id, type=type, limit=n)):
        sent = "sent" if e["reported_at"] else "PENDING"
        print(f"{e['ts']}  {e['username'] or '?':<12}{e['type']:<19}{e['arxiv_id']:<16}{sent:<8}{json.dumps(e['payload'])}")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def export(db: Session, settings: Settings, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tags: dict[tuple[int, str], list[str]] = {}
    for pt in db.exec(select(PaperTag)):
        tags.setdefault((pt.user_id, pt.arxiv_id), []).append(pt.tag)
    by_paper = {(p["user_id"], p["arxiv_id"]): p for p in admin.reading_by_paper(db, settings)}
    names = dict(db.exec(select(User.id, User.username)).all())

    write_csv(out / "users.csv", [
        {"user_id": u["id"], "username": u["username"], "is_admin": u["is_admin"], "created_at": u["created_at"]}
        for u in admin.users(db)
    ])
    papers = []
    for p in db.exec(select(Paper)):
        r = by_paper[(p.user_id, p.arxiv_id)]
        papers.append({
            "user_id": p.user_id, "username": names.get(p.user_id), "arxiv_id": p.arxiv_id, "version": p.version, "title": p.title, "authors": "; ".join(p.authors),
            "categories": "; ".join(p.categories), "published_at": iso(p.published_at), "num_pages": p.num_pages,
            "status": p.status, "status_source": p.status_source, "rating": p.rating,
            "tags": "; ".join(sorted(tags.get((p.user_id, p.arxiv_id), []))), "added_at": iso(p.added_at),
            "last_opened_at": iso(p.last_opened_at), "total_active_ms": r["total_ms"], "visits": r["visits"],
            "pages_viewed": r["pages_viewed"],
        })
    write_csv(out / "papers.csv", papers)
    write_csv(out / "sessions.csv", admin.sessions(db, limit=1_000_000))
    write_csv(out / "page_dwell.csv", [
        {"user_id": uid, "arxiv_id": a, "page": int(page), "active_ms": ms}
        for (uid, a), p in by_paper.items() for page, ms in p["page_ms"].items()
    ])
    with (out / "events.jsonl").open("w") as f:
        for e in db.exec(select(Event).order_by(Event.ts)):
            f.write(json.dumps(admin.event_row(e, {}, names)) + "\n")
    print(f"Exported to {out.resolve()}")
    for name in ("users.csv", "papers.csv", "sessions.csv", "page_dwell.csv", "events.jsonl"):
        print(f"  {name}")


def ask_password() -> str:
    password = getpass.getpass("Password: ")
    if getpass.getpass("Again: ") != password:
        raise SystemExit("Passwords don't match")
    return password


def user_cmd(db: Session, args) -> None:
    if args.action == "list":
        for u in admin.users(db):
            role = "admin" if u["is_admin"] else ""
            print(f"{u['id']:>4}  {u['username']:<20}{role:<7}{u['papers']:>4} papers{u['events']:>7} events"
                  f"  joined {u['created_at'][:10]}")
        return
    if not args.name:
        raise SystemExit(f"user {args.action} needs a username")
    try:
        if args.action == "add":
            user = auth.create_user(db, args.name, ask_password(), is_admin=args.admin)
            print(f"Created {user.username} (id {user.id}{', admin' if user.is_admin else ''})")
        elif args.action == "passwd":
            user = find_user(db, args.name)
            password = ask_password()
            auth.check_new_password(password)
            user.password_hash = auth.hash_password(password)
            for login in db.exec(select(Login).where(Login.user_id == user.id)):
                db.delete(login)  # sign out everywhere
            db.add(user)
            db.commit()
            print(f"Password changed for {user.username}; their browsers are signed out")
        elif args.action == "rename":
            user = find_user(db, args.name)
            new = auth.normalise_username(args.new or "")
            if db.exec(select(User).where(User.username == new)).first():
                raise ValueError("That username is taken")
            old, user.username = user.username, new
            db.add(user)
            db.commit()
            print(f"Renamed {old} to {new}")
        elif args.action == "admin":
            user = find_user(db, args.name)
            user.is_admin = not args.revoke
            db.add(user)
            db.commit()
            print(f"{user.username} is {'now' if user.is_admin else 'no longer'} an admin")
    except ValueError as e:
        raise SystemExit(str(e))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("stats", help="summary of captured data")
    st.add_argument("-u", "--user", help="only this username")
    ev = sub.add_parser("events", help="most recent events")
    ev.add_argument("-n", type=int, default=30)
    ev.add_argument("-t", "--type", help="only this event type")
    ev.add_argument("-u", "--user", help="only this username")
    ex = sub.add_parser("export", help="write CSV/JSONL files")
    ex.add_argument("--out", type=Path, help="default: DATA_DIR/exports/<timestamp>")
    us = sub.add_parser("user", help="manage accounts")
    us.add_argument("action", choices=["add", "list", "passwd", "rename", "admin"])
    us.add_argument("name", nargs="?")
    us.add_argument("new", nargs="?", help="rename: the new username")
    us.add_argument("--admin", action="store_true", help="add: make an admin")
    us.add_argument("--revoke", action="store_true", help="admin: remove admin rights")
    args = parser.parse_args()

    settings = Settings()
    if args.cmd != "user" and not (settings.data_dir / "app.db").exists():
        raise SystemExit(f"No database at {settings.data_dir / 'app.db'}. Set DATA_DIR (e.g. DATA_DIR=../data).")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with Session(make_engine(settings.db_url)) as db:
        uid = find_user(db, args.user).id if getattr(args, "user", None) else None
        if args.cmd == "stats":
            stats(db, settings, uid)
        elif args.cmd == "events":
            events(db, args.n, args.type, uid)
        elif args.cmd == "user":
            user_cmd(db, args)
        else:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            export(db, settings, args.out or settings.data_dir / "exports" / stamp)


if __name__ == "__main__":
    main()
