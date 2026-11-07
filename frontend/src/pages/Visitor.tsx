import { Link } from "react-router-dom";
import { useApp } from "../App";
import { SignInButtons } from "./Login";

/** The home page for anyone not signed in: the starter papers, readable straight away. */
export function VisitorHome() {
  const { config, starter } = useApp();

  return (
    <div className="home">
      <nav className="home-nav">
        <span className="brand">ResearchMate</span>
        <a className="home-nav-link" href="#sign-in">
          Sign in
        </a>
      </nav>

      <header className="home-hero">
        <h1>A paper reader that learns what you like</h1>
      </header>

      <section className="home-papers" aria-labelledby="home-papers-title">
        <h2 id="home-papers-title">Papers you might enjoy</h2>
        <p className="muted">Five papers behind today's large language models.</p>
        {starter ? (
          <ol className="home-cards">
            {starter.papers.map((p, i) => (
              <li key={p.arxiv_id}>
                <Link to={`/paper/${p.key}`} className="home-card">
                  <span className="home-card-n" aria-hidden="true">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="home-card-body">
                    <span className="home-card-title">{p.title}</span>
                    <span className="home-card-meta">
                      {p.authors} · {p.year}
                    </span>
                    <span className="home-card-why">{p.why}</span>
                  </span>
                  <span className="home-card-arrow" aria-hidden="true">
                    →
                  </span>
                </Link>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </section>

      <section className="home-sign-in" id="sign-in" aria-labelledby="home-sign-in-title">
        <h2 id="home-sign-in-title">Keep a library</h2>
        <p className="muted">
          Sign in to add any arXiv paper, keep your place, track your reading, and get recommendations tailored to
          what you read.
        </p>
        <SignInButtons providers={config.providers} />
      </section>
    </div>
  );
}
