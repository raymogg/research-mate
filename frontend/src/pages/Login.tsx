import { type FormEvent, useState } from "react";
import { api, type User } from "../api";

export function Login({ signupOpen, onSignedIn }: { signupOpen: boolean; onSignedIn: (u: User) => void }) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <h1>Reading library</h1>
        <p className="muted small">{signup ? "Create an account with your invite code." : "Sign in to continue."}</p>
        <label>
          Username
          <input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
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
        {signup && (
          <label>
            Invite code
            <input autoComplete="off" value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
        )}
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
      </form>
    </div>
  );
}
