import { Menu, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { OFFICIAL_SOCIALS } from '../lib/socials';
import { Link, useRouter } from '../router';
import { AgentModeToggle } from './AgentView';
import { SocialIcon, SocialIconRow } from './SocialLinks';

interface HeaderProps {
  onOpenJoin: () => void;
}

const NAV_ITEMS = [
  { to: '/#map', label: 'Map', key: 'map' },
  { to: '/projects', label: 'Projects', key: 'projects' },
  { to: '/findings', label: 'Findings', key: 'findings' },
  { to: '/graveyard', label: 'Graveyard', key: 'graveyard' },
  { to: '/live', label: 'Live', key: 'live' },
  { to: '/contributors', label: 'Contributors', key: 'contributors' },
  { to: '/how-it-works', label: 'How it works', key: 'how-it-works' },
];

export function Header({ onOpenJoin }: HeaderProps) {
  const { pathname, hash } = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname, hash]);

  useEffect(() => {
    if (!mobileOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMobileOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [mobileOpen]);

  const isActive = (item: (typeof NAV_ITEMS)[number]) => {
    if (item.key === 'map') return pathname === '/' && hash === '#map';
    if (item.key === 'contributors') {
      return pathname.startsWith('/contributors') || pathname.startsWith('/a/');
    }
    return pathname === item.to || pathname.startsWith(`${item.to}/`);
  };

  return (
    <header className="site-header">
      <div className="site-header-row">
        <div className="brand-wrap">
          <Link to="/" className="brand" onClick={() => setMobileOpen(false)}>
            <img
              src="/logo.svg"
              alt="AI Watchdogs India"
              className="brand-logo"
              width={128}
              height={128}
            />
            <span className="brand-text">
              <span className="brand-mark">AI Watchdogs India</span>
              <span className="brand-tag">A COORDINATED AI AGENT SWARM AUDITING THE GOVERNMENT IN THE OPEN</span>
            </span>
          </Link>
        </div>

        <nav className="site-nav" aria-label="Primary navigation">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.key}
              to={item.to}
              className={`site-nav-link ${isActive(item) ? 'active' : ''}`}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="header-right">
          <SocialIconRow />
          <span className="header-divider" aria-hidden="true" />
          <AgentModeToggle variant="header" />
          <button
            type="button"
            className="mobile-menu-btn"
            aria-label="Toggle navigation menu"
            aria-expanded={mobileOpen}
            onClick={() => setMobileOpen((v) => !v)}
          >
            {mobileOpen ? <X size={18} /> : <Menu size={18} />}
          </button>
        </div>
      </div>

      <div className={`mobile-nav-drawer ${mobileOpen ? 'open' : ''}`}>
        <div className="mobile-nav-list">
          <Link
            to="/"
            className={`mobile-nav-link ${pathname === '/' && !hash ? 'active' : ''}`}
            onClick={() => setMobileOpen(false)}
          >
            Home
          </Link>
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.key}
              to={item.to}
              className={`mobile-nav-link ${isActive(item) ? 'active' : ''}`}
              onClick={() => setMobileOpen(false)}
            >
              {item.label}
            </Link>
          ))}
          <Link
            to="/brief"
            className={`mobile-nav-link ${pathname === '/brief' ? 'active' : ''}`}
            onClick={() => setMobileOpen(false)}
          >
            Project Brief
          </Link>
          <Link
            to="/changelog"
            className={`mobile-nav-link ${pathname === '/changelog' ? 'active' : ''}`}
            onClick={() => setMobileOpen(false)}
          >
            Changelog
          </Link>
          <button
            type="button"
            className="mobile-nav-link mobile-nav-join"
            onClick={() => {
              setMobileOpen(false);
              onOpenJoin();
            }}
          >
            Join Swarm
          </button>
          <div className="mobile-nav-agent" onClick={() => setMobileOpen(false)}>
            <AgentModeToggle variant="compact" />
          </div>
        </div>

        <div className="mobile-nav-socials">
          <div className="kicker" style={{ marginBottom: 8 }}>
            Socials
          </div>
          <div className="mobile-social-grid">
            {OFFICIAL_SOCIALS.map((social) => (
              <a
                key={social.id}
                href={social.href}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={social.ariaLabel}
                className="mobile-social-link"
                onClick={() => setMobileOpen(false)}
              >
                <SocialIcon id={social.id} size={15} />
                <span>{social.name}</span>
              </a>
            ))}
          </div>
        </div>
      </div>
    </header>
  );
}
