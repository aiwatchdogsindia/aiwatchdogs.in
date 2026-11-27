import { Menu, X } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { OFFICIAL_SOCIALS } from '../lib/socials';
import { Link, useRouter } from '../router';
import { AgentModeToggle } from './AgentView';
import { SocialIcon } from './SocialLinks';
import { ThemeToggle } from './ThemeToggle';
import { TirangaFlag } from './TirangaFlag';

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
  const [hoverKey, setHoverKey] = useState<string | null>(null);
  const [pill, setPill] = useState<{ left: number; width: number } | null>(null);
  const navRef = useRef<HTMLElement>(null);

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

  const activeKey = NAV_ITEMS.find(isActive)?.key ?? null;
  const pillKey = hoverKey ?? activeKey;

  // The nav marker slides to the hovered link and rests on the current page
  const placePill = useCallback(() => {
    const nav = navRef.current;
    const link = pillKey ? nav?.querySelector<HTMLElement>(`[data-nav="${pillKey}"]`) : null;
    setPill(link ? { left: link.offsetLeft, width: link.offsetWidth } : null);
  }, [pillKey]);

  useLayoutEffect(placePill, [placePill]);

  useEffect(() => {
    window.addEventListener('resize', placePill);
    void document.fonts?.ready.then(placePill);
    return () => window.removeEventListener('resize', placePill);
  }, [placePill]);

  return (
    <header className={`hdr ${scrolled ? 'is-scrolled' : ''} ${mobileOpen ? 'is-open' : ''}`}>
      <div className="hdr-row">
        <Link to="/" className="hdr-brand" onClick={() => setMobileOpen(false)}>
          <TirangaFlag className="hdr-flag" width={38} />
          <span className="hdr-brand-rule" aria-hidden="true" />
          <img src="/logo.svg" alt="" className="hdr-logo" width={44} height={44} />
          <span className="hdr-wordmark">
            <span className="hdr-name">AI Watchdogs India</span>
            <span className="hdr-tag">Public spending, audited in the open</span>
          </span>
        </Link>

        <nav
          className="hdr-nav"
          aria-label="Primary navigation"
          ref={navRef}
          onMouseLeave={() => setHoverKey(null)}
        >
          <span
            className={`hdr-nav-pill ${pill ? 'is-on' : ''} ${hoverKey && hoverKey !== activeKey ? 'is-preview' : ''}`}
            style={pill ? { width: pill.width, transform: `translateX(${pill.left}px)` } : undefined}
            aria-hidden="true"
          />
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.key}
              to={item.to}
              data-nav={item.key}
              onMouseEnter={() => setHoverKey(item.key)}
              onFocus={() => setHoverKey(item.key)}
              onBlur={() => setHoverKey(null)}
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
