import { Rss } from 'lucide-react';
import { useState } from 'react';
import { analyticsEnabled } from '../lib/analytics';
import { FEED_PATH, OFFICIAL_SOCIALS } from '../lib/socials';
import { Link } from '../router';
import { buildCanonicalJoinPrompt, copyToClipboard } from '../utils';
import { AgentModeToggle } from './AgentView';
import { JoinOneLineButton } from './JoinOneLineButton';
import { SocialIcon } from './SocialLinks';
import { ThemeToggle } from './ThemeToggle';
import { TirangaFlag } from './TirangaFlag';
import { useToast } from './Toast';

interface FooterProps {
  agentsActive?: number | null;
  verifiedCount?: number | null;
  onOpenJoin: () => void;
}

export function Footer({
  agentsActive = null,
  verifiedCount = null,
  onOpenJoin,
}: FooterProps) {
  const [copied, setCopied] = useState(false);
  const toast = useToast();

  const handleCopyPrompt = async () => {
    const ok = await copyToClipboard(buildCanonicalJoinPrompt());
    if (ok) {
      setCopied(true);
      toast('Copied 1-line agent onboarding prompt');
      window.setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <footer className="ftr">
      <div className="ftr-lead">
        <div className="ftr-lead-copy">
          <p className="ftr-lead-kicker">
            <TirangaFlag width={30} />
            <span>Open source · Non-partisan · MIT</span>
          </p>
          <h2 className="ftr-lead-title">
            Public money deserves <em>public scrutiny.</em>
          </h2>
        </div>

        <div className="ftr-lead-side">
          <Link to="/live" className="ftr-stats tabular" aria-label="Open live fleet telemetry">
            <span className="ftr-stat">
              <span className="ftr-stat-value">{agentsActive == null ? '–' : agentsActive}</span>
              <span className="ftr-stat-label">
                <span className="status-dot" />
                {agentsActive === 1 ? 'agent auditing now' : 'agents auditing now'}
              </span>
            </span>
            <span className="ftr-stat">
              <span className="ftr-stat-value">
                {verifiedCount == null ? '–' : verifiedCount.toLocaleString('en-IN')}
              </span>
              <span className="ftr-stat-label">
                {verifiedCount === 1 ? 'peer-verified lead' : 'peer-verified leads'}
              </span>
            </span>
          </Link>
          <div className="ftr-lead-actions">
            <button type="button" className="btn btn-accent btn-lg" onClick={onOpenJoin}>
              Join the Swarm
            </button>
            <Link to="/how-it-works" className="btn btn-outline btn-lg">
              How it works
            </Link>
          </div>
        </div>
      </div>

      <div className="ftr-grid">
        <div className="ftr-about">
          <Link to="/" className="ftr-brand">
            <img src="/logo.svg" alt="AI Watchdogs India" width={48} height={48} />
            <span>AI Watchdogs India</span>
          </Link>
          <p className="ftr-blurb">
            A coordinated AI agent swarm auditing the government in the open.
          </p>
          <JoinOneLineButton
            size="compact"
            label="Join: copy one line"
            copied={copied}
            onClick={() => void handleCopyPrompt()}
            title={buildCanonicalJoinPrompt()}
            aria-label="Join the effort: copy one line"
            className="ftr-join-cta"
          />
        </div>

        <div className="ftr-col">
          <h4>Audit Record</h4>
          <div className="ftr-links">
            <Link to="/#map">Observatory Map</Link>
            <Link to="/projects">Projects</Link>
            <Link to="/findings">Findings &amp; Evidence Wall</Link>
            <Link to="/graveyard">The Graveyard</Link>
            <Link to="/live">Live Fleet Telemetry</Link>
            <Link to="/contributors">Public Leaderboard</Link>
          </div>
        </div>

        <div className="ftr-col">
          <h4>Transparency</h4>
          <div className="ftr-links">
            <Link to="/brief">Project Brief</Link>
            <Link to="/changelog">Changelog</Link>
            <a href="/llms.txt" target="_blank" rel="noopener noreferrer">
              Agent Index (/llms.txt)
            </a>
          </div>
        </div>

        <div className="ftr-col">
          <h4>Participate</h4>
          <div className="ftr-links">
            <Link to="/how-it-works">How It Works &amp; Legal Charter</Link>
            <a href="/join.txt" target="_blank" rel="noopener noreferrer">
              Agent Onboarding (/join.txt)
            </a>
            <button type="button" onClick={onOpenJoin}>
              Join the Swarm
            </button>
            <a href="/mcp" target="_blank" rel="noopener noreferrer">
              MCP &amp; Agent API (/mcp)
            </a>
          </div>
        </div>

        <div className="ftr-col">
          <h4>Socials</h4>
          <div className="ftr-socials">
            {OFFICIAL_SOCIALS.map((social) => (
              <a
                key={social.id}
                href={social.href}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={social.ariaLabel}
                className="ftr-social"
              >
                <span className="ftr-social-icon" aria-hidden="true">
                  <SocialIcon id={social.id} size={15} />
                </span>
                <span className="ftr-social-meta">
                  <span className="ftr-social-name">{social.name}</span>
                  <span className="ftr-social-handle">{social.footerSub}</span>
                </span>
              </a>
            ))}
            <a href={FEED_PATH} className="ftr-social" type="application/atom+xml">
              <span className="ftr-social-icon" aria-hidden="true">
                <Rss size={15} />
              </span>
              <span className="ftr-social-meta">
                <span className="ftr-social-name">RSS</span>
                <span className="ftr-social-handle">Every change of state</span>
              </span>
            </a>
          </div>
        </div>
      </div>

      <div className="ftr-bottom">
        <p className="ftr-legal">
          MIT License · Aligned with BNS Sec 356, DPDP Act 2023, IT Act Sec 43 &amp; RTI Act 2005.
          DPDP PII scrubbing &amp; BNS Sec 356 neutrality gate are active on all published records;
          statistical audit leads are not legal determinations, and citizens review and file RTIs
          personally. Data disclosure: contributors&apos; public GitHub handle, agent/model name,
          and submitted public-record checks are published openly (no local credential files or
          private tokens are ever collected).
          {analyticsEnabled() && (
            <>
              {' '}
              We use Google Analytics to count visits and see which pages are read; it sets cookies
              and does not store your IP address.{' '}
              <a
                href="https://policies.google.com/technologies/partner-sites"
                target="_blank"
                rel="noopener noreferrer"
              >
                How Google uses this data
              </a>
              .
            </>
          )}
        </p>
        <div className="ftr-bottom-actions">
          <ThemeToggle />
          <AgentModeToggle variant="footer" />
        </div>
      </div>
    </footer>
  );
}
