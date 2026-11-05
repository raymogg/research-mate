export const STATUSES = ["unread", "reading", "read", "skimmed", "abandoned"] as const;
export type Status = (typeof STATUSES)[number];
export type SortKey = "last_opened" | "added" | "read_time" | "rating";

export interface Metrics {
  total_ms: number;
  visits: number;
  pages_viewed: number;
  completion: number | null;
  furthest_page: number | null;
  page_ms?: Record<string, number>;
}

export interface Paper {
  arxiv_id: string;
  key: string;
  version: string | null;
  title: string;
  authors: string[];
  categories: string[];
  published_at: string | null;
  num_pages: number | null;
  status: Status;
  status_source: "auto" | "manual";
  rating: number | null;
  rating_prompted: boolean;
  feedback_state: FeedbackState;
  source: DiscoverySource | null;
  added_at: string;
  last_opened_at: string | null;
  tags: string[];
  metrics: Metrics;
  abstract?: string;
}

/** none: opened before questions existed; pending: waiting for answers. */
export type FeedbackState = "none" | "pending" | "done" | "skipped";
export const LEVELS = ["too_basic", "just_right", "too_advanced"] as const;
export const REASONS = ["off_topic", "poorly_written", "not_now", "great_method", "great_idea"] as const;

export interface Answers {
  interest?: number | null;
  value?: number | null;
  novelty?: number | null;
  level?: (typeof LEVELS)[number] | null;
  reasons?: (typeof REASONS)[number][];
  skipped?: "skip" | "didnt_read";
  context: "after_reading" | "next_visit";
  shown_ms: number;
}

/** Where the user found a paper they added. Asked, optional. */
export const DISCOVERY_SOURCES = ["colleague", "social", "search", "citation", "feed", "other"] as const;
export type DiscoverySource = (typeof DISCOVERY_SOURCES)[number];

/** How a reading session started. Reload, history and typed URLs are all "direct". */
export type Via =
  | { kind: "library"; slate_id: string; position: number }
  | { kind: "pasted" | "direct" | "resume" };

/** Sent when a session opens; the server keeps whatever parts are valid. */
export interface SessionContext {
  via: Via;
  device: "phone" | "tablet" | "desktop";
  viewport: [number, number];
  screen: [number, number];
  dpr: number;
  local_hour: number;
  weekday: number; // 1 = Monday
  tz: string;
  tz_offset_min: number;
}

/** Bump with the backend's SCHEMA_VERSION when event shapes or meanings change. */
export const SCHEMA_VERSION = 4;

/** The thresholds stamped on each client event, so old events stay interpretable. */
export type EventConfig = Pick<ClientConfig, "idle_cutoff_s" | "return_visit_gap_min" | "page_viewed_min_s" | "auto_read_threshold">;

export function eventConfig(c: ClientConfig): EventConfig {
  const { idle_cutoff_s, return_visit_gap_min, page_viewed_min_s, auto_read_threshold } = c;
  return { idle_cutoff_s, return_visit_gap_min, page_viewed_min_s, auto_read_threshold };
}

export interface ClientConfig {
  idle_cutoff_s: number;
  return_visit_gap_min: number;
  page_viewed_min_s: number;
  auto_read_threshold: number;
  flush_interval_s: number;
  signup_open: boolean;
}

export interface User {
  id: number;
  username: string;
  is_admin: boolean;
}

export interface AdminUser extends User {
  created_at: string;
  papers: number;
  events: number;
  last_event_ts: string | null;
  total_active_ms: number;
}

export interface ClientEvent {
  event_id: string;
  type: "page_dwell" | "paper_closed" | "text_copied" | "zoom_changed" | "impression";
  arxiv_id: string;
  session_id: string | null; // null only for impressions
  ts: string;
  payload: Record<string, unknown>;
  schema_version: number;
  config: EventConfig;
}

export interface EventType {
  type: string;
  source: "client" | "server";
  description: string;
  payload: Record<string, string>;
  count: number;
  last_ts: string | null;
}

export interface Overview {
  totals: {
    users: number;
    papers: number;
    papers_by_status: Record<Status, number>;
    rated: number;
    tags: number;
    sessions: number;
    return_visits: number;
    total_active_ms: number;
    events: number;
    unreported_events: number;
    oldest_unreported_ts: string | null;
    first_event_ts: string | null;
    last_event_ts: string | null;
  };
  event_types: EventType[];
  storage: {
    data_dir: string;
    db_bytes: number;
    pdf_count: number;
    pdf_bytes: number;
    feedback_log: string;
    feedback_log_lines: number;
    feedback_log_bytes: number;
  };
  settings: ClientConfig & { feedback_url: string };
}

export interface StoredEvent {
  event_id: string;
  user_id: number;
  username: string | null;
  type: string;
  arxiv_id: string;
  title: string | null;
  session_id: string | null;
  ts: string;
  payload: Record<string, unknown>;
  schema_version: number;
  config: Record<string, number> | null;
  reported_at: string | null;
}

export interface SessionRow {
  session_id: string;
  user_id: number;
  username: string | null;
  arxiv_id: string;
  title: string | null;
  started_at: string;
  ended_at: string | null;
  active_ms: number;
  is_return_visit: boolean;
  events: number;
  pages_touched: number;
}

export interface PaperReading {
  user_id: number;
  username: string | null;
  arxiv_id: string;
  title: string;
  status: Status;
  status_source: "auto" | "manual";
  rating: number | null;
  feedback_state: FeedbackState;
  feedback: Answers | null;
  source: DiscoverySource | null;
  num_pages: number | null;
  total_ms: number;
  visits: number;
  pages_viewed: number;
  page_ms: Record<string, number>;
  added_at: string;
  last_opened_at: string | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** Fired when any request comes back 401, so the app can show the sign-in screen. */
export const SIGNED_OUT = "taste-reader:signed-out";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (res.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event(SIGNED_OUT));
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // keep the status text
    }
    throw new ApiError(message, res.status);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const tagPath = (key: string, tag: string) => `/api/papers/${key}/tags/${encodeURIComponent(tag)}`;

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  config: () => request<ClientConfig>("/api/config"),
  auth: {
    me: () => request<User>("/api/auth/me"),
    login: (username: string, password: string) => request<User>("/api/auth/login", post({ username, password })),
    signup: (username: string, password: string, invite_code: string) =>
      request<User>("/api/auth/signup", post({ username, password, invite_code })),
    logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  },
  listPapers: (params: URLSearchParams) => request<Paper[]>(`/api/papers?${params}`),
  getPaper: (key: string) => request<Paper>(`/api/papers/${key}`),
  addPaper: (input: string, source: DiscoverySource | null, intent: "open" | "later" = "open") =>
    request<Paper>("/api/papers", post({ input, source, intent })),
  updatePaper: (
    key: string,
    body: Partial<{ status: Status; rating: number | null; prompted: boolean; rating_prompted: boolean; num_pages: number }>,
  ) => request<Paper>(`/api/papers/${key}`, { method: "PATCH", body: JSON.stringify(body) }),
  openSession: (key: string, context: SessionContext) =>
    request<{ session_id: string; is_return_visit: boolean; paper: Paper }>(
      `/api/papers/${key}/sessions`,
      post({ context }),
    ),
  addTag: (key: string, tag: string) => request<Paper>(tagPath(key, tag), { method: "PUT" }),
  removeTag: (key: string, tag: string) => request<Paper>(tagPath(key, tag), { method: "DELETE" }),
  listTags: () => request<{ name: string; count: number }[]>("/api/tags"),
  pdfUrl: (key: string) => `/api/papers/${key}/pdf`,
  pendingFeedback: () => request<Paper[]>("/api/feedback/pending"),
  answer: (key: string, body: Answers) => request<Paper>(`/api/papers/${key}/feedback`, post(body)),
  admin: {
    users: () => request<AdminUser[]>("/api/admin/users"),
    overview: (params: URLSearchParams) => request<Overview>(`/api/admin/overview?${params}`),
    events: (params: URLSearchParams) => request<StoredEvent[]>(`/api/admin/events?${params}`),
    sessions: (params: URLSearchParams) => request<SessionRow[]>(`/api/admin/sessions?${params}`),
    papers: (params: URLSearchParams) => request<PaperReading[]>(`/api/admin/papers?${params}`),
  },
};
