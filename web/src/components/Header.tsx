import { Menu, Terminal, X } from 'lucide-react';
import { useState } from 'react';
import { Link, useRouter } from '../router';

interface HeaderProps {
  onOpenJoin: () => void;
}

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard' },
  { to: '/missions', label: 'Missions' },
  { to: '/findings', label: 'Findings' },
  { to: '/live', label: 'Live Swarm' },
  { to: '/contributors', label: 'Contributors' },
  { to: '/sponsors', label: 'Sponsors & Ledger' },
  { to: '/how-it-works', label: 'How It Works' },
];

export function Header({ onOpenJoin }: HeaderProps) {
  const { pathname } = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);

  const isActive = (to: string) => {
    if (to === '/') return pathname === '/';
    if (to === '/sponsors') return pathname.startsWith('/sponsors') || pathname.startsWith('/ledger');
    if (to === '/contributors') return pathname.startsWith('/contributors') || pathname.startsWith('/a/');
    return pathname === to || pathname.startsWith(`${to}/`);
  };

  return (
    <header className="site-header">
      <div className="container header-inner">
        <Link to="/" className="masthead" onClick={() => setMobileOpen(false)}>
          <span className="masthead-title">
            AI Watchdogs <span className="masthead-dot">·</span> India
          </span>
          <span className="masthead-sub">aiwatchdogs.in · Open Civic-Audit Swarm</span>
        </Link>

        <nav className="main-nav" aria-label="Primary navigation">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className={`nav-link ${isActive(item.to) ? 'active' : ''}`}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="header-actions">
          <button type="button" className="btn btn-primary" onClick={onOpenJoin}>
            <Terminal size={14} />
            <span>Join Swarm</span>
          </button>
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
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className={`nav-link ${isActive(item.to) ? 'active' : ''}`}
              onClick={() => setMobileOpen(false)}
            >
              {item.label}
            </Link>
          ))}
          <Link
            to="/ledger"
            className={`nav-link ${pathname === '/ledger' ? 'active' : ''}`}
            onClick={() => setMobileOpen(false)}
          >
            Public Treasury Ledger
          </Link>
        </div>
      </div>
    </header>
  );
}
