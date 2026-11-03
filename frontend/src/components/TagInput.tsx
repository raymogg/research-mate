import { useId, useState } from "react";

interface Props {
  tags: string[];
  suggestions: string[];
  onAdd: (tag: string) => void;
  onRemove: (tag: string) => void;
  onTagClick?: (tag: string) => void;
}

export function TagInput({ tags, suggestions, onAdd, onRemove, onTagClick }: Props) {
  const [draft, setDraft] = useState("");
  const listId = useId();
  const submit = () => {
    const tag = draft.trim().toLowerCase();
    if (tag && !tags.includes(tag)) onAdd(tag);
    setDraft("");
  };
  return (
    <span className="tags">
      {tags.map((t) => (
        <span key={t} className="tag">
          <button type="button" className="tag-name" onClick={() => onTagClick?.(t)} title={onTagClick ? "Filter by tag" : undefined}>
            {t}
          </button>
          <button type="button" className="tag-remove" aria-label={`Remove ${t}`} onClick={() => onRemove(t)}>
            ×
          </button>
        </span>
      ))}
      <input
        className="tag-input"
        placeholder="+ tag"
        value={draft}
        list={listId}
        maxLength={64}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            submit();
          } else if (e.key === "Escape") setDraft("");
        }}
        onBlur={() => draft.trim() && submit()}
      />
      <datalist id={listId}>
        {suggestions
          .filter((s) => !tags.includes(s))
          .map((s) => (
            <option key={s} value={s} />
          ))}
      </datalist>
    </span>
  );
}
