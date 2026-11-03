import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { type Answers, api, LEVELS, type Paper, REASONS } from "../api";
import { ago, authorLine } from "../format";

type Context = Answers["context"];
type Level = (typeof LEVELS)[number];
type Reason = (typeof REASONS)[number];

const LEVEL_LABELS: Record<Level, string> = {
  too_basic: "Too basic",
  just_right: "Just right",
  too_advanced: "Too advanced",
};
const REASON_LABELS: Record<Reason, string> = {
  off_topic: "Off-topic for me",
  poorly_written: "Poorly written",
  not_now: "Not now",
  great_method: "Great method",
  great_idea: "Great idea",
};

const readerKey = (path: string | null) => path?.match(/^\/paper\/([^/]+)/)?.[1] ?? null;

/**
 * Asks once about each paper after its first visit: on leaving the reader, or,
 * if the tab was closed instead, on the next page load. Mounted inside the router.
 */
export function PaperQuestions() {
  const { pathname } = useLocation();
  const previous = useRef<string | null>(null);
  const [queue, setQueue] = useState<{ paper: Paper; context: Context }[]>([]);

  useEffect(() => {
    const left = readerKey(previous.current);
    const here = readerKey(pathname);
    previous.current = pathname;
    let cancelled = false;
    api
      .pendingFeedback()
      .then((papers) => {
        if (cancelled) return;
        const asking = papers.filter((p) => p.key !== here); // still reading it
        asking.sort((a, b) => Number(b.key === left) - Number(a.key === left)); // the one just left first
        setQueue(asking.map((paper) => ({ paper, context: paper.key === left ? "after_reading" : "next_visit" })));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  const current = queue[0];
  if (!current) return null;
  return (
    <QuestionsDialog
      key={current.paper.key}
      paper={current.paper}
      context={current.context}
      remaining={queue.length - 1}
      onDone={() => setQueue((q) => q.slice(1))}
    />
  );
}

function QuestionsDialog(props: { paper: Paper; context: Context; remaining: number; onDone: () => void }) {
  const { paper, context, remaining, onDone } = props;
  const dialog = useRef<HTMLDialogElement>(null);
  const shownAt = useRef(Date.now());
  const [interest, setInterest] = useState<number | null>(null);
  const [value, setValue] = useState<number | null>(null);
  const [novelty, setNovelty] = useState<number | null>(null);
  const [level, setLevel] = useState<Level | null>(null);
  const [reasons, setReasons] = useState<Reason[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    dialog.current?.showModal();
    shownAt.current = Date.now();
  }, []);

  const answered = interest !== null || value !== null || novelty !== null || level !== null || reasons.length > 0;

  const send = (extra: Pick<Answers, "skipped"> | Omit<Answers, "context" | "shown_ms">) => {
    setSending(true);
    api
      .answer(paper.key, { ...extra, context, shown_ms: Date.now() - shownAt.current })
      .then(onDone)
      .catch((e) => {
        if (e.status === 409) onDone(); // answered in another tab
        else {
          setError(e.message);
          setSending(false);
        }
      });
  };
  const save = () => send({ interest, value, novelty, level, reasons });
  const skip = (why: "skip" | "didnt_read") => send({ skipped: why });

  return (
    <dialog
      ref={dialog}
      className="questions"
      aria-labelledby="questions-title"
      onCancel={(e) => {
        e.preventDefault(); // Escape skips, through the server, rather than just hiding
        if (!sending) skip("skip");
      }}
    >
      <form
        method="dialog"
        onSubmit={(e) => {
          e.preventDefault();
          if (answered && !sending) save();
        }}
      >
        <header>
          <h2 id="questions-title">
            {context === "after_reading" ? "Before you go: how was this paper?" : "How was this paper?"}
          </h2>
          <p className="questions-paper">
            <strong>{paper.title}</strong>
            <span className="muted small">
              {" "}
              {authorLine(paper.authors)}
              {context === "next_visit" && ` · opened ${ago(paper.last_opened_at)}`}
            </span>
          </p>
          <p className="muted small">Answer any you like. We only ask once per paper.</p>
        </header>

        <Scale label="How interesting was it?" value={interest} onChange={setInterest} />
        <Scale label="How valuable was it to you?" value={value} onChange={setValue} />
        <Scale label="How novel was it?" value={novelty} onChange={setNovelty} low="Nothing new" high="Very new" />

        <fieldset className="question">
          <legend>How did it match your level?</legend>
          <div className="segmented">
            {LEVELS.map((l) => (
              <button
                key={l}
                type="button"
                aria-pressed={level === l}
                onClick={() => setLevel(level === l ? null : l)}
              >
                {LEVEL_LABELS[l]}
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className="question">
          <legend>Anything else? (pick any)</legend>
          <div className="chips">
            {REASONS.map((r) => (
              <button
                key={r}
                type="button"
                className="chip"
                aria-pressed={reasons.includes(r)}
                onClick={() => setReasons((rs) => (rs.includes(r) ? rs.filter((x) => x !== r) : [...rs, r]))}
              >
                {REASON_LABELS[r]}
              </button>
            ))}
          </div>
        </fieldset>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <footer>
          <button type="button" className="link" disabled={sending} onClick={() => skip("didnt_read")}>
            I didn't really read it
          </button>
          <span className="questions-actions">
            {remaining > 0 && <span className="muted small">{remaining} more</span>}
            <button type="button" className="secondary" disabled={sending} onClick={() => skip("skip")}>
              Skip
            </button>
            <button type="submit" className="primary" disabled={!answered || sending}>
              Save
            </button>
          </span>
        </footer>
      </form>
    </dialog>
  );
}

function Scale(props: { label: string; value: number | null; onChange: (v: number | null) => void; low?: string; high?: string }) {
  const { label, value, onChange, low = "Not at all", high = "Very" } = props;
  return (
    <fieldset className="question">
      <legend>{label}</legend>
      <div className="scale">
        <span className="muted small">{low}</span>
        <div className="segmented" role="radiogroup" aria-label={label}>
          {[1, 2, 3, 4, 5].map((n) => (
            <button
              key={n}
              type="button"
              role="radio"
              aria-checked={value === n}
              onClick={() => onChange(value === n ? null : n)}
            >
              {n}
            </button>
          ))}
        </div>
        <span className="muted small">{high}</span>
      </div>
    </fieldset>
  );
}
