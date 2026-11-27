import { Menu, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { OFFICIAL_SOCIALS } from '../lib/socials';
import { Link, useRouter } from '../router';
import { AgentModeToggle } from './AgentView';
import { SocialIcon } from './SocialLinks';
import { ThemeToggle } from './ThemeToggle';

interface HeaderProps {
  onOpenJoin: () => void;
}

const NAV_ITEMS = [
  { to: '/projects', label: 'Projects', key: 'projects' },
  { to: '/findings', label: 'Findings', key: 'findings' },
  { to: '/live', label: 'Live', key: 'live' },
  { to: '/#map', label: 'Map', key: 'map' },
  { to: '/graveyard', label: 'Graveyard', key: 'graveyard' },
  { to: '/contributors', label: 'Contributors', key: 'contributors' },
  { to: '/how-it-works', label: 'How it works', key: 'how-it-works' },
];

export function Header({ onOpenJoin }: HeaderProps) {
  const { pathname, hash } = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname, hash]);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

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
    <header className={`hdr ${scrolled ? 'is-scrolled' : ''} ${mobileOpen ? 'is-open' : ''}`}>
      {/* Tiranga ribbon: saffron, white, green */}
      <div className="hdr-ribbon" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>

      <div className="hdr-row">
        <Link to="/" className="hdr-brand" onClick={() => setMobileOpen(false)}>
          <img src="/logo.svg" alt="" className="hdr-logo" width={44} height={44} />
          <span className="hdr-wordmark">
            <span className="hdr-name">AI Watchdogs India</span>
            <span className="hdr-tag">Public spending, audited in the open</span>
          </span>
        </Link>

        <nav className="hdr-nav" aria-label="Primary navigation">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.key}
              to={item.to}
              className={`hdr-link ${isActive(item) ? 'is-active' : ''}`}
              aria-current={isActive(item) ? 'page' : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="hdr-tools">
          <AgentModeToggle variant="header" />
          <ThemeToggle />
          <button type="button" className="btn btn-primary btn-sm hdr-join" onClick={onOpenJoin}>
            Join Us
          </button>
          <button
            type="button"
            className="hdr-menu-btn"
            aria-label="Toggle navigation menu"
            aria-expanded={mobileOpen}
            onClick={() => setMobileOpen((v) => !v)}
          >
            {mobileOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>

      <div className="hdr-drawer" hidden={!mobileOpen}>
        <nav className="hdr-drawer-nav" aria-label="Mobile navigation">
          <Link
            to="/"
            className={`hdr-drawer-link ${pathname === '/' && !hash ? 'is-active' : ''}`}
            onClick={() => setMobileOpen(false)}
          >
            Home
          </Link>
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.key}
              to={item.to}
              className={`hdr-drawer-link ${isActive(item) ? 'is-active' : ''}`}
              onClick={() => setMobileOpen(false)}
            >
              {item.label}
            </Link>
          ))}
          <Link
            to="/brief"
            className={`hdr-drawer-link ${pathname === '/brief' ? 'is-active' : ''}`}
            onClick={() => setMobileOpen(false)}
          >
            Project Brief
          </Link>
          <Link
            to="/changelog"
            className={`hdr-drawer-link ${pathname === '/changelog' ? 'is-active' : ''}`}
            onClick={() => setMobileOpen(false)}
          >
            Changelog
          </Link>
        </nav>

        <div className="hdr-drawer-actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              setMobileOpen(false);
              onOpenJoin();
            }}
          >
            Join Us
          </button>
          <span onClick={() => setMobileOpen(false)}>
            <AgentModeToggle variant="compact" />
          </span>
        </div>

        <div className="hdr-drawer-socials">
          {OFFICIAL_SOCIALS.map((social) => (
            <a
              key={social.id}
              href={social.href}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={social.ariaLabel}
              className="hdr-drawer-social"
              onClick={() => setMobileOpen(false)}
            >
              <SocialIcon id={social.id} size={16} />
              <span>{social.name}</span>
            </a>
          ))}
        </div>
      </div>
    </header>
  );
}
