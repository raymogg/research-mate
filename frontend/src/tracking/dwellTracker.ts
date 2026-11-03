import type { ClientEvent } from "../api";
import type { EventBuffer } from "./eventBuffer";

const TICK_MS = 1000;
const MAX_TICK_MS = 5000; // ignore gaps from a sleeping laptop or throttled timers
const INPUT_EVENTS = ["mousemove", "mousedown", "wheel", "keydown", "touchstart", "scroll"] as const;

/**
 * Counts active reading time per page for one session. Time accrues only while
 * the tab is visible and there was input within the idle cutoff, and goes to
 * whichever page is most in view. Pending time becomes `page_dwell` events when
 * the page changes and whenever the buffer flushes.
 */
export class DwellTracker {
  private page: number | null = null;
  private pending = new Map<number, number>();
  private lastInput = Date.now();
  private lastTick = Date.now();
  private activeMs = 0;
  private timer: number;
  private removeCollector: () => void;
  private onInput = () => {
    this.lastInput = Date.now();
  };

  constructor(
    private buffer: EventBuffer,
    private arxivId: string,
    readonly sessionId: string,
    private idleCutoffMs: number,
    private onTick?: (activeMs: number) => void,
  ) {
    this.timer = window.setInterval(() => this.tick(), TICK_MS);
    this.removeCollector = buffer.addCollector(() => this.drain());
    INPUT_EVENTS.forEach((t) => window.addEventListener(t, this.onInput, { passive: true, capture: true }));
  }

  get isActive() {
    return document.visibilityState === "visible" && Date.now() - this.lastInput < this.idleCutoffMs;
  }

  setPage(page: number) {
    if (page === this.page) return;
    this.tick();
    const previous = this.page;
    this.page = page;
    if (previous !== null) this.drain(previous);
  }

  /** Ends the session: hands over remaining time and emits `paper_closed`. */
  close() {
    this.tick();
    this.drain();
    window.clearInterval(this.timer);
    this.removeCollector();
    INPUT_EVENTS.forEach((t) => window.removeEventListener(t, this.onInput, { capture: true }));
    this.emit("paper_closed", { active_ms: this.activeMs });
  }

  private tick() {
    const now = Date.now();
    const elapsed = Math.min(now - this.lastTick, MAX_TICK_MS);
    this.lastTick = now;
    if (this.page === null || !this.isActive) return;
    this.pending.set(this.page, (this.pending.get(this.page) ?? 0) + elapsed);
    this.activeMs += elapsed;
    this.onTick?.(this.activeMs);
  }

  private drain(only?: number) {
    for (const [page, ms] of this.pending) {
      if (only !== undefined && page !== only) continue;
      if (ms > 0) this.emit("page_dwell", { page, active_ms: Math.round(ms) });
      this.pending.delete(page);
    }
  }

  private emit(type: ClientEvent["type"], payload: Record<string, unknown>) {
    this.buffer.push({
      event_id: crypto.randomUUID(),
      type,
      arxiv_id: this.arxivId,
      session_id: this.sessionId,
      ts: new Date().toISOString(),
      payload,
    });
  }
}
