import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { memo, type MutableRefObject, useEffect, useRef, useState } from "react";
import { bytes } from "../format";
import type { VisibleRange } from "../tracking/dwellTracker";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

type Size = { width: number; height: number };
type Progress = { source: "arxiv" | "cache"; loaded: number; total: number | null; done: boolean };
const PAGE_GAP = 16;
const SIDE_PADDING = 32;
const MAX_FIT_WIDTH = 960; // fit-to-width, but not so wide that lines get hard to follow

interface Props {
  url: string;
  zoom: number; // multiplier on fit-to-width
  onLoaded: (numPages: number) => void;
  onPageInView: (page: number) => void;
  onError: (message: string) => void;
  onCopy: (page: number | null, chars: number) => void;
  /** Set to a function that reports how much of a page is on screen. */
  visibleRangeRef: MutableRefObject<VisibleRange | null>;
}

export const PdfView = memo(function PdfView(props: Props) {
  const { url, zoom, onLoaded, onPageInView, onError, onCopy, visibleRangeRef } = props;
  const scrollRef = useRef<HTMLDivElement>(null);
  const [doc, setDoc] = useState<pdfjs.PDFDocumentProxy | null>(null);
  const [sizes, setSizes] = useState<Size[]>([]);
  const [width, setWidth] = useState(0);
  const [near, setNear] = useState<Set<number>>(new Set([1, 2]));
  const [progress, setProgress] = useState<Progress | null>(null);
  const callbacks = useRef({ onLoaded, onPageInView, onError, onCopy });
  callbacks.current = { onLoaded, onPageInView, onError, onCopy };

  useEffect(() => {
    visibleRangeRef.current = (page) => {
      const root = scrollRef.current;
      const el = root?.querySelector<HTMLElement>(`[data-page="${page}"]`);
      if (!root || !el) return null;
      const view = root.getBoundingClientRect();
      const box = el.getBoundingClientRect();
      if (!box.height) return null;
      const top = Math.max(0, (view.top - box.top) / box.height);
      const bottom = Math.min(1, (view.bottom - box.top) / box.height);
      return bottom > top ? [top, bottom] : null;
    };
    return () => {
      visibleRangeRef.current = null;
    };
  }, [visibleRangeRef]);

  // Copies from the text layer. Only the length leaves the browser.
  useEffect(() => {
    const onCopyEvent = () => {
      const sel = document.getSelection();
      const root = scrollRef.current;
      if (!sel || !root || !sel.anchorNode || !root.contains(sel.anchorNode)) return;
      const anchor = sel.anchorNode instanceof Element ? sel.anchorNode : sel.anchorNode.parentElement;
      const page = Number(anchor?.closest<HTMLElement>("[data-page]")?.dataset.page) || null;
      callbacks.current.onCopy(page, sel.toString().length);
    };
    document.addEventListener("copy", onCopyEvent);
    return () => document.removeEventListener("copy", onCopyEvent);
  }, []);

  // Fetch the whole file in one request (pdf.js would otherwise fire several range
  // requests), then load it and every page's natural size, so placeholders keep the
  // scroll stable.
  useEffect(() => {
    const abort = new AbortController();
    let task: pdfjs.PDFDocumentLoadingTask | null = null;
    let cancelled = false;
    setProgress(null);
    download(url, abort.signal, (p) => !cancelled && setProgress(p))
      .then((data) => {
        if (cancelled) return null;
        task = pdfjs.getDocument({ data });
        return task.promise;
      })
      .then(async (pdf) => {
        if (!pdf) return;
        const pages = await Promise.all(
          Array.from({ length: pdf.numPages }, (_, i) => pdf.getPage(i + 1)),
        );
        if (cancelled) return;
        setSizes(pages.map((p) => p.getViewport({ scale: 1 })).map(({ width, height }) => ({ width, height })));
        setDoc(pdf);
        callbacks.current.onLoaded(pdf.numPages);
      })
      .catch((err) => !cancelled && callbacks.current.onError(String(err?.message ?? err)));
    return () => {
      cancelled = true;
      abort.abort();
      void task?.destroy();
    };
  }, [url]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const maxBase = Math.max(1, ...sizes.map((s) => s.width));
  const scale = width ? (Math.min(width - SIDE_PADDING, MAX_FIT_WIDTH) / maxBase) * zoom : 0;

  // Which page has the largest visible area, and which pages are close enough to render.
  useEffect(() => {
    const root = scrollRef.current;
    if (!root || !doc || !scale) return;
    const visible = new Map<number, number>();
    const pageEls = root.querySelectorAll<HTMLElement>("[data-page]");

    const inView = new IntersectionObserver(
      (entries) => {
        for (const e of entries) visible.set(Number((e.target as HTMLElement).dataset.page), e.intersectionRect.height);
        let best = 0;
        let bestArea = 0;
        visible.forEach((area, page) => {
          if (area > bestArea) [best, bestArea] = [page, area];
        });
        if (best) callbacks.current.onPageInView(best);
      },
      { root, threshold: Array.from({ length: 21 }, (_, i) => i / 20) },
    );
    const nearby = new IntersectionObserver(
      (entries) =>
        setNear((prev) => {
          const next = new Set(prev);
          for (const e of entries) {
            const page = Number((e.target as HTMLElement).dataset.page);
            if (e.isIntersecting) next.add(page);
            else next.delete(page);
          }
          return next;
        }),
      { root, rootMargin: "150% 0px" },
    );
    pageEls.forEach((el) => {
      inView.observe(el);
      nearby.observe(el);
    });
    return () => {
      inView.disconnect();
      nearby.disconnect();
    };
  }, [doc, scale]);

  return (
    <div className="pdf-scroll" ref={scrollRef}>
      {doc &&
        scale > 0 &&
        sizes.map((size, i) => (
          <PdfPage key={i} doc={doc} pageNumber={i + 1} size={size} scale={scale} render={near.has(i + 1)} />
        ))}
      {!doc && <LoadingState progress={progress} />}
    </div>
  );
});

async function download(url: string, signal: AbortSignal, onProgress: (p: Progress) => void): Promise<Uint8Array> {
  const res = await fetch(url, { signal });
  if (!res.ok) {
    let message = `Couldn't load the PDF (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // keep the status
    }
    throw new Error(message);
  }
  const source = res.headers.get("X-Pdf-Source") === "arxiv" ? "arxiv" : "cache";
  const total = Number(res.headers.get("Content-Length")) || null;
  const reader = res.body!.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  onProgress({ source, loaded, total, done: false });
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.length;
    onProgress({ source, loaded, total, done: false });
  }
  onProgress({ source, loaded, total, done: true });
  const data = new Uint8Array(loaded);
  let offset = 0;
  for (const c of chunks) {
    data.set(c, offset);
    offset += c.length;
  }
  return data;
}

function LoadingState({ progress }: { progress: Progress | null }) {
  if (progress?.done) return <div className="pdf-loading">Preparing pages…</div>;
  const fraction = progress?.total ? progress.loaded / progress.total : null;
  const title = progress?.source === "arxiv" ? "Downloading from arXiv" : "Loading PDF";
  return (
    <div className="pdf-loading">
      <div className="pdf-progress">
        <div className="pdf-progress-title">{title}…</div>
        <div
          className={`pdf-progress-track${fraction === null ? " indeterminate" : ""}`}
          role="progressbar"
          aria-label={title}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={fraction === null ? undefined : Math.round(fraction * 100)}
        >
          <div className="pdf-progress-fill" style={fraction === null ? undefined : { width: `${fraction * 100}%` }} />
        </div>
        <div className="pdf-progress-detail">
          {!progress
            ? "Connecting…"
            : progress.total
              ? `${bytes(progress.loaded)} of ${bytes(progress.total)}`
              : `${bytes(progress.loaded)}`}
          {progress?.source === "arxiv" && " · first open of this paper, later opens are instant"}
        </div>
      </div>
    </div>
  );
}

function PdfPage(props: { doc: pdfjs.PDFDocumentProxy; pageNumber: number; size: Size; scale: number; render: boolean }) {
  const { doc, pageNumber, size, scale, render } = props;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const width = Math.floor(size.width * scale);
  const height = Math.floor(size.height * scale);

  useEffect(() => {
    if (!render) return;
    let task: ReturnType<pdfjs.PDFPageProxy["render"]> | null = null;
    let text: pdfjs.TextLayer | null = null;
    let cancelled = false;
    void doc.getPage(pageNumber).then((page) => {
      const canvas = canvasRef.current;
      if (cancelled || !canvas) return;
      const dpr = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale });
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      task = page.render({
        canvasContext: canvas.getContext("2d")!,
        viewport,
        transform: dpr === 1 ? undefined : [dpr, 0, 0, dpr, 0, 0],
      });
      task.promise.catch(() => {}); // cancelled renders reject
      // Invisible, selectable text over the canvas, so passages can be copied.
      const container = textRef.current;
      if (container) {
        container.replaceChildren();
        text = new pdfjs.TextLayer({ textContentSource: page.streamTextContent(), container, viewport });
        text.render().catch(() => {});
      }
    });
    return () => {
      cancelled = true;
      task?.cancel();
      text?.cancel();
    };
  }, [doc, pageNumber, scale, render]);

  return (
    <div
      className="pdf-page"
      data-page={pageNumber}
      style={{ width, height, marginBottom: PAGE_GAP, ["--scale-factor" as string]: scale }}
    >
      {render && <canvas ref={canvasRef} style={{ width, height }} />}
      {render && <div ref={textRef} className="textLayer" />}
    </div>
  );
}
