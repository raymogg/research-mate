import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { useApp } from "../App";
import { api, eventConfig, type Paper, type Via } from "../api";
import { PdfView } from "../components/PdfView";
import { RatingStars } from "../components/RatingStars";
import { StatusSelect } from "../components/StatusSelect";
import { TagInput } from "../components/TagInput";
import { authorLine, duration } from "../format";
import { sessionContext } from "../tracking/context";
import { DwellTracker, type VisibleRange } from "../tracking/dwellTracker";

const ZOOM_STEP = 0.15;

export function Reader() {
  const { key = "" } = useParams();
  const { config, buffer } = useApp();
  const [paper, setPaper] = useState<Paper | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [numPages, setNumPages] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [sessionMs, setSessionMs] = useState(0);
  const [allTags, setAllTags] = useState<string[]>([]);
  const [showPrompt, setShowPrompt] = useState(false);
  const tracker = useRef<DwellTracker | null>(null);
  const pageRef = useRef(1);
  const visibleRange = useRef<VisibleRange | null>(null);
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

  // One reading session per visit. Coming back to a tab hidden for longer than the
  // return-visit gap closes the old session and opens a new one.
  useEffect(() => {
    let cancelled = false;
    let hiddenAt: number | null = null;
    const gapMs = config.return_visit_gap_min * 60_000;

    const start = async () => {
      const session = await api.openSession(key, sessionContext(via.current));
      if (cancelled) return;
      via.current = { kind: "resume" };
      merge(session.paper);
      setSessionMs(0);
      const t = new DwellTracker(
        buffer,
        session.paper.arxiv_id,
        session.session_id,
        eventConfig(config),
        (p) => visibleRange.current?.(p) ?? null,
        setSessionMs,
      );
      t.setPage(pageRef.current);
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
  }, [key, buffer, config, merge]);

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
  const onLoaded = useCallback((n: number) => setNumPages(n), []);
  const onPdfError = useCallback((m: string) => setError(`Couldn't load the PDF: ${m}`), []);
  const onCopy = useCallback((p: number | null, chars: number) => tracker.current?.copied(p, chars), []);
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
            <span className="small">
              Page {page}
              {numPages ? ` / ${numPages}` : ""}
            </span>
            <span className="zoom">
              <button onClick={() => changeZoom(Math.max(0.4, zoom - ZOOM_STEP))} aria-label="Zoom out">
                −
              </button>
              <button onClick={() => changeZoom(1)} title="Fit width">
                {Math.round(zoom * 100)}%
              </button>
              <button onClick={() => changeZoom(Math.min(3, zoom + ZOOM_STEP))} aria-label="Zoom in">
                +
              </button>
            </span>
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
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </header>
      <PdfView
        url={api.pdfUrl(key)}
        zoom={zoom}
        onLoaded={onLoaded}
        onPageInView={onPageInView}
        onError={onPdfError}
        onCopy={onCopy}
        visibleRangeRef={visibleRange}
      />
    </div>
  );
}
