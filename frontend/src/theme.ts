import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark";

// Keep in sync with the inline script in index.html, which applies the theme before first paint.
const STORAGE_KEY = "researchmate:theme";
const media = window.matchMedia("(prefers-color-scheme: dark)");
const listeners = new Set<() => void>();

function saved(): Theme | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

/** The theme in effect: the one picked with the toggle (on <html>), else the OS setting. */
function current(): Theme {
  const picked = document.documentElement.dataset.theme;
  if (picked === "light" || picked === "dark") return picked;
  return media.matches ? "dark" : "light";
}

function apply() {
  const picked = saved();
  if (picked) document.documentElement.dataset.theme = picked;
  else delete document.documentElement.dataset.theme;
  listeners.forEach((fn) => fn());
}

media.addEventListener("change", apply);
window.addEventListener("storage", (e) => e.key === STORAGE_KEY && apply()); // other tabs

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function setTheme(theme: Theme) {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // storage blocked: still switch for this page
    document.documentElement.dataset.theme = theme;
    listeners.forEach((fn) => fn());
    return;
  }
  apply();
}

export function useTheme(): [Theme, () => void] {
  const theme = useSyncExternalStore(subscribe, current);
  return [theme, () => setTheme(theme === "dark" ? "light" : "dark")];
}
