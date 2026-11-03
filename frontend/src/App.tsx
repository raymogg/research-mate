import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { api, ApiError, SIGNED_OUT, type ClientConfig, type User } from "./api";
import { Admin } from "./pages/Admin";
import { Library } from "./pages/Library";
import { Login } from "./pages/Login";
import { Reader } from "./pages/Reader";
import { EventBuffer } from "./tracking/eventBuffer";

interface AppContext {
  config: ClientConfig;
  buffer: EventBuffer;
  user: User;
  signOut: () => Promise<void>;
}

const Ctx = createContext<AppContext | null>(null);

export function useApp(): AppContext {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useApp outside provider");
  return ctx;
}

export function App() {
  const [base, setBase] = useState<{ config: ClientConfig; buffer: EventBuffer } | null>(null);
  // undefined: still checking; null: signed out
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .config()
      .then((config) => setBase({ config, buffer: new EventBuffer(config.flush_interval_s) }))
      .catch((e) => setError(`Can't reach the backend: ${e.message}`));
    api.auth
      .me()
      .then(setUser)
      .catch((e) => (e instanceof ApiError && e.status === 401 ? setUser(null) : setError(e.message)));
    const onSignedOut = () => setUser(null);
    window.addEventListener(SIGNED_OUT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT, onSignedOut);
  }, []);

  const signIn = useCallback(
    (u: User) => {
      setUser(u);
      void base?.buffer.flush(); // anything queued while signed out
    },
    [base],
  );

  const signOut = useCallback(async () => {
    await base?.buffer.flush();
    await api.auth.logout();
    setUser(null);
  }, [base]);

  if (error) return <div className="page-message">{error}</div>;
  if (!base || user === undefined) return <div className="page-message">Loading…</div>;
  if (user === null) return <Login signupOpen={base.config.signup_open} onSignedIn={signIn} />;
  return (
    <Ctx.Provider value={{ ...base, user, signOut }}>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/paper/:key" element={<Reader />} />
          <Route
            path="/admin"
            element={user.is_admin ? <Admin /> : <div className="page-message">Admins only. <Link to="/">Back to library</Link></div>}
          />
          <Route
            path="*"
            element={
              <div className="page-message">
                Not found. <Link to="/">Back to library</Link>
              </div>
            }
          />
        </Routes>
      </BrowserRouter>
    </Ctx.Provider>
  );
}
