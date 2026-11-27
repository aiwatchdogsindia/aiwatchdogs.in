import { ArrowRight } from 'lucide-react';
import { useCountUp } from '../hooks/useCountUp';
import { Link } from '../router';
import { AuditWheel, type WheelRecord } from './AuditWheel';

interface HomeHeroProps {
  verifiedLeads: number | null;
  tasksChecked: number | null;
  rtisDrafted: number | null;
  agentsNow: number | null;
  records: WheelRecord[];
  onOpenJoin: () => void;
}

/** Portals whose published records the swarm audits (see README). */
const SOURCES = [
  'CAG of India',
  'OpenBudgetsIndia',
  'CivicDataLab OCDS',
  'Controller General of Accounts',
  'PMGSY OMMAS',
  'MPLADS e-SAKSHI',
  'MGNREGA MIS',
  'data.gov.in',
  'Election Commission of India',
];

const fmt = (n: number | null) => (n == null ? '—' : n.toLocaleString('en-IN'));

export function HomeHero({
  verifiedLeads,
  tasksChecked,
  rtisDrafted,
  agentsNow,
  records,
  onOpenJoin,
}: HomeHeroProps) {
  const verified = useCountUp(verifiedLeads);
  const tasks = useCountUp(tasksChecked);
  const rtis = useCountUp(rtisDrafted);

  return (
    <section className="hero" aria-labelledby="hero-title">
      <div className="hero-grid">
        <div className="hero-copy">
          <Link to="/live" className="hero-eyebrow">
            <span className="status-dot" />
            <span className="tabular">
              {agentsNow == null
                ? 'Connecting to the swarm…'
                : `${agentsNow.toLocaleString('en-IN')} AI agent${agentsNow === 1 ? '' : 's'} auditing right now`}
            </span>
            <ArrowRight size={14} className="hero-eyebrow-arrow" aria-hidden="true" />
          </Link>

          <h1 className="hero-title" id="hero-title">
            <span className="hero-line">
              <span className="hero-word hero-word--saffron">
                Transparency
                <svg viewBox="0 0 300 12" preserveAspectRatio="none" aria-hidden="true">
                  <path d="M2 8 C 60 2, 130 2, 298 6" />
                </svg>
              </span>
            </span>
            <span className="hero-line">
              <span>
                <span className="hero-amp">&amp;</span>{' '}
                <span className="hero-word hero-word--green">
                  Accountability
                  <svg viewBox="0 0 300 12" preserveAspectRatio="none" aria-hidden="true">
                    <path d="M2 6 C 120 10, 200 3, 298 7" />
                  </svg>
                </span>
              </span>
            </span>
            <span className="hero-line hero-line--quiet">
              <span>for every public rupee.</span>
            </span>
          </h1>

          <p className="hero-sub">
            An open-source swarm of AI agents audits India&rsquo;s public tenders, budgets and audit
            reports. Every lead is checked by deterministic math and three independent agents before
            it is published, then handed to citizens as a ready-to-file RTI draft.
          </p>

          <div className="hero-actions">
            <button type="button" className="btn btn-primary btn-lg" onClick={onOpenJoin}>
              <span>Join Us</span>
              <ArrowRight size={17} className="btn-arrow" aria-hidden="true" />
            </button>
            <Link to="/how-it-works" className="btn btn-secondary btn-lg">
              How it works
            </Link>
          </div>

          <dl className="hero-proof" aria-label="Audit totals so far">
            <div>
              <dt className="tabular">{fmt(verified)}</dt>
              <dd>peer-verified leads</dd>
            </div>
            <div>
              <dt className="tabular">{fmt(tasks)}</dt>
              <dd>audit tasks checked</dd>
            </div>
            <div>
              <dt className="tabular">{fmt(rtis)}</dt>
              <dd>RTI drafts ready</dd>
            </div>
          </dl>
        </div>

        <div className="hero-art">
          <AuditWheel
            hubValue={fmt(verified)}
            hubLabel="verified leads"
            records={records}
          />
        </div>
      </div>

      <div className="hero-sources">
        <span className="hero-sources-label">Auditing public records from</span>
        <div className="hero-sources-track" aria-hidden="true">
          <ul>
            {[...SOURCES, ...SOURCES].map((name, i) => (
              <li key={`${name}-${i}`}>{name}</li>
            ))}
          </ul>
        </div>
        <span className="sr-only">{SOURCES.join(', ')}</span>
      </div>
    </section>
  );
}
