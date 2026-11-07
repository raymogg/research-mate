import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { BrowserRouter, Link, Route, Routes, useLocation } from "react-router-dom";
import { api, ApiError, SIGNED_OUT, type ClientConfig, type User } from "./api";
import { Admin } from "./pages/Admin";
import { Library } from "./pages/Library";
import { Login } from "./pages/Login";
import { Privacy } from "./pages/Privacy";
import { Profile } from "./pages/Profile";
import { Reader } from "./pages/Reader";
import { PaperQuestions } from "./components/PaperQuestions";
import { SiteFooter } from "./components/SiteFooter";
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

  if (window.location.pathname === "/privacy") return <Shell><Privacy /></Shell>; // readable signed out
  if (error) return <Shell><div className="page-message">{error}</div></Shell>;
  if (!base || user === undefined) return <Shell><div className="page-message">Loading…</div></Shell>;
  if (user === null)
    return (
      <Shell>
        <Login signupOpen={base.config.signup_open} providers={base.config.providers} onSignedIn={signIn} />
      </Shell>
    );
  return (
    <Ctx.Provider value={{ ...base, user, signOut }}>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <PaperQuestions />
        <RoutedShell>
          <Routes>
            <Route path="/" element={<Library />} />
            <Route path="/paper/:key" element={<Reader />} />
            <Route path="/profile" element={<Profile />} />
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
        </RoutedShell>
      </BrowserRouter>
    </Ctx.Provider>
  );
}

/** Page content above the site footer, which sits at the bottom of short pages. */
function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="shell">
      <div className="shell-main">{children}</div>
      <SiteFooter />
    </div>
  );
}

/** The reader fills the window, so it gets no footer; its toolbar has the theme toggle. */
function RoutedShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  return pathname.startsWith("/paper/") ? <>{children}</> : <Shell>{children}</Shell>;
}
