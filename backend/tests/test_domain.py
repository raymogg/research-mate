from fastapi.testclient import TestClient

from app.main import create_app


def client(settings, feedback, host: str) -> TestClient:
    settings.public_url = "https://example.org"
    return TestClient(create_app(settings, feedback_sender=feedback), base_url=f"https://{host}", follow_redirects=False)


def test_aliases_redirect_to_the_public_host(settings, feedback):
    for host in ("www.example.org", "my-app.fly.dev"):
        resp = client(settings, feedback, host).get("/api/auth/me?x=1")
        assert resp.status_code == 308
        assert resp.headers["location"] == "https://example.org/api/auth/me?x=1"


def test_public_host_and_machine_address_are_served(settings, feedback):
    for host in ("example.org", "10.0.0.2:8000"):
        assert client(settings, feedback, host).get("/feedback/health").status_code == 200


def test_health_is_public_and_checks_the_database(anon, app, monkeypatch):
    body = anon.get("/api/health").json()
    assert body["ok"] is True and body["db_ok"] is True and isinstance(body["disk_ok"], bool)

    import shutil
    from collections import namedtuple

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _: Usage(100, 95, 5))
    resp = anon.get("/api/health")
    assert resp.status_code == 200 and resp.json()["disk_ok"] is False  # full disk warns, doesn't fail

    app.state.engine.dispose()
    (app.state.settings.data_dir / "app.db").unlink()
    (app.state.settings.data_dir / "app.db").mkdir()  # unreadable as a database
    assert anon.get("/api/health").status_code == 503
