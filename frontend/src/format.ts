export function duration(ms: number): string {
  if (ms < 1000) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

export function ago(iso: string | null): string {
  if (!iso) return "never";
  const mins = (Date.now() - new Date(iso).getTime()) / 60_000;
  if (mins < 1) return "just now";
  if (mins < 60) return `${Math.floor(mins)}m ago`;
  if (mins < 60 * 24) return `${Math.floor(mins / 60)}h ago`;
  if (mins < 60 * 24 * 30) return `${Math.floor(mins / 60 / 24)}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function authorLine(authors: string[]): string {
  if (!authors.length) return "";
  return authors.length > 1 ? `${authors[0]} et al.` : authors[0];
}

export function bytes(n: number): string {
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n < 10 && i ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

export function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" }) : "—";
}
