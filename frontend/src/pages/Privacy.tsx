/**
 * The privacy policy. Public: it renders before sign-in, so links to it are
 * plain <a href> (a full page load) rather than router links.
 *
 * Keep it true to the code. If what we collect, where it goes or how long it's
 * kept changes, change this page and UPDATED in the same commit.
 */

const UPDATED = "6 November 2026";
const CONTACT_EMAIL = "privacy@researchmate.org";
const BACKUP_DAYS = 14; // fly.toml snapshot_retention
const LOGIN_DAYS = 30; // Settings.login_days

export function Privacy() {
  const mail = <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>;
  return (
    <div className="admin privacy">
      <header className="admin-header">
        <div>
          <a href="/">← ResearchMate</a>
          <h1>Privacy policy</h1>
          <p className="muted small">Last updated {UPDATED}</p>
        </div>
      </header>

      <section className="privacy-summary">
        <h2>The short version</h2>
        <ul>
          <li>
            We record the papers you save and how you read them. We use this <strong>only</strong> for research into
            how people find and read papers, and to tailor your recommendations.
          </li>
          <li>
            We <strong>never sell</strong> your data, and we <strong>never give or share it</strong> with third parties.
          </li>
          <li>
            You can <strong>download</strong> all of it or <strong>delete</strong> all of it at any time from your{" "}
            <a href="/profile">profile page</a>.
          </li>
        </ul>
      </section>

      <section>
        <h2>Who we are</h2>
        <p>
          ResearchMate (“we”, “us”) is a research project and paper reader at researchmate.org. It's invite only. If
          you have any question about your data, or want us to do something with it, email {mail}.
        </p>
      </section>

      <section>
        <h2>What we collect</h2>
        <h3>Your account</h3>
        <ul>
          <li>Your username.</li>
          <li>
            Your password, if you set one. We only store a scrambled version (a one-way hash), so we can't see or
            recover it.
          </li>
          <li>
            If you sign in with Google or GitHub: the ID that service gives your account, and your email address.
            We never see your Google or GitHub password, and we don't ask for access to anything else in those
            accounts.
          </li>
        </ul>
        <h3>Your library</h3>
        <ul>
          <li>The papers you add, the link you pasted, and where you said you found the paper.</li>
          <li>Your tags, reading statuses and ratings.</li>
          <li>Your answers to the short questions we ask after you read a paper.</li>
        </ul>
        <h3>How you read</h3>
        <ul>
          <li>When you open and close a paper, and whether you read it as a PDF or as HTML.</li>
          <li>
            How long each page or section is on screen while you're active. Time when you're away from the page
            isn't counted.
          </li>
          <li>When you change the text size.</li>
          <li>
            When you copy text from a paper: how many characters you copied and where they start.{" "}
            <strong>Never the text itself.</strong>
          </li>
          <li>Which papers we showed you in lists, and in what order.</li>
          <li>
            Facts about each reading session: device type (phone, tablet or computer), screen and window size,
            your local hour and day of the week, and your time zone.
          </li>
        </ul>
        <h3>What we don't collect</h3>
        <p>
          We don't store your IP address or location with your data. We don't record your keystrokes, your screen
          or the text you copy. We don't use advertising or analytics trackers, and we don't track you on other
          websites.
        </p>
      </section>

      <section>
        <h2>Why we use it</h2>
        <p>We use your data for these purposes and no others:</p>
        <ol>
          <li>
            <strong>Research:</strong> studying how researchers discover, read and judge papers. This includes
            analysing reading data and training models on it.
          </li>
          <li>
            <strong>Your recommendations:</strong> tailoring which papers our preference engine suggests to you.
          </li>
          <li>
            <strong>Running the service:</strong> signing you in, keeping your library, and fixing problems.
          </li>
        </ol>
        <p>
          We don't use your data for advertising, and we don't sell or rent it. If we publish research findings, they
          use only combined or de-identified data that can't reasonably be linked back to you.
        </p>
        <p>
          The people who run ResearchMate can see the data, so they can do this research and look after the site. No
          one else can.
        </p>
      </section>

      <section>
        <h2>Who else is involved</h2>
        <p>
          <strong>We do not sell, rent, trade or give your data to any third party.</strong> Running a website does
          involve a few other companies. Each one only does the job described here, and none of them gets your
          reading data to use for its own purposes:
        </p>
        <ul>
          <li>
            <strong>Fly.io</strong> hosts our servers and database in Sydney, Australia. It stores the data for us,
            on an encrypted disk.
          </li>
          <li>
            <strong>Google and GitHub</strong>, only if you choose to sign in with them. They know that you signed in
            to ResearchMate. They don't receive your reading data.
          </li>
          <li>
            <strong>arXiv</strong>, where the papers come from. Our server fetches papers from arXiv, so arXiv
            doesn't see who asked for them. When you read the HTML version of a paper, though, your browser loads
            its images and styling directly from arxiv.org. arXiv then sees your IP address, as any website you visit
            would.
          </li>
        </ul>
        <p>
          We would only give your data to anyone else if the law requires it. In that case we'll tell you, unless
          the law forbids us to.
        </p>
      </section>

      <section>
        <h2>Your choices and rights</h2>
        <ul>
          <li>
            <strong>See and download your data:</strong> your <a href="/profile">profile page</a> shows everything
            we've recorded about you. “Download my data” gives you all of it in one file (JSON).
          </li>
          <li>
            <strong>Export it with a script:</strong> create an API token on your profile page, then fetch{" "}
            <code>/api/me/export</code> with it. A token can only ever fetch its owner's data, and you can revoke it at
            any time.
          </li>
          <li>
            <strong>Delete your data:</strong> “Delete account” on your profile page removes your account and
            everything recorded about you from our live database straight away. The copies in our daily backups
            are deleted automatically within {BACKUP_DAYS} days. We don't restore deleted accounts from backups.
          </li>
          <li>
            <strong>Correct your data:</strong> you can change your library, tags and ratings in the app. For anything
            else, email {mail}.
          </li>
          <li>
            <strong>Ask or complain:</strong> email {mail} and we'll reply within 30 days. If you're in Australia and
            unhappy with our answer, you can complain to the Office of the Australian Information Commissioner at{" "}
            <a href="https://www.oaic.gov.au" target="_blank" rel="noopener noreferrer">
              oaic.gov.au
            </a>
            .
          </li>
        </ul>
      </section>

      <section>
        <h2>How long we keep it</h2>
        <p>
          We keep your data until you delete your account, so the research and your recommendations can build on your
          reading over time. Backups are kept for {BACKUP_DAYS} days. A sign-in lasts {LOGIN_DAYS} days unless you
          sign out sooner.
        </p>
      </section>

      <section>
        <h2>How we protect it</h2>
        <p>
          The site only works over HTTPS. Passwords and API tokens are stored only as one-way hashes. The database
          is on an encrypted disk. Only the people who run ResearchMate can see other people's data. No system is
          perfectly secure. If a breach is likely to harm you, we'll tell you and the authorities as the law requires.
        </p>
      </section>

      <section>
        <h2>Cookies and browser storage</h2>
        <p>We use no tracking or third-party cookies. We only store these:</p>
        <ul>
          <li>A cookie that keeps you signed in.</li>
          <li>A short-lived cookie while you sign in with Google or GitHub. It's deleted when sign-in finishes.</li>
          <li>
            In your browser's local storage: reading activity not yet sent to us (so it isn't lost if you go
            offline), and whether you last chose the PDF or HTML view.
          </li>
        </ul>
      </section>

      <section>
        <h2>Children</h2>
        <p>ResearchMate isn't meant for anyone under 16, and we don't knowingly collect their data.</p>
      </section>

      <section>
        <h2>Changes to this policy</h2>
        <p>
          If we change this policy, we'll update this page and the date at the top. Before any change takes effect
          that would use your data in a new way, we'll tell you on the site and give you the chance to delete your
          account first. We'll never apply that kind of change to data you've already given us without your consent.
        </p>
      </section>
    </div>
  );
}
