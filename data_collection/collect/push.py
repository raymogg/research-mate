"""Push the catalog to the Fly app, replacing /data/catalog.db.

1. Snapshot: `VACUUM INTO` writes a compact, consistent copy. Safe while fetch
   and embed are running.
2. Compress with gzip. The text shrinks well; the vectors barely do.
3. Check there's room on the volume. The upload, the unpacked copy and the old
   catalog are all on disk for a moment.
4. Upload with `fly ssh sftp`, then run a small install script on the server.
   It unpacks the upload, runs SQLite's integrity check, and only then swaps
   the file into place, so a failed or partial push leaves the old catalog
   untouched.

Needs `fly` on PATH, signed in (`fly auth login`) or with FLY_API_TOKEN set (a
deploy token: `fly tokens create deploy -a <app>`).
"""

import gzip
import json
import logging
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

REMOTE_DIR = "/data"
UPLOAD = "catalog-upload.db.gz"
INSTALLER = "catalog-install.py"
HEADROOM = 1.15  # spare space to keep on the volume, as a fraction of what the push needs

# Runs on the server with the image's Python (stdlib only).
INSTALL_SCRIPT = '''
import gzip, os, shutil, sqlite3, sys

d = sys.argv[1]
gz, new, dst = f"{d}/catalog-upload.db.gz", f"{d}/catalog-new.db", f"{d}/catalog.db"
with gzip.open(gz) as src, open(new, "wb") as out:
    shutil.copyfileobj(src, out, 1 << 20)
os.remove(gz)
con = sqlite3.connect(new)
check = con.execute("PRAGMA quick_check").fetchone()[0]
papers = con.execute("SELECT count(*) FROM papers").fetchone()[0]
vectors = con.execute("SELECT count(*) FROM embeddings").fetchone()[0]
con.close()
if check != "ok":
    os.remove(new)
    sys.exit(f"upload failed the integrity check ({check}); the old catalog is unchanged")
for leftover in (dst + "-wal", dst + "-shm"):
    if os.path.exists(leftover):
        os.remove(leftover)
os.replace(new, dst)
print(f"installed {dst}: {papers} papers, {vectors} embeddings, {os.path.getsize(dst) / 2**30:.2f} GB")
'''


def snapshot(db: Path, out: Path) -> None:
    out.unlink(missing_ok=True)
    con = sqlite3.connect(db, timeout=60)
    con.execute("VACUUM INTO ?", (str(out),))
    con.close()


def compress(src: Path, out: Path) -> None:
    with src.open("rb") as f, gzip.open(out, "wb", compresslevel=3) as g:
        shutil.copyfileobj(f, g, 1 << 20)


class Fly:
    def __init__(self, app: str, run=subprocess.run):
        self.app = app
        self._run = run

    def __call__(self, *args: str, capture: bool = False, check: bool = True) -> str:
        cmd = ["fly", *args, "-a", self.app]
        log.info("$ %s", " ".join(cmd))
        result = self._run(cmd, check=check, text=True, capture_output=capture)
        return result.stdout if capture else ""

    def ssh(self, command: str, capture: bool = False) -> str:
        return self("ssh", "console", "-C", command, capture=capture)

    def wake(self) -> None:
        """Start the machine if it's asleep, so ssh can reach it."""
        machines = json.loads(self("machine", "list", "--json", capture=True) or "[]")
        for m in machines:
            if m.get("state") != "started":
                self("machine", "start", m["id"], check=False)

    def free_bytes(self) -> int:
        out = self.ssh(f"df -Pk {REMOTE_DIR}", capture=True)
        return int(out.strip().splitlines()[-1].split()[3]) * 1024


def push(db: Path, app: str, run=subprocess.run, workdir: Path | None = None) -> None:
    fly = Fly(app, run)
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        tmp = Path(tmp)
        snap, gz, installer = tmp / "catalog.db", tmp / UPLOAD, tmp / INSTALLER
        log.info("snapshotting %s", db)
        snapshot(db, snap)
        log.info("compressing %.2f GB", snap.stat().st_size / 2**30)
        compress(snap, gz)
        installer.write_text(INSTALL_SCRIPT)
        size, packed = snap.stat().st_size, gz.stat().st_size
        log.info("upload is %.2f GB (%.2f GB unpacked)", packed / 2**30, size / 2**30)

        fly.wake()
        need = int((size + packed) * HEADROOM)
        free = fly.free_bytes()
        if free < need:
            raise SystemExit(
                f"not enough space on the volume: {free / 2**30:.2f} GB free, {need / 2**30:.2f} GB needed. "
                f"Extend it: fly volumes extend <vol-id> -s <GB> -a {app}"
            )
        fly.ssh(f"rm -f {REMOTE_DIR}/{UPLOAD} {REMOTE_DIR}/{INSTALLER} {REMOTE_DIR}/catalog-new.db")
        fly("ssh", "sftp", "put", str(gz), f"{REMOTE_DIR}/{UPLOAD}")
        fly("ssh", "sftp", "put", str(installer), f"{REMOTE_DIR}/{INSTALLER}")
        print(fly.ssh(f"python {REMOTE_DIR}/{INSTALLER} {REMOTE_DIR}", capture=True).strip())
        fly.ssh(f"rm -f {REMOTE_DIR}/{INSTALLER}")
