from fastapi.testclient import TestClient

from app.main import create_app


def client(settings, feedback, host: str) -> TestClient:
    settings.public_url = "https://researchmate.org"
    return TestClient(create_app(settings, feedback_sender=feedback), base_url=f"https://{host}", follow_redirects=False)


def test_aliases_redirect_to_the_public_host(settings, feedback):
    for host in ("www.researchmate.org", "taste-reader.fly.dev"):
        resp = client(settings, feedback, host).get("/api/auth/me?x=1")
        assert resp.status_code == 308
        assert resp.headers["location"] == "https://researchmate.org/api/auth/me?x=1"


def test_public_host_and_machine_address_are_served(settings, feedback):
    for host in ("researchmate.org", "172.19.0.2:8000"):
        assert client(settings, feedback, host).get("/feedback/health").status_code == 200
