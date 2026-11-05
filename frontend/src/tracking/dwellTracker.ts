import { SCHEMA_VERSION, type ClientEvent, type EventConfig } from "../api";
import type { EventBuffer } from "./eventBuffer";

const TICK_MS = 1000;
const MAX_TICK_MS = 5000; // ignore gaps from a sleeping laptop or throttled timers
const INPUT_EVENTS = ["mousemove", "mousedown", "wheel", "keydown", "touchstart", "scroll"] as const;
const BANDS = 10;

/** What reading time is counted against: a PDF page number, or an HTML section id. */
export type Unit = number | string;

/** The part of a unit on screen, as fractions of its height (0 = top), or null if none. */
export type VisibleRange = (unit: Unit) => [top: number, bottom: number] | null;

/** How the current view divides the paper. */
export interface Units {
  dwellType: "page_dwell" | "section_dwell";
  /** Payload fields naming a unit, e.g. { page: 3 } or { section: "S2", index, of, share }. */
  describe: (unit: Unit) => Record<string, unknown>;
  visibleRange: VisibleRange;
}

export const pageUnits = (visibleRange: VisibleRange): Units => ({
  dwellType: "page_dwell",
  describe: (page) => ({ page }),
  visibleRange,
});

/**
 * Counts active reading time per unit (PDF page or HTML section) for one
 * session. Time accrues only while the tab is visible and there was input within
 * the idle cutoff, and goes to whichever unit is most in view. Pending time
 * becomes `page_dwell` / `section_dwell` events when the unit changes and
 * whenever the buffer flushes.
 *
 * Each dwell event also says how long each tenth of the unit was on screen
 * (`band_ms`), so we can tell reading the abstract from reading the whole page.
 */
export class DwellTracker {
  private page: Unit | null = null;
  private pending = new Map<Unit, number>();
  private bands = new Map<Unit, number[]>();
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
    private config: EventConfig,
    private units: Units,
    private onTick?: (activeMs: number) => void,
  ) {
    this.timer = window.setInterval(() => this.tick(), TICK_MS);
    this.removeCollector = buffer.addCollector(() => this.drain());
    INPUT_EVENTS.forEach((t) => window.addEventListener(t, this.onInput, { passive: true, capture: true }));
  }

  get isActive() {
    return document.visibilityState === "visible" && Date.now() - this.lastInput < this.config.idle_cutoff_s * 1000;
  }

  setPage(page: Unit) {
    if (page === this.page) return;
    this.tick();
    const previous = this.page;
    this.page = page;
    if (previous !== null) this.drain(previous);
  }

  /** Switches between PDF pages and HTML sections; time so far goes to the old units. */
  setUnits(units: Units) {
    this.tick();
    this.drain();
    this.units = units;
    this.page = null;
  }

  /** Input inside a frame, which the window's listeners don't see. */
  touch() {
    this.lastInput = Date.now();
  }

  /** Text was copied from `unit` (null if unknown). Only the length is sent. */
  copied(unit: Unit | null, chars: number) {
    if (chars <= 0) return;
    this.emit("text_copied", { page: null, ...(unit === null ? {} : this.units.describe(unit)), chars });
  }

  /** The zoom buttons changed the zoom (1 = fit width, or 100% text in HTML). */
  zoomed(from: number, to: number) {
    if (from !== to && this.page !== null) {
      this.emit("zoom_changed", { from, to, page: null, ...this.units.describe(this.page) });
    }
  }

  /** The reader switched between PDF and HTML. */
  viewChanged(from: string, to: string, reason: string) {
    this.emit("view_changed", { from, to, reason });
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
    const range = this.units.visibleRange(this.page);
    if (range) {
      const bands = this.bands.get(this.page) ?? new Array<number>(BANDS).fill(0);
      for (let i = 0; i < BANDS; i++) {
        if (range[0] < (i + 1) / BANDS && range[1] > i / BANDS) bands[i] += elapsed;
      }
      this.bands.set(this.page, bands);
    }
    this.onTick?.(this.activeMs);
  }

  private drain(only?: Unit) {
    for (const [page, ms] of this.pending) {
      if (only !== undefined && page !== only) continue;
      const bands = (this.bands.get(page) ?? new Array<number>(BANDS).fill(0)).map(Math.round);
      if (ms > 0) {
        this.emit(this.units.dwellType, { ...this.units.describe(page), active_ms: Math.round(ms), band_ms: bands });
      }
      this.pending.delete(page);
      this.bands.delete(page);
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
      schema_version: SCHEMA_VERSION,
      config: this.config,
    });
  }
}
