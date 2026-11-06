import { memo, type MutableRefObject, useEffect, useRef, useState } from "react";
import type { Unit, Units } from "../tracking/dwellTracker";

/** Top-level parts of a LaTeXML paper that reading time is counted against. */
const SECTION_CLASSES = ["ltx_abstract", "ltx_section", "ltx_appendix", "ltx_bibliography", "ltx_acknowledgements",
  "ltx_chapter", "ltx_part", "ltx_index"];
const INPUT_EVENTS = ["mousemove", "mousedown", "wheel", "keydown", "touchstart", "scroll"] as const;

export type HtmlFailure = "no_html" | "html_failed";

interface Props {
  url: string;
  zoom: number; // text size, 1 = 100%
  onLoaded?: (sections: number) => void;
  onSectionInView: (section: string, index: number, of: number) => void;
  onError: (kind: HtmlFailure, message: string) => void;
  onCopy: (section: string | null, chars: number) => void;
  /** Input inside the frame: the window's own listeners can't see it. */
  onActivity: () => void;
  /** Set to the section units of the loaded paper. */
  unitsRef: MutableRefObject<Units | null>;
}

function sectionsOf(doc: Document): HTMLElement[] {
  const article = doc.querySelector<HTMLElement>("article.ltx_document");
  if (!article) return [];
  const parts = Array.from(article.children).filter(
    (el): el is HTMLElement => el instanceof doc.defaultView!.HTMLElement && SECTION_CLASSES.some((c) => el.classList.contains(c)),
  );
  return parts.length ? parts : [article]; // a paper without sections is one unit
}

function sectionId(el: HTMLElement, i: number): string {
  if (el.id) return el.id.slice(0, 64);
  if (el.classList.contains("ltx_abstract")) return "abstract";
  if (el.tagName === "ARTICLE") return "body";
  return `part${i + 1}`;
}

/**
 * arXiv's HTML version of a paper, cut down to the paper by our backend and
 * served with scripts blocked. It is same-origin, so we measure from here which
 * section is on screen; scrolling happens inside the frame.
 */
export const HtmlView = memo(function HtmlView(props: Props) {
  const { url, zoom, unitsRef } = props;
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const callbacks = useRef(props);
  callbacks.current = props;

  // Check first that arXiv has HTML for this paper, so a 404 becomes a fallback
  // to the PDF rather than an error page in the frame. The frame's own request
  // for the same URL then comes from the browser cache.
  useEffect(() => {
    let cancelled = false;
    setSrc(null);
    setLoaded(false);
    fetch(url)
      .then((res) => {
        if (cancelled) return;
        if (res.status === 404) callbacks.current.onError("no_html", "arXiv has no HTML version of this paper");
        else if (!res.ok) callbacks.current.onError("html_failed", `${res.status} ${res.statusText}`);
        else setSrc(url);
      })
      .catch((e) => !cancelled && callbacks.current.onError("html_failed", String(e?.message ?? e)));
    return () => {
      cancelled = true;
    };
  }, [url]);

  useEffect(() => {
    const doc = frameRef.current?.contentDocument;
    if (loaded && doc?.body) doc.body.style.setProperty("zoom", String(zoom));
  }, [zoom, loaded]);

  // Once the document is in: find its sections and follow what's on screen.
  useEffect(() => {
    const frame = frameRef.current;
    const win = frame?.contentWindow;
    const doc = frame?.contentDocument;
    if (!loaded || !win || !doc) return;
    const sections = sectionsOf(doc);
    if (!sections.length) {
      callbacks.current.onError("html_failed", "the HTML has no paper in it");
      return;
    }
    const ids = sections.map(sectionId);
    const byId = new Map(ids.map((id, i) => [id, sections[i]]));

    const visibleRange = (unit: Unit): [number, number] | null => {
      const box = byId.get(String(unit))?.getBoundingClientRect();
      if (!box?.height) return null;
      const top = Math.max(0, -box.top / box.height);
      const bottom = Math.min(1, (win.innerHeight - box.top) / box.height);
      return bottom > top ? [top, bottom] : null;
    };
    unitsRef.current = {
      dwellType: "section_dwell",
      describe: (unit) => {
        const id = String(unit);
        const heights = sections.map((el) => el.getBoundingClientRect().height);
        const total = heights.reduce((a, b) => a + b, 0) || 1;
        const i = Math.max(0, ids.indexOf(id));
        return { section: id, index: i + 1, of: ids.length, share: Math.round((heights[i] / total) * 10_000) / 10_000 };
      },
      visibleRange,
    };
    callbacks.current.onLoaded?.(ids.length);

    let frameReq = 0;
    const findInView = () => {
      frameReq = 0;
      let best = -1;
      let bestPx = 0;
      sections.forEach((el, i) => {
        const box = el.getBoundingClientRect();
        const px = Math.min(box.bottom, win.innerHeight) - Math.max(box.top, 0);
        if (px > bestPx) [best, bestPx] = [i, px];
      });
      if (best >= 0) callbacks.current.onSectionInView(ids[best], best + 1, ids.length);
    };
    const onScroll = () => {
      if (!frameReq) frameReq = win.requestAnimationFrame(findInView);
    };
    const onInput = () => callbacks.current.onActivity();
    const onCopy = () => {
      const sel = doc.getSelection();
      if (!sel?.anchorNode) return;
      const anchor = sel.anchorNode instanceof doc.defaultView!.Element ? sel.anchorNode : sel.anchorNode.parentElement;
      const i = sections.findIndex((el) => el.contains(anchor));
      callbacks.current.onCopy(i >= 0 ? ids[i] : null, sel.toString().length);
    };

    findInView();
    win.addEventListener("scroll", onScroll, { passive: true });
    win.addEventListener("resize", onScroll);
    INPUT_EVENTS.forEach((t) => win.addEventListener(t, onInput, { passive: true, capture: true }));
    doc.addEventListener("copy", onCopy);
    return () => {
      unitsRef.current = null;
      if (frameReq) win.cancelAnimationFrame(frameReq);
      win.removeEventListener("scroll", onScroll);
      win.removeEventListener("resize", onScroll);
      INPUT_EVENTS.forEach((t) => win.removeEventListener(t, onInput, { capture: true }));
      doc.removeEventListener("copy", onCopy);
    };
  }, [loaded, unitsRef]);

  return (
    <div className="html-view">
      {!loaded && <div className="pdf-loading">Loading the HTML version…</div>}
      {src && (
        <iframe
          ref={frameRef}
          className={loaded ? "html-frame" : "html-frame loading"}
          src={src}
          title="Paper (HTML)"
          onLoad={() => setLoaded(true)}
        />
      )}
    </div>
  );
});
