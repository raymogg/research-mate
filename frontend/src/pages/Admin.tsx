import { type ReactNode, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { type AdminUser, api, type Overview, type PaperReading, type SessionRow, type StoredEvent } from "../api";
import { PageDwellStrip } from "../components/PageDwellStrip";
import { STATUS_LABELS } from "../components/StatusSelect";
import { bytes, duration, when } from "../format";

const PAGE_SIZE = 100;

export function Admin() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [papers, setPapers] = useState<PaperReading[]>([]);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  // Everything below the header is scoped to this user ("" = everyone).
  const [userId, setUserId] = useState("");

  const load = useCallback(() => {
    const params = new URLSearchParams(userId ? { user_id: userId } : {});
    Promise.all([
      api.admin.overview(params),
      api.admin.papers(params),
      api.admin.sessions(params),
      api.admin.users(),
    ])
      .then(([o, p, s, u]) => {
        setOverview(o);
        setPapers(p);
        setSessions(s);
        setUsers(u);
        setLoadedAt(new Date());
        setError(null);
      })
      .catch((e) => setError(e.message));
  }, [userId]);
  useEffect(load, [load]);

  // Event stream filters live here so a session row can narrow the stream.
  const [filter, setFilterState] = useState({ type: "", arxiv_id: "", session_id: "" });
  const setFilter = (f: Filter, scroll = true) => {
    setFilterState(f);
    if (scroll) document.getElementById("events")?.scrollIntoView({ behavior: "smooth" });
  };

  if (error) return <div className="page-message error">{error}</div>;
  if (!overview) return <div className="page-message">Loading…</div>;
  const { totals: t, storage: st, settings } = overview;

  return (
    <div className="admin">
      <header className="admin-header">
        <div>
          <Link to="/">← Library</Link>
          <h1>Captured data</h1>
          <p className="muted">
            Everything the reader stores locally and forwards to the feedback API.
            {t.first_event_ts && ` Recording since ${when(t.first_event_ts)}.`}
          </p>
        </div>
        <div className="admin-actions">
          <label className="muted small">
            User{" "}
            <select
              value={userId}
              onChange={(e) => {
                setUserId(e.target.value);
                setFilterState({ type: "", arxiv_id: "", session_id: "" });
              }}
            >
              <option value="">Everyone ({users.length})</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.username}
                </option>
              ))}
            </select>
          </label>
          <span className="muted small">Updated {loadedAt?.toLocaleTimeString()}</span>
          <button className="button" onClick={load}>
            Refresh
          </button>
        </div>
      </header>

      <section className="tiles">
        <Tile label="Papers" value={t.papers} detail={statusLine(t.papers_by_status)} />
        <Tile
          label="Active reading"
          value={duration(t.total_active_ms)}
          detail={t.sessions ? `${duration(t.total_active_ms / t.sessions)} per session on average` : undefined}
        />
        <Tile label="Sessions" value={t.sessions} detail={`${t.return_visits} return visit${t.return_visits === 1 ? "" : "s"}`} />
        <Tile label="Events stored" value={t.events} detail={t.last_event_ts ? `last ${when(t.last_event_ts)}` : undefined} />
        <Tile
          label="Delivery to feedback API"
          value={
            t.unreported_events ? (
              <span className="state warning">! {t.unreported_events} pending</span>
            ) : (
              <span className="state good">✓ All sent</span>
            )
          }
          detail={
            t.unreported_events
              ? `oldest from ${when(t.oldest_unreported_ts)}`
              : `${st.feedback_log_lines} lines in feedback.log`
          }
        />
      </section>

      {!userId && (
        <section>
          <h2>Users</h2>
          <table className="grid clickable">
            <thead>
              <tr>
                <th>User</th>
                <th>Joined</th>
                <th className="num">Papers</th>
                <th className="num">Active reading</th>
                <th className="num">Events</th>
                <th className="num">Last active</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} onClick={() => setUserId(String(u.id))}>
                  <td>
                    {u.username} {u.is_admin && <span className="muted small">(admin)</span>}
                  </td>
                  <td className="nowrap muted">{when(u.created_at)}</td>
                  <td className="num">{u.papers}</td>
                  <td className="num">{duration(u.total_active_ms)}</td>
                  <td className="num">{u.events}</td>
                  <td className="num muted small">{u.last_event_ts ? when(u.last_event_ts) : "never"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section>
        <h2>What we capture</h2>
        <p className="muted small">
          Every event shares an envelope: <code>event_id</code>, <code>type</code>, <code>arxiv_id</code>,{" "}
          <code>session_id</code>, <code>ts</code>, <code>payload</code> and <code>user_id</code> (a number; the
          username isn't forwarded). Client events come from the browser; server events are emitted by the backend when
          state changes. Accounts store a username, a password hash and sign-up time. No IP addresses or browser details
          are stored.
        </p>
        <table className="grid">
          <thead>
            <tr>
              <th>Event</th>
              <th>Source</th>
              <th>Meaning</th>
              <th>Payload</th>
              <th className="num">Count</th>
              <th className="num">Last seen</th>
            </tr>
          </thead>
          <tbody>
            {overview.event_types.map((e) => (
              <tr key={e.type}>
                <td>
                  <button className="link mono" onClick={() => setFilter({ type: e.type, arxiv_id: "", session_id: "" })}>
                    {e.type}
                  </button>
                </td>
                <td>
                  <span className={`source source-${e.source}`}>{e.source}</span>
                </td>
                <td>{e.description}</td>
                <td className="payload-spec">
                  {Object.entries(e.payload).map(([k, v]) => (
                    <div key={k}>
                      <code>{k}</code> <span className="muted">{v}</span>
                    </div>
                  ))}
                </td>
                <td className="num">{e.count}</td>
                <td className="num muted small">{e.last_ts ? when(e.last_ts) : "never"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Reading by paper</h2>
        <p className="muted small">
          Bars show active time per page, scaled to each paper's busiest page. Solid bars passed the{" "}
          {settings.page_viewed_min_s}s "viewed" threshold; faint bars didn't. Hover a bar for its time.
        </p>
        <table className="grid">
          <thead>
            <tr>
              <th>Paper</th>
              {!userId && <th>User</th>}
              <th>Status</th>
              <th className="num">Rating</th>
              <th className="num">Active</th>
              <th className="num">Visits</th>
              <th className="num">Viewed</th>
              <th>Time per page</th>
            </tr>
          </thead>
          <tbody>
            {papers.map((p) => (
              <tr key={`${p.user_id}:${p.arxiv_id}`}>
                <td className="paper-cell">
                  <button className="link left" onClick={() => setFilter({ type: "", arxiv_id: p.arxiv_id, session_id: "" })}>
                    {p.title}
                  </button>
                  <div className="muted small">{p.arxiv_id}</div>
                </td>
                {!userId && <td>{p.username}</td>}
                <td>
                  {STATUS_LABELS[p.status]} <span className="muted small">({p.status_source})</span>
                </td>
                <td className="num">{p.rating ? `${p.rating}★` : "—"}</td>
                <td className="num">{duration(p.total_ms)}</td>
                <td className="num">{p.visits}</td>
                <td className="num">
                  {p.pages_viewed}/{p.num_pages ?? "?"}
                </td>
                <td>
                  <PageDwellStrip pageMs={p.page_ms} numPages={p.num_pages} viewedMinMs={settings.page_viewed_min_s * 1000} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Sessions</h2>
        <p className="muted small">Most recent {sessions.length}. Click a row to see its events below.</p>
        <table className="grid clickable">
          <thead>
            <tr>
              <th>Started</th>
              {!userId && <th>User</th>}
              <th>Paper</th>
              <th className="num">Active</th>
              <th className="num">Pages</th>
              <th className="num">Events</th>
              <th>Return visit</th>
              <th>Ended</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr
                key={s.session_id}
                className={filter.session_id === s.session_id ? "selected" : undefined}
                onClick={() => setFilter({ type: "", arxiv_id: "", session_id: s.session_id })}
              >
                <td className="nowrap">{when(s.started_at)}</td>
                {!userId && <td>{s.username}</td>}
                <td>{s.title ?? s.arxiv_id}</td>
                <td className="num">{duration(s.active_ms)}</td>
                <td className="num">{s.pages_touched}</td>
                <td className="num">{s.events}</td>
                <td>{s.is_return_visit ? "Yes" : "—"}</td>
                <td className="nowrap muted">{s.ended_at ? when(s.ended_at) : "still open"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <EventStream
        overview={overview}
        papers={papers}
        userId={userId}
        filter={filter}
        setFilter={setFilter}
        reloadKey={loadedAt}
      />

      <section>
        <h2>Storage and settings</h2>
        <dl className="facts">
          <dt>Data directory</dt>
          <dd className="mono">{st.data_dir}</dd>
          <dt>Database</dt>
          <dd>app.db, {bytes(st.db_bytes)}</dd>
          <dt>PDF cache</dt>
          <dd>
            {st.pdf_count} files, {bytes(st.pdf_bytes)}
          </dd>
          <dt>Feedback log</dt>
          <dd>
            <span className="mono">{st.feedback_log}</span>, {st.feedback_log_lines} lines, {bytes(st.feedback_log_bytes)}
          </dd>
          <dt>Feedback URL</dt>
          <dd className="mono">{settings.feedback_url}</dd>
          <dt>Thresholds</dt>
          <dd>
            idle {settings.idle_cutoff_s}s · return visit after {settings.return_visit_gap_min} min · page viewed at{" "}
            {settings.page_viewed_min_s}s · auto-Read at {Math.round(settings.auto_read_threshold * 100)}% · flush every{" "}
            {settings.flush_interval_s}s
          </dd>
          <dt>Export</dt>
          <dd>
            Run <code>make export</code> for users.csv, papers.csv, sessions.csv, page_dwell.csv and events.jsonl.
          </dd>
        </dl>
      </section>
    </div>
  );
}

function statusLine(byStatus: Overview["totals"]["papers_by_status"]): string | undefined {
  const parts = Object.entries(byStatus)
    .filter(([, n]) => n)
    .map(([s, n]) => `${n} ${STATUS_LABELS[s as keyof typeof STATUS_LABELS].toLowerCase()}`);
  return parts.join(" · ") || undefined;
}

function Tile({ label, value, detail }: { label: string; value: ReactNode; detail?: string }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {detail && <div className="tile-detail">{detail}</div>}
    </div>
  );
}

type Filter = { type: string; arxiv_id: string; session_id: string };

function EventStream(props: {
  overview: Overview;
  papers: PaperReading[];
  userId: string;
  filter: Filter;
  setFilter: (f: Filter, scroll?: boolean) => void;
  reloadKey: Date | null;
}) {
  const { overview, papers, userId, filter, setFilter, reloadKey } = props;
  const paperOptions = [...new Map(papers.map((p) => [p.arxiv_id, p.title])).entries()];
  const [events, setEvents] = useState<StoredEvent[]>([]);
  const [more, setMore] = useState(false);

  const fetchPage = useCallback(
    (offset = 0) => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
      if (userId) params.set("user_id", userId);
      Object.entries(filter).forEach(([k, v]) => v && params.set(k, v));
      return api.admin.events(params).then((page) => {
        setMore(page.length === PAGE_SIZE);
        return page;
      });
    },
    [filter, userId],
  );

  useEffect(() => {
    void fetchPage().then(setEvents);
  }, [fetchPage, reloadKey]);

  const filtered = filter.type || filter.arxiv_id || filter.session_id;

  return (
    <section id="events">
      <h2>Event stream</h2>
      <div className="filters">
        <label>
          Type
          <select value={filter.type} onChange={(e) => setFilter({ ...filter, type: e.target.value }, false)}>
            <option value="">All</option>
            {overview.event_types.map((t) => (
              <option key={t.type} value={t.type}>
                {t.type}
              </option>
            ))}
          </select>
        </label>
        <label>
          Paper
          <select value={filter.arxiv_id} onChange={(e) => setFilter({ ...filter, arxiv_id: e.target.value }, false)}>
            <option value="">All</option>
            {paperOptions.map(([id, title]) => (
              <option key={id} value={id}>
                {title.length > 50 ? `${title.slice(0, 50)}…` : title}
              </option>
            ))}
          </select>
        </label>
        {filter.session_id && (
          <button className="chip" onClick={() => setFilter({ ...filter, session_id: "" }, false)}>
            session {filter.session_id.slice(0, 8)} ×
          </button>
        )}
        {filtered && (
          <button className="link" onClick={() => setFilter({ type: "", arxiv_id: "", session_id: "" }, false)}>
            Clear filters
          </button>
        )}
      </div>
      {events.length === 0 ? (
        <p className="muted">No events match.</p>
      ) : (
        <table className="grid">
          <thead>
            <tr>
              <th>Time</th>
              {!userId && <th>User</th>}
              <th>Event</th>
              <th>Paper</th>
              <th>Session</th>
              <th>Payload</th>
              <th>Delivered</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.event_id}>
                <td className="nowrap">{when(e.ts)}</td>
                {!userId && <td>{e.username}</td>}
                <td className="mono">{e.type}</td>
                <td className="truncate" title={e.title ?? e.arxiv_id}>
                  {e.title ?? e.arxiv_id}
                </td>
                <td className="mono muted">{e.session_id?.slice(0, 8) ?? "—"}</td>
                <td>
                  <code className="payload">{JSON.stringify(e.payload)}</code>
                </td>
                <td>
                  {e.reported_at ? (
                    <span className="state good">✓ Sent</span>
                  ) : (
                    <span className="state warning">! Pending</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {more && (
        <button className="button" onClick={() => void fetchPage(events.length).then((p) => setEvents((ev) => [...ev, ...p]))}>
          Load older events
        </button>
      )}
    </section>
  );
}
