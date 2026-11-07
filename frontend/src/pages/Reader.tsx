import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { useApp } from "../App";
import { api, eventConfig, type Paper, type ReaderView, type Via } from "../api";
import { type HtmlFailure, HtmlView } from "../components/HtmlView";
import { PdfView } from "../components/PdfView";
import { RatingStars } from "../components/RatingStars";
import { ThemeToggle } from "../components/SiteFooter";
import { StatusSelect } from "../components/StatusSelect";
import { TagInput } from "../components/TagInput";
import { authorLine, duration } from "../format";
import { migrateKey } from "../storage";
import { useTheme } from "../theme";
import { deviceKind, sessionContext } from "../tracking/context";
import { DwellTracker, pageUnits, type Units, type VisibleRange } from "../tracking/dwellTracker";

const ZOOM_STEP = 0.15;
const VIEW_KEY = "researchmate:view"; // the view this browser last picked
migrateKey("taste-reader:view", VIEW_KEY);

/** The view to open papers in: the one picked last on this device, else HTML on phones. */
function preferredView(): ReaderView {
  try {
    const saved = localStorage.getItem(VIEW_KEY);
    if (saved === "pdf" || saved === "html") return saved;
  } catch {
    // storage blocked: use the default
  }
  return deviceKind() === "phone" ? "html" : "pdf";
}

const FALLBACK_NOTICE: Record<HtmlFailure, string> = {
  no_html: "arXiv has no HTML version of this paper, so here's the PDF.",
  html_failed: "The HTML version wouldn't load, so here's the PDF.",
};

export function Reader() {
  const { key = "" } = useParams();
  const { config, buffer } = useApp();
  const [paper, setPaper] = useState<Paper | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [numPages, setNumPages] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [section, setSection] = useState<{ index: number; of: number } | null>(null);
  const [view, setView] = useState<ReaderView>(preferredView);
  const [notice, setNotice] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [sessionMs, setSessionMs] = useState(0);
  const [allTags, setAllTags] = useState<string[]>([]);
  const [showPrompt, setShowPrompt] = useState(false);
  const tracker = useRef<DwellTracker | null>(null);
  const pageRef = useRef<number | string | null>(null); // unit in view: PDF page or HTML section
  const visibleRange = useRef<VisibleRange | null>(null);
  const htmlUnits = useRef<Units | null>(null);
  const viewRef = useRef(view);
  const switchReason = useRef<"user" | HtmlFailure>("user");
  const zoomRef = useRef(1);
  const location = useLocation();
  const navigate = useNavigate();
  // How we got here, from the link that opened the reader. Later sessions on this
  // page (after a long absence, or a back/forward restore) are "resume".
  const via = useRef<Via>((location.state as { via?: Via } | null)?.via ?? { kind: "direct" });

  // Drop the link's state so a reload counts as "direct", not as another click.
  useEffect(() => {
    if (location.state) navigate(location.pathname, { replace: true, state: null });
  }, [location, navigate]);

  const merge = useCallback((updated: Paper) => {
    setPaper((prev) => (prev ? { ...prev, ...updated, abstract: updated.abstract ?? prev.abstract } : updated));
  }, []);
  const run = (p: Promise<Paper>) => p.then(merge).catch((e) => setError(e.message));

  // Time goes to PDF pages or HTML sections, whichever is showing. Both read the
  // live view through refs, so a tracker can outlive a switch.
  const units = useMemo<Record<ReaderView, Units>>(
    () => ({
      pdf: pageUnits((p) => visibleRange.current?.(p) ?? null),
      html: {
        dwellType: "section_dwell",
        describe: (u) => htmlUnits.current?.describe(u) ?? { section: String(u) },
        visibleRange: (u) => htmlUnits.current?.visibleRange(u) ?? null,
      },
    }),
    [],
  );

  // One reading session per visit. Coming back to a tab hidden for longer than the
  // return-visit gap closes the old session and opens a new one.
  useEffect(() => {
    let cancelled = false;
    let hiddenAt: number | null = null;
    const gapMs = config.return_visit_gap_min * 60_000;

    const start = async () => {
      const openedIn = viewRef.current;
      const session = await api.openSession(key, sessionContext(via.current, openedIn));
      if (cancelled) return;
      via.current = { kind: "resume" };
      merge(session.paper);
      setSessionMs(0);
      const t = new DwellTracker(
        buffer,
        session.paper.arxiv_id,
        session.session_id,
        eventConfig(config),
        units[viewRef.current],
        setSessionMs,
      );
      // Switched while the session was opening (e.g. no HTML): record it now
      if (viewRef.current !== openedIn) t.viewChanged(openedIn, viewRef.current, switchReason.current);
      if (pageRef.current !== null) t.setPage(pageRef.current);
      tracker.current = t;
    };
    const end = (keepalive = false) => {
      tracker.current?.close();
      tracker.current = null;
      void buffer.flush({ keepalive });
    };
    const onPageHide = () => end(true);
    const onPageShow = (e: PageTransitionEvent) => {
      // Restored from the back/forward cache: the old session was closed on pagehide.
      if (e.persisted && !tracker.current) start().catch((err) => setError(err.message));
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        hiddenAt = Date.now();
      } else if (hiddenAt !== null && Date.now() - hiddenAt >= gapMs) {
        hiddenAt = null;
        end();
        start().catch((e) => setError(e.message));
      }
    };

    start().catch((e) => setError(e.message));
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("pageshow", onPageShow);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("pageshow", onPageShow);
      end();
    };
  }, [key, buffer, config, merge, units]);

  // Server-side state changes (e.g. auto-Read) arrive with each event flush.
  useEffect(
    () =>
      buffer.onPapers((papers) => {
        const updated = papers.find((p) => p.key === key);
        if (updated) merge(updated);
      }),
    [buffer, key, merge],
  );

  useEffect(() => {
    api.listTags().then((ts) => setAllTags(ts.map((t) => t.name))).catch(() => {});
  }, []);

  // Tell the backend the page count once the PDF has loaded; it drives completion.
  useEffect(() => {
    if (paper && numPages && paper.num_pages !== numPages) {
      api.updatePaper(paper.key, { num_pages: numPages }).then(merge).catch(() => {});
    }
  }, [paper, numPages, merge]);

  // Ask for a rating once, right after the paper is auto-marked Read.
  useEffect(() => {
    if (!paper || paper.status !== "read" || paper.status_source !== "auto") return;
    if (paper.rating_prompted || paper.rating !== null) return;
    setShowPrompt(true);
    api.updatePaper(paper.key, { rating_prompted: true }).then(merge).catch(() => {});
  }, [paper, merge]);

  const onPageInView = useCallback((p: number) => {
    pageRef.current = p;
    setPage(p);
    tracker.current?.setPage(p);
  }, []);
  const onSectionInView = useCallback((id: string, index: number, of: number) => {
    pageRef.current = id;
    setSection({ index, of });
    tracker.current?.setPage(id);
  }, []);

  const switchView = useCallback((to: ReaderView, reason: "user" | HtmlFailure) => {
    const from = viewRef.current;
    if (from === to) return;
    viewRef.current = to;
    switchReason.current = reason;
    pageRef.current = null;
    tracker.current?.setUnits(units[to]); // hands over the old view's time first
    tracker.current?.viewChanged(from, to, reason);
    setView(to);
    setSection(null);
    zoomRef.current = 1;
    setZoom(1);
    setNotice(reason === "user" ? null : FALLBACK_NOTICE[reason]);
    if (reason === "user") {
      try {
        localStorage.setItem(VIEW_KEY, to);
      } catch {
        // not remembered; fine
      }
    }
  }, [units]);
  const onHtmlError = useCallback((kind: HtmlFailure) => switchView("pdf", kind), [switchView]);
  const onHtmlActivity = useCallback(() => tracker.current?.touch(), []);
  const onLoaded = useCallback((n: number) => setNumPages(n), []);
  const onPdfError = useCallback((m: string) => setError(`Couldn't load the PDF: ${m}`), []);
  const onCopy = useCallback((p: number | string | null, chars: number) => tracker.current?.copied(p, chars), []);
  const [theme] = useTheme();
  const changeZoom = (next: number) => {
    const from = zoomRef.current;
    const to = Math.round(next * 100) / 100;
    zoomRef.current = to;
    setZoom(to);
    tracker.current?.zoomed(from, to);
  };

  return (
    <div className="reader">
      <header className="reader-bar">
        <div className="reader-row">
          <Link to="/" className="back">
            ← Library
          </Link>
          <div className="reader-title" title={paper?.title}>
            <strong>{paper?.title ?? "Loading…"}</strong>
            {paper && (
              <span className="muted small">
                {" "}
                {authorLine(paper.authors)} ·{" "}
                <a href={`https://arxiv.org/abs/${paper.arxiv_id}`} target="_blank" rel="noreferrer">
                  arXiv:{paper.arxiv_id}
                </a>
              </span>
            )}
          </div>
          <div className="reader-tools">
            <span className="muted small" title="Active reading time this session">
              {duration(sessionMs)}
            </span>
            <span className="segmented view-toggle" role="radiogroup" aria-label="Show the paper as">
              {(["pdf", "html"] as const).map((v) => (
                <button
                  key={v}
                  role="radio"
                  aria-checked={view === v}
                  title={v === "html" ? "arXiv's HTML version: reflows to fit small screens" : "The PDF"}
                  onClick={() => switchView(v, "user")}
                >
                  {v.toUpperCase()}
                </button>
              ))}
            </span>
            <span className="small">
              {view === "pdf"
                ? `Page ${page}${numPages ? ` / ${numPages}` : ""}`
                : section
                  ? `Section ${section.index} / ${section.of}`
                  : ""}
            </span>
            <span className="zoom">
              <button onClick={() => changeZoom(Math.max(0.4, zoom - ZOOM_STEP))} aria-label="Zoom out">
                −
              </button>
              <button onClick={() => changeZoom(1)} title={view === "pdf" ? "Fit width" : "Normal text size"}>
                {Math.round(zoom * 100)}%
              </button>
              <button onClick={() => changeZoom(Math.min(3, zoom + ZOOM_STEP))} aria-label="Zoom in">
                +
              </button>
            </span>
            <ThemeToggle />
          </div>
        </div>
        {paper && (
          <div className="reader-row">
            <StatusSelect value={paper.status} onChange={(s) => run(api.updatePaper(paper.key, { status: s }))} />
            <RatingStars value={paper.rating} onChange={(r) => run(api.updatePaper(paper.key, { rating: r }))} />
            <TagInput
              tags={paper.tags}
              suggestions={allTags}
              onAdd={(t) => run(api.addTag(paper.key, t))}
              onRemove={(t) => run(api.removeTag(paper.key, t))}
            />
          </div>
        )}
        {showPrompt && paper && (
          <div className="rating-prompt" role="status">
            <span>You've read most of this paper. How useful was it?</span>
            <RatingStars
              size="md"
              value={paper.rating}
              onChange={(r) => {
                run(api.updatePaper(paper.key, { rating: r, prompted: true }));
                setShowPrompt(false);
              }}
            />
            <button className="link" onClick={() => setShowPrompt(false)}>
              Not now
            </button>
          </div>
        )}
        {notice && (
          <p className="notice small" role="status">
            {notice}
          </p>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </header>
      {view === "html" ? (
        <HtmlView
          url={api.htmlUrl(key, theme)}
          zoom={zoom}
          onSectionInView={onSectionInView}
          onError={onHtmlError}
          onCopy={onCopy}
          onActivity={onHtmlActivity}
          unitsRef={htmlUnits}
        />
      ) : (
        <PdfView
          url={api.pdfUrl(key)}
          zoom={zoom}
          onLoaded={onLoaded}
          onPageInView={onPageInView}
          onError={onPdfError}
          onCopy={onCopy}
          visibleRangeRef={visibleRange}
        />
      )}
    </div>
  );
}
