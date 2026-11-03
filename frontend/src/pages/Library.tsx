import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useApp } from "../App";
import {
  api,
  DISCOVERY_SOURCES,
  type DiscoverySource,
  eventConfig,
  type Paper,
  type SortKey,
  STATUSES,
  type Status,
} from "../api";
import { RatingStars } from "../components/RatingStars";
import { STATUS_LABELS, StatusSelect } from "../components/StatusSelect";
import { TagInput } from "../components/TagInput";
import { ago, authorLine, duration } from "../format";
import { ImpressionTracker, type Slate } from "../tracking/impressions";

const SORTS: Record<SortKey, string> = {
  last_opened: "Last opened",
  added: "Date added",
  read_time: "Read time",
  rating: "Rating",
};

const SOURCE_LABELS: Record<DiscoverySource, string> = {
  colleague: "A colleague",
  social: "Social media",
  search: "Search",
  citation: "A citation",
  feed: "Newsletter or feed",
  other: "Somewhere else",
};

/** The library as one slate: the user's own papers, in the order they chose. */
function librarySlate(papers: Paper[], params: URLSearchParams): Slate {
  const sort = params.get("sort") ?? "last_opened";
  return {
    id: crypto.randomUUID(),
    list: "library",
    filters: {
      sort,
      status: params.get("status") || null,
      tags: params.getAll("tag"),
      min_rating: params.get("min_rating") ? Number(params.get("min_rating")) : null,
    },
    policy: `library:${sort}`,
    purpose: "library",
    items: papers.map((p) => ({ arxiv_id: p.arxiv_id })),
  };
}

export function Library() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [papers, setPapers] = useState<Paper[] | null>(null);
  const [slate, setSlate] = useState<Slate | null>(null);
  const [source, setSource] = useState<DiscoverySource | null>(null);
  const table = useRef<HTMLTableElement>(null);
  const [allTags, setAllTags] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const status = params.get("status") ?? "";
  const tags = params.getAll("tag");
  const minRating = params.get("min_rating") ?? "";
  const sort = (params.get("sort") as SortKey) ?? "last_opened";

  const refresh = useCallback(() => {
    api
      .listPapers(params)
      .then((ps) => {
        setPapers(ps);
        setSlate(librarySlate(ps, params));
      })
      .catch((e) => setError(e.message));
    api.listTags().then((ts) => setAllTags(ts.map((t) => t.name))).catch(() => {});
  }, [params]);

  useEffect(refresh, [refresh]);

  // The reader's last flush can land after this page loads; pick up its numbers.
  const { buffer, config, user, signOut } = useApp();
  useEffect(
    () =>
      buffer.onPapers((updated) => {
        const byId = new Map(updated.map((p) => [p.arxiv_id, p]));
        setPapers((ps) => ps?.map((p) => byId.get(p.arxiv_id) ?? p) ?? null);
      }),
    [buffer],
  );

  // Log which papers were actually on screen, and where, for each load of the list.
  useEffect(() => {
    if (!slate || !table.current) return;
    const tracker = new ImpressionTracker(buffer, eventConfig(config), slate, table.current);
    return () => tracker.stop();
  }, [slate, buffer, config]);

  const setParam = (name: string, value: string | string[]) => {
    const next = new URLSearchParams(params);
    next.delete(name);
    for (const v of Array.isArray(value) ? value : [value]) if (v) next.append(name, v);
    setParams(next, { replace: true });
  };

  const replace = (updated: Paper) => {
    setPapers((ps) => ps?.map((p) => (p.arxiv_id === updated.arxiv_id ? updated : p)) ?? null);
    api.listTags().then((ts) => setAllTags(ts.map((t) => t.name))).catch(() => {});
  };
  const run = (p: Promise<Paper>) => p.then(replace).catch((e) => setError(e.message));

  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    setAdding(true);
    setError(null);
    try {
      const paper = await api.addPaper(input.trim(), source);
      navigate(`/paper/${paper.key}`, { state: { via: { kind: "pasted" } } });
    } catch (err) {
      setError((err as Error).message);
      setAdding(false);
    }
  };

  const filtered = Boolean(status || tags.length || minRating);

  return (
    <div className="library">
      <header className="library-header">
        <div className="title-row">
          <h1>Reading library</h1>
          <div className="account small">
            {user.is_admin && <Link to="/admin">Captured data →</Link>}
            <span className="muted">{user.username}</span>
            <button className="link" onClick={() => void signOut()}>
              Sign out
            </button>
          </div>
        </div>
        <form className="add-form" onSubmit={add}>
          <input
            autoFocus
            placeholder="Paste an arXiv link or ID, e.g. https://arxiv.org/abs/1706.03762"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={adding}
          />
          <button type="submit" disabled={adding || !input.trim()}>
            {adding ? "Adding…" : "Open"}
          </button>
        </form>
        {input.trim() && (
          <div className="source-row">
            <span className="muted small" id="source-label">
              Where did you find it? (optional)
            </span>
            <div className="chips" role="group" aria-labelledby="source-label">
              {DISCOVERY_SOURCES.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="chip"
                  aria-pressed={source === s}
                  disabled={adding}
                  onClick={() => setSource(source === s ? null : s)}
                >
                  {SOURCE_LABELS[s]}
                </button>
              ))}
            </div>
          </div>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </header>

      <div className="filters">
        <label>
          Status
          <select value={status} onChange={(e) => setParam("status", e.target.value)}>
            <option value="">All</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Rating
          <select value={minRating} onChange={(e) => setParam("min_rating", e.target.value)}>
            <option value="">Any</option>
            {[5, 4, 3, 2, 1].map((n) => (
              <option key={n} value={n}>
                {n === 5 ? "5 only" : `${n}+`}
              </option>
            ))}
          </select>
        </label>
        <label>
          Tag
          <select value="" onChange={(e) => setParam("tag", [...tags, e.target.value])}>
            <option value="">{tags.length ? "Add tag…" : "Any"}</option>
            {allTags
              .filter((t) => !tags.includes(t))
              .map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
          </select>
        </label>
        {tags.map((t) => (
          <button key={t} className="chip" onClick={() => setParam("tag", tags.filter((x) => x !== t))}>
            {t} ×
          </button>
        ))}
        <label className="sort">
          Sort
          <select value={sort} onChange={(e) => setParam("sort", e.target.value)}>
            {Object.entries(SORTS).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {papers === null ? (
        <p className="muted">Loading…</p>
      ) : papers.length === 0 ? (
        <p className="empty">
          {filtered ? "No papers match these filters." : "No papers yet. Paste an arXiv link above to start reading."}
        </p>
      ) : (
        <table className="papers" ref={table}>
          <thead>
            <tr>
              <th>Paper</th>
              <th>Status</th>
              <th>Rating</th>
              <th>Tags</th>
              <th className="num">Read time</th>
              <th className="num">Visits</th>
              <th className="num">Pages</th>
              <th className="num">Opened</th>
            </tr>
          </thead>
          <tbody>
            {papers.map((p, i) => (
              <tr key={p.arxiv_id} data-arxiv-id={p.arxiv_id}>
                <td className="paper-cell">
                  <Link
                    to={`/paper/${p.key}`}
                    state={slate && { via: { kind: "library", slate_id: slate.id, position: i + 1 } }}
                    className="paper-title"
                  >
                    {p.title}
                  </Link>
                  <div className="muted small">
                    {authorLine(p.authors)} · {p.arxiv_id}
                    {p.categories[0] ? ` · ${p.categories[0]}` : ""}
                  </div>
                </td>
                <td>
                  <StatusSelect value={p.status} onChange={(s: Status) => run(api.updatePaper(p.key, { status: s }))} />
                </td>
                <td>
                  <RatingStars value={p.rating} onChange={(r) => run(api.updatePaper(p.key, { rating: r }))} />
                </td>
                <td>
                  <TagInput
                    tags={p.tags}
                    suggestions={allTags}
                    onAdd={(t) => run(api.addTag(p.key, t))}
                    onRemove={(t) => run(api.removeTag(p.key, t))}
                    onTagClick={(t) => !tags.includes(t) && setParam("tag", [...tags, t])}
                  />
                </td>
                <td className="num">{duration(p.metrics.total_ms)}</td>
                <td className="num">{p.metrics.visits}</td>
                <td className="num">{p.num_pages ? `${p.metrics.pages_viewed}/${p.num_pages}` : "—"}</td>
                <td className="num muted">{ago(p.last_opened_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
