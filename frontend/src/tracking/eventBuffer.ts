import type { ClientEvent, Paper } from "../api";

const STORAGE_KEY = "taste-reader.pending-events";
const MAX_BATCH = 500;
const MAX_KEEPALIVE_BATCH = 100; // keepalive bodies are capped at 64 KB

type Collector = () => void;
type PapersListener = (papers: Paper[]) => void;

/**
 * Queues events and posts them to the backend every `intervalS` seconds, when the
 * tab is hidden, and when the page is closing. Unsent events are kept in
 * localStorage, so nothing is lost on a crash; the server ignores resends.
 */
export class EventBuffer {
  private queue: ClientEvent[] = [];
  private sending = false;
  private collectors = new Set<Collector>();
  private listeners = new Set<PapersListener>();

  constructor(intervalS: number) {
    this.queue = load();
    window.setInterval(() => void this.flush(), intervalS * 1000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") void this.flush({ keepalive: true });
    });
    window.addEventListener("pagehide", () => void this.flush({ keepalive: true }));
    void this.flush();
  }

  push(event: ClientEvent) {
    this.queue.push(event);
    save(this.queue);
  }

  /** Called at the start of every flush so trackers can hand over pending time. */
  addCollector(fn: Collector) {
    this.collectors.add(fn);
    return () => {
      this.collectors.delete(fn);
    };
  }

  onPapers(fn: PapersListener) {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  }

  async flush({ keepalive = false } = {}) {
    this.collectors.forEach((collect) => collect());
    // A closing page can't wait for an in-flight request, so it always sends.
    if (!this.queue.length || (this.sending && !keepalive)) return;
    const batch = this.queue.slice(0, keepalive ? MAX_KEEPALIVE_BATCH : MAX_BATCH);
    this.sending = true;
    try {
      const res = await fetch("/api/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events: batch }),
        keepalive,
      });
      // Signed out: keep the events and send them after the next sign-in.
      if (!res.ok) throw new Error(`events rejected: ${res.status}`);
      const sent = new Set(batch.map((e) => e.event_id));
      this.queue = this.queue.filter((e) => !sent.has(e.event_id));
      save(this.queue);
      const { papers } = (await res.json()) as { papers: Paper[] };
      if (papers.length) this.listeners.forEach((fn) => fn(papers));
    } catch (err) {
      console.warn("[taste-reader] event flush failed, will retry", err);
    } finally {
      this.sending = false;
    }
  }
}

function load(): ClientEvent[] {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function save(queue: ClientEvent[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(queue));
  } catch {
    // storage unavailable: events still go out from memory
  }
}
