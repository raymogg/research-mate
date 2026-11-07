import { useTheme } from "../theme";

export function ThemeToggle() {
  const [theme, toggle] = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button className="theme-toggle" onClick={toggle} aria-label={`Switch to ${next} theme`} title={`Switch to ${next} theme`}>
      {theme === "dark" ? (
        // sun
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        // moon
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
      )}
    </button>
  );
}

export function SiteFooter() {
  return (
    <footer className="site-footer small">
      <span>
        Created by{" "}
        <a href="https://raymogg.com" target="_blank" rel="noreferrer">
          raymogg
        </a>
      </span>
      <span aria-hidden="true">·</span>
      <a href="/privacy">Privacy</a>
      <ThemeToggle />
    </footer>
  );
}
