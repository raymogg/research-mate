import { SCHEMA_VERSION, type EventConfig } from "../api";
import type { EventBuffer } from "./eventBuffer";

const MIN_VISIBLE_MS = 1000;
const VISIBLE_SHARE = 0.5;

/** One load of a list: which papers, in what order, and what put them there. */
export interface Slate {
  id: string;
  list: "library" | "suggested";
  filters: Record<string, unknown>;
  policy: string;
  purpose: "library" | "exploit" | "explore" | "elicit" | "curriculum";
  items: { arxiv_id: string; score?: number | null; propensity?: number | null }[];
}

/**
 * Emits one `impression` per paper per slate, once at least half of its row has
 * been on screen for a second while the tab is visible. Rows are found by their
 * `data-arxiv-id` attribute inside `root`.
 */
export class ImpressionTracker {
  private seen = new Set<string>();
  private timers = new Map<string, number>();
  private observer: IntersectionObserver;
  private position: Map<string, number>;

  constructor(
    private buffer: EventBuffer,
    private config: EventConfig,
    private slate: Slate,
    root: HTMLElement,
  ) {
    this.position = new Map(slate.items.map((item, i) => [item.arxiv_id, i]));
    this.observer = new IntersectionObserver((entries) => entries.forEach((e) => this.onChange(e)), {
      threshold: VISIBLE_SHARE,
    });
    root.querySelectorAll<HTMLElement>("[data-arxiv-id]").forEach((el) => this.observer.observe(el));
  }

  stop() {
    this.observer.disconnect();
    this.timers.forEach((t) => window.clearTimeout(t));
    this.timers.clear();
  }

  private onChange(entry: IntersectionObserverEntry) {
    const id = (entry.target as HTMLElement).dataset.arxivId;
    if (!id || this.seen.has(id)) return;
    if (entry.isIntersecting && entry.intersectionRatio >= VISIBLE_SHARE) {
      if (!this.timers.has(id)) this.timers.set(id, window.setTimeout(() => this.fire(id), MIN_VISIBLE_MS));
    } else {
      window.clearTimeout(this.timers.get(id));
      this.timers.delete(id);
    }
  }

  private fire(id: string) {
    if (document.visibilityState !== "visible") {
      // Still on screen but the tab is hidden: check again shortly.
      this.timers.set(id, window.setTimeout(() => this.fire(id), MIN_VISIBLE_MS));
      return;
    }
    this.timers.delete(id);
    const i = this.position.get(id);
    if (i === undefined) return;
    this.seen.add(id);
    const item = this.slate.items[i];
    this.buffer.push({
      event_id: crypto.randomUUID(),
      type: "impression",
      arxiv_id: id,
      session_id: null,
      ts: new Date().toISOString(),
      payload: {
        list: this.slate.list,
        slate_id: this.slate.id,
        position: i + 1,
        slate_size: this.slate.items.length,
        filters: this.slate.filters,
        policy: this.slate.policy,
        purpose: this.slate.purpose,
        score: item.score ?? null,
        propensity: item.propensity ?? 1,
      },
      schema_version: SCHEMA_VERSION,
      config: this.config,
    });
  }
}
