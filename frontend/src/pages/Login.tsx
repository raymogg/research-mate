import { type FormEvent, useState } from "react";
import { api, PROVIDER_NAMES, type Provider, type User } from "../api";

/** What `?auth_error=` from the OAuth callback means. */
export const AUTH_ERRORS: Record<string, string> = {
  no_account: "No account uses that sign-in yet. Have an invite code? Create an account with it.",
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

export function Login({
  signupOpen,
  providers,
  onSignedIn,
}: {
  signupOpen: boolean;
  providers: Provider[];
  onSignedIn: (u: User) => void;
}) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(takeAuthError);
  const signup = mode === "signup";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(signup ? await api.auth.signup(username, password, code) : await api.auth.login(username, password));
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  async function withProvider(provider: Provider) {
    setBusy(true);
    setError(null);
    try {
      const { url } = await api.auth.oauthStart(provider, mode, code);
      window.location.assign(url);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <h1>Reading library</h1>
        <p className="muted small">{signup ? "Create an account with your invite code." : "Sign in to continue."}</p>
        {signup && (
          <label>
            Invite code
            <input autoFocus autoComplete="off" value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
        )}
        {providers.length > 0 && (
          <>
            <div className="provider-buttons">
              {providers.map((p) => (
                <button
                  key={p}
                  type="button"
                  className="provider-button"
                  disabled={busy || (signup && !code)}
                  onClick={() => void withProvider(p)}
                >
                  Continue with {PROVIDER_NAMES[p]}
                </button>
              ))}
            </div>
            <p className="divider small muted">
              <span>or {signup ? "choose a username and password" : "use your username"}</span>
            </p>
          </>
        )}
        <label>
          Username
          <input
            autoFocus={!signup && providers.length === 0}
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete={signup ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button className="primary" type="submit" disabled={busy || !username || !password || (signup && !code)}>
          {busy ? "…" : signup ? "Create account" : "Sign in"}
        </button>
        {signupOpen && (
          <button
            type="button"
            className="link"
            onClick={() => {
              setMode(signup ? "login" : "signup");
              setError(null);
            }}
          >
            {signup ? "Have an account? Sign in" : "Have an invite code? Create an account"}
          </button>
        )}
        <p className="muted small">
          {signup ? "By creating an account you agree to how we handle your data, set out in our " : "How we handle your data: "}
          <a href="/privacy">privacy policy</a>.
        </p>
      </form>
    </div>
  );
}
