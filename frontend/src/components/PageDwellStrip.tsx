import { useState } from "react";
import { duration } from "../format";

const HEIGHT = 32;
const MAX_WIDTH = 280;
const GAP = 2;

interface Props {
  pageMs: Record<string, number>;
  numPages: number | null;
  viewedMinMs: number;
}

/**
 * Active time per page as a bar strip, scaled to this paper's busiest page.
 * Pages over the "viewed" threshold are solid; time under it is faint; unread pages are a baseline tick.
 */
export function PageDwellStrip({ pageMs, numPages, viewedMinMs }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const maxPage = Math.max(numPages ?? 0, ...Object.keys(pageMs).map(Number));
  if (!maxPage) return <span className="muted small">no reading yet</span>;

  const barW = Math.max(2, Math.min(10, Math.floor((MAX_WIDTH - GAP * (maxPage - 1)) / maxPage)));
  const width = maxPage * barW + (maxPage - 1) * GAP;
  const peak = Math.max(1, ...Object.values(pageMs));
  const r = barW >= 4 ? 2 : 0;
  const hoveredMs = hover ? pageMs[hover] ?? 0 : 0;

  return (
    <span className="strip">
      <svg width={width} height={HEIGHT} role="img" aria-label={`Active time across ${maxPage} pages`}>
        {Array.from({ length: maxPage }, (_, i) => {
          const page = i + 1;
          const ms = pageMs[page] ?? 0;
          const x = i * (barW + GAP);
          const h = ms ? Math.max(2, (ms / peak) * (HEIGHT - 2)) : 0;
          const y = HEIGHT - h;
          // Rounded top, square base anchored to the baseline.
          const d = `M${x} ${HEIGHT}V${y + r}q0 -${r} ${r} -${r}h${barW - 2 * r}q${r} 0 ${r} ${r}V${HEIGHT}z`;
          return (
            <g key={page} onMouseEnter={() => setHover(page)} onMouseLeave={() => setHover(null)}>
              {ms ? (
                <path d={d} className={ms >= viewedMinMs ? "bar viewed" : "bar under"} />
              ) : (
                <rect x={x} y={HEIGHT - 1} width={barW} height={1} className="bar empty" />
              )}
              {hover === page && <rect x={x - 1} y={0} width={barW + 2} height={HEIGHT} className="bar-hover" />}
              <rect x={x - GAP / 2} y={0} width={barW + GAP} height={HEIGHT} fill="transparent" />
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <span className="strip-tip" style={{ left: Math.min(hover * (barW + GAP), width - 60) }}>
          <strong>Page {hover}</strong> {hoveredMs ? duration(hoveredMs) : "not read"}
          {hoveredMs > 0 && hoveredMs < viewedMinMs && <span className="muted"> · under threshold</span>}
        </span>
      )}
    </span>
  );
}
