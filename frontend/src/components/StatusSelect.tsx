import { STATUSES, type Status } from "../api";

export const STATUS_LABELS: Record<Status, string> = {
  unread: "Unread",
  reading: "Reading",
  read: "Read",
  skimmed: "Skimmed",
  abandoned: "Abandoned",
};

export function StatusSelect({ value, onChange }: { value: Status; onChange: (s: Status) => void }) {
  return (
    <select
      className={`status status-${value}`}
      value={value}
      aria-label="Status"
      onChange={(e) => onChange(e.target.value as Status)}
    >
      {STATUSES.map((s) => (
        <option key={s} value={s}>
          {STATUS_LABELS[s]}
        </option>
      ))}
    </select>
  );
}
