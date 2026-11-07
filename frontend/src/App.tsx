import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { BrowserRouter, Link, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, SIGNED_OUT, type ClientConfig, type StarterPaper, type User } from "./api";
import { Admin } from "./pages/Admin";
import { Library } from "./pages/Library";
import { Login, takePendingPaper } from "./pages/Login";
import { Privacy } from "./pages/Privacy";
import { Profile } from "./pages/Profile";
import { Reader } from "./pages/Reader";
import { VisitorHome } from "./pages/Visitor";
import { PaperQuestions } from "./components/PaperQuestions";
import { SiteFooter } from "./components/SiteFooter";
import { EventBuffer } from "./tracking/eventBuffer";

export interface Starter {
  policy: string;
  papers: StarterPaper[];
}

interface AppContext {
  config: ClientConfig;
  buffer: EventBuffer;
  user: User | null; // null: a visitor, reading starter papers only
  starter: Starter | null; // null until loaded, or if it couldn't be
  signOut: () => Promise<void>;
}

const Ctx = createContext<AppContext | null>(null);

export function useApp(): AppContext {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useApp outside provider");
  return ctx;
}

/** The signed-in user, for pages only routed to when there is one. */
export function useUser(): User {
  const { user } = useApp();
  if (!user) throw new Error("useUser while signed out");
  return user;
}

export function App() {
  const [base, setBase] = useState<{ config: ClientConfig; buffer: EventBuffer } | null>(null);
  // undefined: still checking; null: signed out
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [starter, setStarter] = useState<Starter | null>(null);

  useEffect(() => {
    api.starter.list().then(setStarter, () => {});
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

  const signOut = useCallback(async () => {
    await base?.buffer.flush();
    await api.auth.logout();
    setUser(null);
  }, [base]);

  if (window.location.pathname === "/privacy") return <Shell><Privacy /></Shell>; // readable signed out
  if (error) return <Shell><div className="page-message">{error}</div></Shell>;
  if (!base || user === undefined) return <Shell><div className="page-message">Loading…</div></Shell>;
  const signIn = <Login providers={base.config.providers}>Sign in to see this page.</Login>;
  return (
    <Ctx.Provider value={{ ...base, user, starter, signOut }}>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        {user === null ? (
          <RoutedShell>
            <Routes>
              <Route path="/" element={<VisitorHome />} />
              <Route path="/paper/:key" element={<Reader />} />
              <Route path="*" element={signIn} />
            </Routes>
          </RoutedShell>
        ) : (
          <SignedIn user={user} />
        )}
      </BrowserRouter>
    </Ctx.Provider>
  );
}

function SignedIn({ user }: { user: User }) {
  const navigate = useNavigate();
  const [pending] = useState(takePendingPaper);
  const [error, setError] = useState<string | null>(null);

  // Back from signing in while reading a starter paper: keep it, and carry on reading.
  useEffect(() => {
    if (!pending) return;
    api
      .addPaper(pending, "suggested")
      .then((p) => navigate(`/paper/${p.key}`, { replace: true, state: { via: { kind: "direct" } } }))
      .catch((e) => setError(`Couldn't add that paper to your library: ${e.message}`));
  }, [pending, navigate]);

  return (
    <>
      <PaperQuestions />
      {error && (
        <p className="error page-message" role="alert">
          {error}
        </p>
      )}
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
    </>
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
