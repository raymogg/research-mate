import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type MyEvent, PROVIDER_NAMES, type Profile as ProfileData, type Provider } from "../api";
import { useApp, useUser } from "../App";
import { ago, duration, when } from "../format";
import { takeAuthError } from "./Login";

const PAGE = 50;

/** Plain-language names for the events we record. */
const EVENT_LABELS: Record<string, string> = {
  paper_added: "Added to library",
  paper_opened: "Opened",
  impression: "Shown in your library",
  zoom_changed: "Changed zoom",
  page_dwell: "Read a page",
  text_copied: "Copied text",
  paper_closed: "Closed",
  status_changed: "Status changed",
  completion_reached: "Finished reading",
  rating_set: "Rated",
  paper_feedback: "Answered questions",
  feedback_skipped: "Skipped questions",
  tag_added: "Tagged",
  tag_removed: "Removed a tag",
};

function detail(e: MyEvent): string {
  const p = e.payload;
  switch (e.type) {
    case "page_dwell":
      return `page ${p.page}, ${duration(Number(p.active_ms) || 0)}`;
    case "status_changed":
      return `${p.from ?? "—"} → ${p.to}`;
    case "rating_set":
      return p.to ? `${p.to} stars` : "cleared";
    case "tag_added":
    case "tag_removed":
      return String(p.tag ?? "");
    case "zoom_changed":
      return `${Math.round(Number(p.to) * 100)}% of page width`;
    case "text_copied":
      return `${p.chars} characters (the text itself isn't kept)`;
    case "paper_closed":
      return `${duration(Number(p.active_ms) || 0)} active`;
    default:
      return "";
  }
}

export function Profile() {
  const { config, signOut } = useApp();
  const user = useUser();
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [events, setEvents] = useState<MyEvent[]>([]);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(takeAuthError);

  const refresh = useCallback(() => {
    api.me.profile().then(setProfile, (e) => setError(e.message));
  }, []);

  const loadEvents = useCallback((offset: number) => {
    api.me.events(offset, PAGE).then(
      (page) => {
        setEvents((prev) => (offset ? [...prev, ...page] : page));
        setMore(page.length === PAGE);
      },
      (e) => setError(e.message),
    );
  }, []);

  useEffect(() => {
    refresh();
    loadEvents(0);
  }, [refresh, loadEvents]);

  if (!profile) return <div className="page-message">{error ?? "Loading…"}</div>;
  const d = profile.data;

  return (
    <div className="admin profile">
      <header className="admin-header">
        <div>
          <Link to="/">← Library</Link>
          <h1>Your profile</h1>
          <p className="muted small">
            Signed in as <strong>{user.username}</strong> · member since {new Date(profile.created_at).toLocaleDateString()}
          </p>
        </div>
        <div className="admin-actions">
          <button className="button" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <SignIns profile={profile} providers={config.providers} onChange={refresh} onError={setError} />

      <section>
        <h2>Your data</h2>
        <p className="muted small">
          What the reader has recorded about you: your library and how you read it. Only you and the site's admins
          can see it. The <a href="/privacy">privacy policy</a> explains what it's used for.
        </p>
        <div className="tiles">
          <Tile label="Papers in your library" value={d.papers} />
          <Tile label="Reading sessions" value={d.sessions} detail={`${duration(d.active_ms)} reading in total`} />
          <Tile
            label="Recorded events"
            value={d.events}
            detail={d.first_event_ts ? `${when(d.first_event_ts)} to ${ago(d.last_event_ts)}` : "none yet"}
          />
        </div>
        <p>
          <a className="button" href={api.me.exportUrl} download>
            Download my data
          </a>{" "}
          <span className="muted small">One JSON file with everything listed here, including every event.</span>
        </p>

        <h3>Activity</h3>
        {events.length === 0 ? (
          <p className="muted small">Nothing recorded yet.</p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>When</th>
                <th>What</th>
                <th>Paper</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.event_id}>
                  <td className="nowrap muted small">{when(e.ts)}</td>
                  <td className="nowrap">{EVENT_LABELS[e.type] ?? e.type}</td>
                  <td className="truncate">{e.title ?? e.arxiv_id}</td>
                  <td className="muted small">{detail(e)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {more && (
          <p>
            <button className="button" onClick={() => loadEvents(events.length)}>
              Show more
            </button>
          </p>
        )}
      </section>

      <Tokens profile={profile} onChange={refresh} onError={setError} />
      <DeleteAccount profile={profile} />
    </div>
  );
}

function Tile({ label, value, detail }: { label: string; value: number; detail?: string }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value.toLocaleString()}</div>
      {detail && <div className="tile-detail">{detail}</div>}
    </div>
  );
}

function SignIns({
  profile,
  providers,
  onChange,
  onError,
}: {
  profile: ProfileData;
  providers: Provider[];
  onChange: () => void;
  onError: (m: string | null) => void;
}) {
  const linked = new Set(profile.identities.map((i) => i.provider));
  const canUnlink = profile.identities.length > 1;

  async function link(p: Provider) {
    onError(null);
    try {
      window.location.assign((await api.auth.oauthStart(p, "link")).url);
    } catch (e) {
      onError((e as Error).message);
    }
  }

  async function unlink(p: Provider) {
    onError(null);
    try {
      await api.me.unlink(p);
      onChange();
    } catch (e) {
      onError((e as Error).message);
    }
  }

  return (
    <section>
      <h2>Ways to sign in</h2>
      <table className="grid">
        <tbody>
          {profile.identities.map((i) => (
            <tr key={i.provider}>
              <td>{PROVIDER_NAMES[i.provider] ?? i.provider}</td>
              <td className="muted small">
                {i.email ?? "no email shared"} · last used {ago(i.last_used_at)}
              </td>
              <td className="num">
                <button
                  className="link"
                  disabled={!canUnlink}
                  title={canUnlink ? undefined : "Your only way to sign in"}
                  onClick={() => void unlink(i.provider)}
                >
                  Unlink
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {providers.some((p) => !linked.has(p)) && (
        <p>
          {providers
            .filter((p) => !linked.has(p))
            .map((p) => (
              <button key={p} className="button" onClick={() => void link(p)}>
                Link {PROVIDER_NAMES[p]}
              </button>
            ))}
        </p>
      )}
    </section>
  );
}

function Tokens({
  profile,
  onChange,
  onError,
}: {
  profile: ProfileData;
  onChange: () => void;
  onError: (m: string | null) => void;
}) {
  const [name, setName] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  const origin = window.location.origin;

  async function create(e: FormEvent) {
    e.preventDefault();
    onError(null);
    try {
      setCreated((await api.me.createToken(name.trim())).token);
      setName("");
      onChange();
    } catch (err) {
      onError((err as Error).message);
    }
  }

  async function revoke(id: number) {
    onError(null);
    try {
      await api.me.revokeToken(id);
      onChange();
    } catch (err) {
      onError((err as Error).message);
    }
  }

  return (
    <section>
      <h2>Export API</h2>
      <p className="muted small">
        Fetch the same file as "Download my data" from a script. A token only works for the export, and only ever
        returns your own data.
      </p>
      <pre className="mono code-block">{`curl -H "Authorization: Bearer <token>" ${origin}${api.me.exportUrl} -o my-data.json`}</pre>
      {created && (
        <div className="notice token-created" role="status">
          <p>
            New token. Copy it now; it won't be shown again:
          </p>
          <code className="token">{created}</code>{" "}
          <button className="link" onClick={() => void navigator.clipboard?.writeText(created)}>
            Copy
          </button>
        </div>
      )}
      {profile.tokens.length > 0 && (
        <table className="grid">
          <thead>
            <tr>
              <th>Name</th>
              <th>Created</th>
              <th>Last used</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {profile.tokens.map((t) => (
              <tr key={t.id}>
                <td>{t.name}</td>
                <td className="muted small">{when(t.created_at)}</td>
                <td className="muted small">{ago(t.last_used_at)}</td>
                <td className="num">
                  <button className="link" onClick={() => void revoke(t.id)}>
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form className="inline-form" onSubmit={create}>
        <input placeholder="Token name, e.g. laptop script" maxLength={64} value={name} onChange={(e) => setName(e.target.value)} />
        <button className="button" type="submit" disabled={!name.trim()}>
          Create token
        </button>
      </form>
    </section>
  );
}

function DeleteAccount({ profile }: { profile: ProfileData }) {
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.me.deleteAccount(confirm);
      window.location.assign("/"); // signed out: back to the sign-in screen
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <section className="danger-zone">
      <h2>Delete your account</h2>
      <p className="small">
        Deletes your account and everything above: your library, tags, ratings, answers, reading sessions and every
        recorded event. This can't be undone. Copies in the site's backups expire within{" "}
        {profile.backup_retention_days} days.
      </p>
      <form className="inline-form" onSubmit={remove}>
        <input
          aria-label="Type your username to confirm"
          placeholder={`Type ${profile.username} to confirm`}
          autoComplete="off"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        <button
          className="button danger"
          type="submit"
          disabled={busy || confirm.trim().toLowerCase() !== profile.username}
        >
          {busy ? "Deleting…" : "Delete my account and data"}
        </button>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
