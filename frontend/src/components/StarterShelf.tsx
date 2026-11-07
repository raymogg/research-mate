import { type ReactNode, type Ref } from "react";
import { Link } from "react-router-dom";
import type { StarterPaper } from "../api";

/**
 * The starter papers in reading order. Each row carries `data-arxiv-id`, so an
 * ImpressionTracker can watch the list. Without `onOpen`, titles link straight
 * to the reader (visitors).
 */
export function StarterShelf({
  papers,
  onOpen,
  actions,
  listRef,
}: {
  papers: StarterPaper[];
  onOpen?: (p: StarterPaper, position: number) => void;
  actions?: (p: StarterPaper, position: number) => ReactNode;
  listRef?: Ref<HTMLOListElement>;
}) {
  return (
    <ol className="starter" ref={listRef}>
      {papers.map((p, i) => (
        <li key={p.arxiv_id} data-arxiv-id={p.arxiv_id}>
          <span className="starter-n" aria-hidden="true">
            {i + 1}
          </span>
          <div className="starter-body">
            {onOpen ? (
              <button className="link paper-title" onClick={() => onOpen(p, i + 1)}>
                {p.title}
              </button>
            ) : (
              <Link to={`/paper/${p.key}`} className="paper-title">
                {p.title}
              </Link>
            )}
            <div className="muted small">
              {p.authors} · {p.year} · arXiv:{p.arxiv_id}
            </div>
            <p className="small">{p.why}</p>
          </div>
          {actions && <div className="starter-actions">{actions(p, i + 1)}</div>}
        </li>
      ))}
    </ol>
  );
}
