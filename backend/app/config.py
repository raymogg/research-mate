import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# Settings that change what an event means. Stamped on each event.
EVENT_CONFIG_KEYS = ("idle_cutoff_s", "return_visit_gap_min", "page_viewed_min_s", "auto_read_threshold")


@dataclass
class Settings:
    idle_cutoff_s: int = field(default_factory=lambda: int(_env("IDLE_CUTOFF_S", "60")))
    return_visit_gap_min: int = field(default_factory=lambda: int(_env("RETURN_VISIT_GAP_MIN", "30")))
    page_viewed_min_s: int = field(default_factory=lambda: int(_env("PAGE_VIEWED_MIN_S", "5")))
    auto_read_threshold: float = field(default_factory=lambda: float(_env("AUTO_READ_THRESHOLD", "0.8")))
    flush_interval_s: int = field(default_factory=lambda: int(_env("FLUSH_INTERVAL_S", "15")))
    feedback_url: str = field(
        default_factory=lambda: _env("FEEDBACK_URL", "http://127.0.0.1:8000/feedback/events")
    )
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")))
    static_dir: Path = field(default_factory=lambda: Path(_env("STATIC_DIR", "./static")))
    # Send the login cookie over HTTPS only. Set in production.
    cookie_secure: bool = field(default_factory=lambda: _env("COOKIE_SECURE", "0") == "1")
    login_days: int = field(default_factory=lambda: int(_env("LOGIN_DAYS", "30")))
    # Where browsers reach the app, e.g. https://researchmate.org. OAuth
    # redirect URIs are built from it; empty uses the request's own URL (dev).
    public_url: str = field(default_factory=lambda: _env("PUBLIC_URL", ""))
    # OAuth apps for "Continue with Google / GitHub", the only ways to sign in.
    # A provider shows up once both are set.
    google_client_id: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_SECRET", ""))
    github_client_id: str = field(default_factory=lambda: _env("GITHUB_CLIENT_ID", ""))
    github_client_secret: str = field(default_factory=lambda: _env("GITHUB_CLIENT_SECRET", ""))
    # Days Fly keeps volume snapshots (fly.toml `snapshot_retention`): how long
    # deleted data can linger in backups. Shown on the profile page.
    backup_retention_days: int = field(default_factory=lambda: int(_env("BACKUP_RETENTION_DAYS", "14")))

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'app.db'}"

    @property
    def catalog_path(self) -> Path:
        """The paper catalog behind suggestions. Built and pushed by data_collection/; read-only here."""
        return self.data_dir / "catalog.db"

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def html_dir(self) -> Path:
        """Reader documents built from arXiv's HTML (see arxiv.fetch_html)."""
        return self.data_dir / "html"

    @property
    def feedback_log(self) -> Path:
        return self.data_dir / "feedback.log"

    def oauth_client(self, provider: str) -> tuple[str, str] | None:
        """(client id, secret) if that provider is set up."""
        pair = (getattr(self, f"{provider}_client_id", ""), getattr(self, f"{provider}_client_secret", ""))
        return pair if all(pair) else None

    def event_config(self) -> dict:
        """The thresholds stamped on every event (see `Event.config`)."""
        return {k: getattr(self, k) for k in EVENT_CONFIG_KEYS}

    def client_config(self) -> dict:
        """Thresholds the browser needs; served so there is one source of truth."""
        return {
            "idle_cutoff_s": self.idle_cutoff_s,
            "return_visit_gap_min": self.return_visit_gap_min,
            "page_viewed_min_s": self.page_viewed_min_s,
            "auto_read_threshold": self.auto_read_threshold,
            "flush_interval_s": self.flush_interval_s,
            "providers": [p for p in ("google", "github") if self.oauth_client(p)],
        }
