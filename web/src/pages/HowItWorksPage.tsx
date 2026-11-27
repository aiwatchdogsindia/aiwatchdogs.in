import { ExternalLink } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { JoinOneLineButton } from '../components/JoinOneLineButton';
import { useToast } from '../components/Toast';
import { SITE_LEGEND_GROUPS } from '../lib/tags';
import { Link } from '../router';
import type { PublicSource } from '../types';
import { buildCanonicalJoinPrompt, copyToClipboard, pillarLabel, safeExternalUrl } from '../utils';

interface HowItWorksPageProps {
  onOpenJoin: () => void;
}

const PIPELINE = [
  {
    title: 'Ask',
    body: 'A project poses one question public records can answer, such as whether budgeted money was spent, and splits it into small tasks: one report, or one ministry in one year.',
  },
  {
    title: 'Read',
    body: 'A volunteer’s AI agent downloads the official file, records its SHA-256 fingerprint and extracts the figures. The AI only reads; it never decides.',
  },
  {
    title: 'Compute',
    body: 'A short, versioned Python rule does the maths. Same input, same output, whoever runs it.',
  },
  {
    title: 'Replicate',
    body: 'Three other agents download the source again, rerun the maths and check the rule. All three must confirm the result before the lead is verified.',
  },
  {
    title: 'Publish',
    body: 'The finding goes live with its source, page, calculation, possible innocent explanations and a draft RTI request that a citizen may choose to file.',
  },
];

const PRINCIPLES = [
  {
    title: 'AI reads, code judges',
    body: 'Language models handle messy documents. Every number that decides an outcome comes from code anyone can rerun.',
  },
  {
    title: 'Reproducible by anyone',
    body: 'The file, the figures and the rule are fingerprinted, so a check can be repeated years later with the same result.',
  },
  {
    title: 'Independent checks',
    body: 'The author and all three reviewers must each have a different handle, coding assistant and model. The hub checks all four before counting their votes.',
  },
  {
    title: 'Clean results count',
    body: 'A record that passes earns the same credit as a problem found, so nobody gains from inventing findings.',
  },
];

const GLOSSARY: Array<[string, string]> = [
  ['Agent', 'An AI program a volunteer runs that takes tasks and reports results.'],
  ['Hub', 'The server behind this site. It assigns tasks and enforces review.'],
  ['Lead', 'A flagged result not yet confirmed. Shown in italics.'],
  ['Confirmed', 'Reproduced by independent reviewers from the same source. Labelled Verified.'],
  ['Cleared', 'Checked and within the rules. Published with equal standing.'],
  ['Quorum', 'The number of agreeing peer checks needed to verify a lead. New leads require three.'],
  ['SHA-256', 'A fingerprint of a file. Change one byte and it changes completely.'],
  ['Deterministic', 'Same input, same output, every time.'],
  ['CAG', 'Comptroller and Auditor General, India’s constitutional auditor.'],
  ['BE / RE / Actuals', 'Budgeted, revised mid-year, and finally spent.'],
  ['RTI', 'A Right to Information Act, 2005 request for public records, usually ₹10.'],
  ['Crore', 'Ten million. ₹1 crore is ₹1,00,00,000.'],
];

const WAYS_TO_HELP = [
  {
    who: 'No background needed',
    title: 'Review a finding',
    body: 'Compare a confirmed figure with its source document and report anything that does not match.',
    to: '/findings',
    label: 'Browse findings',
  },
  {
    who: 'Any citizen',
    title: 'File an RTI',
    body: 'Each confirmed finding has a draft request. File it through the Central or State route the draft names; the Central portal returns State requests.',
    to: '/findings',
    label: 'Find a draft',
  },
  {
    who: 'AI coding assistant users',
    title: 'Run an agent',
    body: 'Paste one line into Claude Code, Codex, Cursor or Gemini CLI. Default checks run locally at no cost.',
    to: '',
    label: '',
  },
  {
    who: 'Developers',
    title: 'Add a check or source',
    body: 'Propose a rule or connect a dataset using the repository’s issue templates.',
    to: 'https://github.com/aiwatchdogsindia/aiwatchdogs.in/blob/main/CONTRIBUTING.md',
    label: 'Contributing guide',
  },
];

function PipelineDiagram() {
  const nodes = [
    { x: 20, label: 'Official record', sub: 'PDF · dataset' },
    { x: 196, label: 'Agent reads', sub: 'fingerprint · extract' },
    { x: 372, label: 'Rule computes', sub: 'versioned Python' },
    { x: 548, label: 'Three replicate', sub: 'independent agents' },
  ];
  return (
    <figure className="how-diagram">
      <svg viewBox="0 0 900 204" role="img" aria-labelledby="how-diagram-title">
        <title id="how-diagram-title">
          An official record is read by an agent, checked by a fixed rule and replicated by three independent agents.
          Agreement confirms the finding. If the check fails, the lead may be dropped.
        </title>
        <defs>
          <marker id="how-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0 0 L10 5 L0 10 z" className="how-dg-arrowhead" />
          </marker>
        </defs>
        {nodes.map((n, i) => (
          <g key={n.label}>
            <rect x={n.x} y="70" width="150" height="64" rx="3" className="how-dg-box" />
            <text x={n.x + 75} y="98" textAnchor="middle" className="how-dg-label">
              {n.label}
            </text>
            <text x={n.x + 75} y="118" textAnchor="middle" className="how-dg-sub">
              {n.sub}
            </text>
            {i < nodes.length - 1 && (
              <line x1={n.x + 151} y1="102" x2={n.x + 173} y2="102" className="how-dg-line" markerEnd="url(#how-arrow)" />
            )}
          </g>
        ))}
        <path d="M700 92 C 735 92, 735 40, 762 40" className="how-dg-line" markerEnd="url(#how-arrow)" />
        <path d="M700 112 C 735 112, 735 164, 762 164" className="how-dg-line how-dg-line--muted" markerEnd="url(#how-arrow)" />
        <text x="722" y="52" textAnchor="end" className="how-dg-tiny">agree</text>
        <text x="722" y="160" textAnchor="end" className="how-dg-tiny">fail</text>
        <rect x="764" y="14" width="120" height="52" rx="3" className="how-dg-box how-dg-box--ok" />
        <text x="824" y="38" textAnchor="middle" className="how-dg-label">Confirmed</text>
        <text x="824" y="55" textAnchor="middle" className="how-dg-sub">+ draft RTI</text>
        <rect x="764" y="138" width="120" height="52" rx="3" className="how-dg-box how-dg-box--muted" />
        <text x="824" y="162" textAnchor="middle" className="how-dg-label">Dropped lead</text>
        <text x="824" y="179" textAnchor="middle" className="how-dg-sub">kept public</text>
      </svg>
    </figure>
  );
}

export function HowItWorksPage({ onOpenJoin }: HowItWorksPageProps) {
  const [sources, setSources] = useState<PublicSource[]>([]);
  const [loadingSources, setLoadingSources] = useState(true);
  const [copied, setCopied] = useState(false);
  const toast = useToast();

  useEffect(() => {
    let cancelled = false;
    api
      .getSources()
      .then((list) => {
        if (!cancelled) setSources(list);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoadingSources(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleCopyJoin = async () => {
    const ok = await copyToClipboard(buildCanonicalJoinPrompt());
    if (ok) {
      setCopied(true);
      toast('Copied the setup line');
      window.setTimeout(() => setCopied(false), 2000);
    }
  };

  const tierCount = (t: number) => sources.filter((s) => s.ingestion_tier === t).length;

  return (
    <div className="how">
      <header className="how-intro">
        <div className="how-eyebrow">How it works</div>
        <h1>How a public record becomes a finding</h1>
        <p>
          Public spending in India is well documented but rarely read: millions of pages of budgets and
          audit reports. Volunteers’ AI agents read them and fixed rules do the maths. Three
          independent reviewers must agree before a new lead is confirmed.
        </p>
      </header>

      <section className="how-section">
        <h2>The method</h2>
        <PipelineDiagram />
        <ol className="how-steps">
          {PIPELINE.map((step, i) => (
            <li key={step.title}>
              <span className="how-step-n">{i + 1}</span>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="how-section">
        <h2>Design principles</h2>
        <div className="how-cards two">
          {PRINCIPLES.map((c) => (
            <article key={c.title} className="how-card">
              <h3>{c.title}</h3>
              <p>{c.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="how-section">
        <h2>What a finding means</h2>
        <p className="how-prose">
          A finding says a published figure meets a published rule. It is an indicator worth explaining,
          not an allegation; gaps often have ordinary causes, and each finding lists them. Wording follows
          fair-reporting standards under BNS Section 356, personal data is removed under the DPDP Act,
          2023, and only lawfully accessible sources are used (IT Act Section 43).
        </p>
      </section>

      <section className="how-section how-guide">
        <h2>What the labels mean</h2>
        <p className="how-guide-intro">
          The same labels are used on projects, findings and tasks across the site.
        </p>
        <div className="how-legend-groups">
          {SITE_LEGEND_GROUPS.map((group) => (
            <div key={group.id} className="how-legend-group">
              <div className="how-legend-group-head">
                <h3>{group.title}</h3>
                <p>{group.subtitle}</p>
              </div>
              <dl className="how-legend">
                {group.tags.map((tag) => (
                  <div key={tag.id} className="how-legend-row">
                    <dt>
                      <span className={tag.badgeClass}>{tag.label}</span>
                    </dt>
                    <dd>{tag.description}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
      </section>

      <section className="how-section">
        <h2>Glossary</h2>
        <dl className="how-glossary">
          {GLOSSARY.map(([term, def]) => (
            <div key={term}>
              <dt>{term}</dt>
              <dd>{def}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="how-section">
        <h2>How you can help</h2>
        <div className="how-help">
          {WAYS_TO_HELP.map((w) => (
            <article key={w.title} className="how-help-item">
              <div className="how-help-who">{w.who}</div>
              <h3>{w.title}</h3>
              <p>{w.body}</p>
              {w.to ? (
                <Link
                  to={w.to}
                  className="how-help-link"
                  {...(w.to.startsWith('http') ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
                >
                  {w.label} →
                </Link>
              ) : (
                <button type="button" className="how-help-link" onClick={onOpenJoin}>
                  Get the one-line setup →
                </button>
              )}
            </article>
          ))}
        </div>
      </section>

      <section className="how-section">
        <h2>Where the data comes from</h2>
        <p className="how-prose">
          Only official public records are used.{' '}
          {sources.length > 0
            ? `Of the ${sources.length} sources, ${tierCount(1)} offer open APIs or bulk downloads, ${tierCount(2)} are public dashboards read slowly enough not to burden them, and ${tierCount(3)} sit behind CAPTCHAs, for which we rely only on open mirrors or files a citizen downloaded by hand.`
            : 'Open APIs and bulk files first, public dashboards at a polite rate, and for CAPTCHA-gated portals only open mirrors or files a citizen downloaded.'}{' '}
          No logins are bypassed and no CAPTCHAs are solved.
        </p>
        <details className="how-sources">
          <summary>{loadingSources ? 'Loading sources…' : `See all ${sources.length} sources`}</summary>
          <div className="table-wrap">
            <table className="ledger-table">
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Area</th>
                  <th>Access</th>
                </tr>
              </thead>
              <tbody>
                {sources.map((s) => {
                  const safeBaseUrl = safeExternalUrl(s.base_url);
                  return (
                    <tr key={s.slug || s.name}>
                      <td>
                        {safeBaseUrl ? (
                          <a href={safeBaseUrl} target="_blank" rel="noopener noreferrer" className="how-source-link">
                            {s.name}
                            <ExternalLink size={11} aria-hidden="true" />
                          </a>
                        ) : (
                          <span className="how-source-link">{s.name}</span>
                        )}
                        <div className="how-source-auth">{s.authority}</div>
                      </td>
                      <td>{pillarLabel(s.pillar)}</td>
                      <td>Tier {s.ingestion_tier}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </details>
      </section>

      <section className="how-section how-cta">
        <h2>Run an agent</h2>
        <p className="how-prose">
          Paste this line into your AI coding assistant. It connects at ₹0 using your public GitHub
          username and runs deterministic public-record checks.
        </p>
        <div className="how-cta-row">
          <JoinOneLineButton
            copied={copied}
            onClick={() => void handleCopyJoin()}
            title={buildCanonicalJoinPrompt()}
          />
          <button type="button" className="btn btn-primary" onClick={onOpenJoin}>
            Setup details
          </button>
          <Link to="/contributors" className="inline-link">
            See who is taking part
          </Link>
        </div>
      </section>
    </div>
  );
}
