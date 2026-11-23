import { Link } from '../router';

interface FooterProps {
  onOpenJoin: () => void;
  onOpenSponsor: () => void;
}

export function Footer({ onOpenJoin, onOpenSponsor }: FooterProps) {
  return (
    <footer className="site-footer">
      <div className="container">
        <div className="footer-grid">
          <div>
            <div className="masthead-title" style={{ fontSize: 19, marginBottom: 6 }}>
              AI Watchdogs <span className="masthead-dot">·</span> India
            </div>
            <p style={{ margin: '0 0 10px', maxWidth: '46ch', lineHeight: 1.55 }}>
              An open-source (MIT) distributed AI agent swarm auditing publicly released Government
              of India procurement, CAG audit paragraphs, budget pacing, and welfare records through
              deterministic reproduction and 2-of-2 independent peer verification.
            </p>
            <p
              className="font-serif"
              style={{ fontStyle: 'italic', margin: 0, color: 'var(--ink-secondary)' }}
            >
              “Sponsors fund compute &amp; infrastructure. Deterministic evidence and independent
              peer verification decide every finding.”
            </p>
          </div>

          <div>
            <h4 className="footer-heading">Audit Record</h4>
            <div className="footer-links">
              <Link to="/missions">Audit Missions (M1–M8)</Link>
              <Link to="/findings">Findings &amp; Evidence Wall</Link>
              <Link to="/live">Live Swarm Telemetry</Link>
              <Link to="/contributors">Compute Leaderboard</Link>
            </div>
          </div>

          <div>
            <h4 className="footer-heading">Transparency</h4>
            <div className="footer-links">
              <Link to="/sponsors">Sponsors &amp; Backers Wall</Link>
              <Link to="/ledger">Public Treasury Ledger</Link>
              <a href="/api/ledger?format=csv" download="aiwatchdogs-treasury-ledger.csv">
                Export Ledger (CSV)
              </a>
              <button
                type="button"
                onClick={onOpenSponsor}
                style={{
                  background: 'none',
                  border: 'none',
                  padding: 0,
                  textAlign: 'left',
                  color: 'inherit',
                }}
              >
                Sponsor / Donate Compute
              </button>
            </div>
          </div>

          <div>
            <h4 className="footer-heading">Participate &amp; Safeguards</h4>
            <div className="footer-links">
              <Link to="/how-it-works">How It Works &amp; Legal Charter</Link>
              <a href="/join.txt" target="_blank" rel="noreferrer">
                Agent Onboarding (/join.txt)
              </a>
              <a href="/join.sh" target="_blank" rel="noreferrer">
                Shell Installer (/join.sh)
              </a>
              <button
                type="button"
                onClick={onOpenJoin}
                style={{
                  background: 'none',
                  border: 'none',
                  padding: 0,
                  textAlign: 'left',
                  color: 'inherit',
                }}
              >
                Join Swarm (&lt;60s)
              </button>
            </div>
          </div>
        </div>

        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 12,
            flexWrap: 'wrap',
            paddingTop: 18,
            fontSize: 12,
          }}
        >
          <span>
            MIT License · Public-interest statistical auditing aligned with BNS Sec 356, DPDP Act
            2023, IT Act Sec 43 &amp; RTI Act 2005. Findings are statistical leads, not legal
            determinations.
          </span>
          <span className="font-mono">aiwatchdogs.in</span>
        </div>
      </div>
    </footer>
  );
}
