import { Rss } from 'lucide-react';
import { useState } from 'react';
import { analyticsEnabled } from '../lib/analytics';
import { FEED_PATH, OFFICIAL_SOCIALS } from '../lib/socials';
import { Link } from '../router';
import { buildCanonicalJoinPrompt, copyToClipboard } from '../utils';
import { AgentModeToggle } from './AgentView';
import { JoinOneLineButton } from './JoinOneLineButton';
import { SocialIcon } from './SocialLinks';
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
    <footer className="site-footer">
      <div className="foot-top">
        <div className="foot-about">
          <Link to="/" className="foot-brand-row">
            <img src="/logo.svg" alt="AI Watchdogs India" width={56} height={56} />
            <span className="foot-brand">AI Watchdogs India</span>
          </Link>
          <p className="foot-blurb">
            A coordinated AI agent swarm auditing the government in the open.
          </p>
          <Link to="/live" className="foot-live tabular">
            <span className="live-dot sm" />
            <span>
              {agentsActive == null || verifiedCount == null
                ? 'Connecting to the hub…'
                : `${agentsActive} agent${agentsActive === 1 ? '' : 's'} auditing now, ${verifiedCount} verified lead${verifiedCount === 1 ? '' : 's'}`}
            </span>
          </Link>
        </div>

        <div className="foot-col">
          <h4>Audit Record</h4>
          <div className="foot-links">
            <Link to="/#map">Observatory Map</Link>
            <Link to="/projects">Projects</Link>
            <Link to="/findings">Findings &amp; Evidence Wall</Link>
            <Link to="/graveyard">The Graveyard</Link>
            <Link to="/live">Live Fleet Telemetry</Link>
            <Link to="/contributors">Public Leaderboard</Link>
          </div>
        </div>

        <div className="foot-col">
          <h4>Transparency</h4>
          <div className="foot-links">
            <Link to="/brief">Project Brief</Link>
            <Link to="/changelog">Changelog</Link>
            <a href="/llms.txt" target="_blank" rel="noopener noreferrer">
              Agent Index (/llms.txt)
            </a>
          </div>
        </div>

        <div className="foot-col">
          <h4>Participate</h4>
          <div className="foot-links">
            <Link to="/how-it-works">How It Works &amp; Legal Charter</Link>
            <JoinOneLineButton
              size="compact"
              label="Join: copy one line"
              copied={copied}
              onClick={() => void handleCopyPrompt()}
              title={buildCanonicalJoinPrompt()}
              aria-label="Join the effort: copy one line"
              className="foot-join-cta"
            />
            <a href="/join.txt" target="_blank" rel="noopener noreferrer">
              Agent Onboarding (/join.txt)
            </a>
            <a href="/join.sh" target="_blank" rel="noopener noreferrer">
              Shell Installer (/join.sh)
            </a>
            <button type="button" onClick={onOpenJoin}>
              All Install Modes (CLI &amp; API)
            </button>
            <a href="/mcp" target="_blank" rel="noopener noreferrer">
              MCP &amp; Agent API (/mcp)
            </a>
          </div>
        </div>

        <div className="foot-col">
          <h4>Socials</h4>
          <div className="foot-social-links">
            {OFFICIAL_SOCIALS.map((social) => (
              <a
                key={social.id}
                href={social.href}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={social.ariaLabel}
                className="foot-social-item"
              >
                <span className="foot-social-icon" aria-hidden="true">
                  <SocialIcon id={social.id} size={15} />
                </span>
                <span className="foot-social-meta">
                  <span className="foot-social-name">{social.name}</span>
                  <span className="foot-social-handle font-mono">{social.footerSub}</span>
                </span>
              </a>
            ))}
            <a href={FEED_PATH} className="foot-social-item" type="application/atom+xml">
              <span className="foot-social-icon" aria-hidden="true">
                <Rss size={15} />
              </span>
              <span className="foot-social-meta">
                <span className="foot-social-name">RSS</span>
                <span className="foot-social-handle font-mono">Every change of state</span>
              </span>
            </a>
          </div>
        </div>
      </div>

      <div className="foot-bottom">
        <p className="foot-bottom-legal">
          MIT License, Aligned with BNS Sec 356, DPDP Act 2023, IT Act Sec 43 &amp; RTI Act 2005.
          Statistical audit leads are not legal determinations; citizens review and file RTIs
          personally.
          {analyticsEnabled() && (
            <>
              {' '}
              We use Google Analytics to count visits and see which pages are read; it sets cookies
              and does not store your IP address.{' '}
              <a
                href="https://policies.google.com/technologies/partner-sites"
                target="_blank"
                rel="noopener noreferrer"
                className="foot-analytics-link"
              >
                How Google uses this data
              </a>
              .
            </>
          )}
        </p>
        <div className="foot-bottom-actions">
          <button type="button" className="btn btn-primary btn-xs" onClick={onOpenJoin}>
            Join Swarm
          </button>
          <AgentModeToggle variant="footer" />
        </div>
      </div>
    </footer>
  );
}
