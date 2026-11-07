"""Sign-in with Google and GitHub: the OAuth 2.0 authorization code flow with PKCE.

The browser asks us to start (routes/oauth.py). We save a random state in
`oauth_attempts` and in a short-lived cookie, then send the browser to the
provider. The provider sends it back to our callback with a code, which
`fetch_profile` swaps for an access token and reads the account's id and email
with it. The account id (`subject`) is what we match on; emails can change.
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

TIMEOUT_S = 10


@dataclass(frozen=True)
class Provider:
    name: str
    authorize_url: str
    token_url: str
    scope: str
    extra: tuple[tuple[str, str], ...] = ()  # more query parameters for the authorize URL


PROVIDERS = {
    "google": Provider(
        "google",
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
        "openid email profile",
        (("prompt", "select_account"),),
    ),
    "github": Provider(
        "github",
        "https://github.com/login/oauth/authorize",
        "https://github.com/login/oauth/access_token",
        "read:user user:email",
    ),
}


@dataclass
class Profile:
    subject: str
    email: str | None
    username_hint: str  # starting point for a new account's username


class OAuthError(Exception):
    pass


def new_verifier() -> str:
    return secrets.token_urlsafe(48)


def challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorize_url(provider: Provider, client_id: str, redirect_uri: str, state: str, verifier: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": provider.scope,
        "state": state,
        "code_challenge": challenge(verifier),
        "code_challenge_method": "S256",
        **dict(provider.extra),
    }
    return f"{provider.authorize_url}?{urlencode(params)}"


def fetch_profile(provider: Provider, client: tuple[str, str], code: str, verifier: str, redirect_uri: str) -> Profile:
    """Swap the callback's code for a token and read who signed in. Raises OAuthError."""
    client_id, secret = client
    try:
        with httpx.Client(timeout=TIMEOUT_S, headers={"Accept": "application/json"}) as http:
            resp = http.post(provider.token_url, data={
                "client_id": client_id,
                "client_secret": secret,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            })
            token = resp.json().get("access_token") if resp.is_success else None
            if not token:
                raise OAuthError(f"{provider.name} didn't give a token ({resp.status_code})")
            auth = {"Authorization": f"Bearer {token}"}
            if provider.name == "google":
                return _google(http, auth)
            return _github(http, auth)
    except httpx.HTTPError as e:
        raise OAuthError(f"couldn't reach {provider.name}: {e}") from e


def _google(http: httpx.Client, auth: dict) -> Profile:
    info = http.get("https://openidconnect.googleapis.com/v1/userinfo", headers=auth).raise_for_status().json()
    email = info.get("email") if info.get("email_verified") else None
    return Profile(subject=str(info["sub"]), email=email, username_hint=(email or "").split("@")[0])


def _github(http: httpx.Client, auth: dict) -> Profile:
    user = http.get("https://api.github.com/user", headers=auth).raise_for_status().json()
    # The profile email is often hidden; the primary verified one is in /user/emails
    emails = http.get("https://api.github.com/user/emails", headers=auth)
    email = next(
        (e["email"] for e in (emails.json() if emails.is_success else []) if e.get("primary") and e.get("verified")),
        None,
    )
    return Profile(subject=str(user["id"]), email=email, username_hint=user.get("login") or "")
