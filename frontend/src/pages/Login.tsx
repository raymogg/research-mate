import { type ReactNode, useState } from "react";
import { api, PROVIDER_NAMES, type Provider } from "../api";

/** What `?auth_error=` from the OAuth callback means. */
export const AUTH_ERRORS: Record<string, string> = {
  expired: "That sign-in took too long or was started elsewhere. Try again.",
  cancelled: "Sign-in was cancelled.",
  provider: "We couldn't confirm the sign-in with the provider. Try again.",
  taken: "That account is already linked to a different user here.",
};

/** Reads `?auth_error=` once and removes it from the address bar. */
export function takeAuthError(): string | null {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("auth_error");
  if (!code) return null;
  params.delete("auth_error");
  const rest = params.toString();
  window.history.replaceState(null, "", window.location.pathname + (rest ? `?${rest}` : ""));
  return AUTH_ERRORS[code] ?? "Sign-in didn't work. Try again.";
}

// The starter paper a visitor was reading when they chose to sign in. Added to
// their library, and reopened, once they're back from the provider.
const PENDING_KEY = "researchmate:after-sign-in";

export function takePendingPaper(): string | null {
  try {
    const key = sessionStorage.getItem(PENDING_KEY);
    sessionStorage.removeItem(PENDING_KEY);
    return key;
  } catch {
    return null;
  }
}

/** "Continue with Google / GitHub". A first sign-in creates the account. */
export function SignInButtons({ providers, paperKey }: { providers: Provider[]; paperKey?: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(takeAuthError);

  async function go(provider: Provider) {
    setBusy(true);
    setError(null);
    try {
      const { url } = await api.auth.oauthStart(provider, "login");
      try {
        if (paperKey) sessionStorage.setItem(PENDING_KEY, paperKey);
      } catch {
        // storage blocked: they'll land on their library instead
      }
      window.location.assign(url);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="sign-in">
      {providers.length === 0 ? (
        <p className="muted small">Sign-in isn't set up on this server yet.</p>
      ) : (
        <div className="provider-buttons">
          {providers.map((p) => (
            <button key={p} type="button" className="provider-button" disabled={busy} onClick={() => void go(p)}>
              Continue with {PROVIDER_NAMES[p]}
            </button>
          ))}
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <p className="muted small">
        New here? Your first sign-in creates your account. By continuing you agree to how we handle your data, set
        out in our <a href="/privacy">privacy policy</a>.
      </p>
    </div>
  );
}

/** A page that needs an account, seen signed out. */
export function Login({ providers, children }: { providers: Provider[]; children?: ReactNode }) {
  return (
    <div className="login">
      <div className="login-card">
        <div className="brand">ResearchMate</div>
        <h1>Sign in</h1>
        <p className="muted small">{children ?? "Sign in to see your library."}</p>
        <SignInButtons providers={providers} />
        <a className="small" href="/">
          ← Browse the starter papers
        </a>
      </div>
    </div>
  );
}
