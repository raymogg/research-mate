"""Pushing the catalog to Fly (with fly commands faked, and the installer run locally)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from collect import push

from .conftest import add


class FakeFly:
    """Plays the server: sftp puts land in `remote`, ssh commands run there."""

    def __init__(self, remote: Path, free_gb: float = 100):
        self.remote, self.free_gb, self.commands = remote, free_gb, []

    def __call__(self, cmd, check=True, text=True, capture_output=False):
        assert cmd[0] == "fly" and cmd[-2:] == ["-a", "taste-reader"]
        args = cmd[1:-2]
        self.commands.append(args)
        out = ""
        if args[:2] == ["machine", "list"]:
            out = json.dumps([{"id": "m1", "state": "stopped"}])
        elif args[:3] == ["ssh", "sftp", "put"]:
            (self.remote / Path(args[4]).name).write_bytes(Path(args[3]).read_bytes())
        elif args[:2] == ["ssh", "console"]:
            c = args[3]
            if c.startswith("df"):
                out = f"Filesystem 1024-blocks Used Available\n/dev/vdc 1 1 {int(self.free_gb * 2**20)} /data\n"
            elif c.startswith("python"):
                r = subprocess.run([sys.executable, str(self.remote / push.INSTALLER), str(self.remote)],
                                   capture_output=True, text=True, check=False)
                if r.returncode:
                    raise subprocess.CalledProcessError(r.returncode, cmd, r.stdout, r.stderr)
                out = r.stdout
            elif c.startswith("rm"):
                for path in c.split()[2:]:
                    (self.remote / Path(path).name).unlink(missing_ok=True)
        return subprocess.CompletedProcess(cmd, 0, out, "")


def test_push_installs_a_checked_copy(con, tmp_path, capsys):
    add(con, ("2611.00001", "2026-11-01"), ("2611.00002", "2026-11-02"))
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "catalog.db").write_bytes(b"old")
    (remote / "catalog.db-wal").write_bytes(b"old wal")
    fly = FakeFly(remote)
    push.push(tmp_path / "catalog.db", "taste-reader", run=fly)

    assert ["machine", "start", "m1"] in fly.commands
    assert "installed" in capsys.readouterr().out
    assert sorted(p.name for p in remote.iterdir()) == ["catalog.db"]  # upload, installer and old WAL gone
    import sqlite3

    assert sqlite3.connect(remote / "catalog.db").execute("SELECT count(*) FROM papers").fetchone()[0] == 2


def test_push_refuses_without_room(con, tmp_path):
    add(con, ("2611.00001", "2026-11-01"))
    remote = tmp_path / "remote"
    remote.mkdir()
    fly = FakeFly(remote, free_gb=0)
    with pytest.raises(SystemExit, match="not enough space"):
        push.push(tmp_path / "catalog.db", "taste-reader", run=fly)
    assert not any(c[:3] == ["ssh", "sftp", "put"] for c in fly.commands)


def test_a_corrupt_upload_leaves_the_old_catalog(tmp_path):
    import gzip

    (tmp_path / "catalog.db").write_bytes(b"old")
    with gzip.open(tmp_path / push.UPLOAD, "wb") as g:
        g.write(b"not a database")
    (tmp_path / push.INSTALLER).write_text(push.INSTALL_SCRIPT)
    r = subprocess.run([sys.executable, str(tmp_path / push.INSTALLER), str(tmp_path)], capture_output=True, text=True, check=False)
    assert r.returncode != 0
    assert (tmp_path / "catalog.db").read_bytes() == b"old"
