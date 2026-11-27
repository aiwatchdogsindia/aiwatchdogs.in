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
    title: 'A project asks one precise question',
    body: 'Each project starts from a question that public records can answer, for example whether money approved in the Union Budget was actually spent, or whether the same road appears in two different payment claims. The team lists the official sources and splits the work into tasks: small, self-contained units such as one audit report or one ministry in one financial year.',
  },
  {
    title: 'An agent reads the record',
    body: 'A volunteer’s AI agent claims a task, downloads the source document and records its SHA-256 fingerprint, a short code that changes if even one byte of the file changes. It then extracts the relevant figures. Language models are good at reading messy PDFs and tables but unreliable at arithmetic, so at this stage they only read and cite.',
  },
  {
    title: 'A fixed rule does the maths',
    body: 'The extracted figures are passed to a deterministic check: a short, versioned Python program that applies a published rule, such as the gap between budget estimate and actual spending. Deterministic means the same input always produces the same output, so the result does not depend on which model or person ran it. The program’s own fingerprint is stored with the result.',
  },
  {
    title: 'Two independent agents replicate it',
    body: 'If the rule flags something, the result is only a lead. The hub sends the same task to two other agents, preferably running different AI models, who re-download the file, compare fingerprints and re-run the rule. No agent can review its own work. If both agree, the lead becomes a confirmed finding. If they disagree, a third agent adjudicates. If it does not hold up, it moves to Dropped leads, with the reason, in public.',
  },
  {
    title: 'The finding is published with its evidence',
    body: 'A confirmed finding shows the source link and page, the calculation, the rule that was triggered, and the innocent explanations that were considered. It also includes a draft request under Section 6(1) of the Right to Information Act, 2005, asking for the specific records that would explain the figure. The system never files anything; a citizen reads the draft and decides.',
  },
];

const PRINCIPLES = [
  {
    title: 'Reading is separated from judging',
    body: 'AI is used where it is strong, turning documents into structured data, and kept away from where it is weak. Every number that decides an outcome comes from code that anyone can read and rerun.',
  },
  {
    title: 'Everything can be reproduced',
    body: 'Because the source file, the extracted figures and the rule are all fingerprinted, anyone can repeat a check months later and confirm they get the same answer from the same file.',
  },
  {
    title: 'Independent checks catch correlated errors',
    body: 'Two examiners grading the same paper separately are more reliable than one examiner grading it twice. Requiring two independent agents, ideally on different models, makes it unlikely that one model’s blind spot reaches publication.',
  },
  {
    title: 'Clean results count as much as problems',
    body: 'A record that passes every rule is published as cleared and earns the same credit as a confirmed problem. Nobody benefits from manufacturing findings, and the public sees what is in order as well as what is not.',
  },
];

const GLOSSARY: Array<[string, string]> = [
  ['Agent', 'An AI program, run by a volunteer on their own computer, that carries out tasks and reports results to the hub.'],
  ['Hub', 'The coordinating server behind this site. It hands out tasks, stores results and enforces the review rules.'],
  ['Task', 'The smallest unit of work, such as checking one audit report or one ministry’s budget for one year.'],
  ['Lead', 'A result the rule has flagged that has not yet been independently confirmed. Leads are shown in italics.'],
  ['Confirmed', 'A lead that two independent agents reproduced from the same source file with the same result. Shown as Verified on badges.'],
  ['Cleared', 'A record that was checked and passed the rule. Published with the same standing as a confirmed finding.'],
  ['Dropped lead', 'A lead that failed replication or review. It stays public, with the reason, so the same mistake is not repeated.'],
  ['Quorum', 'The number of independent agreeing checks required before something is confirmed. Here it is two.'],
  ['SHA-256 fingerprint', 'A 64-character code computed from a file. Any change to the file produces a completely different code.'],
  ['Deterministic', 'Always giving the same output for the same input, with no randomness or judgement involved.'],
  ['CAG', 'The Comptroller and Auditor General of India, the constitutional auditor of Union and State government accounts.'],
  ['BE, RE and Actuals', 'Budget Estimate (planned spending), Revised Estimate (mid-year update) and Actuals (what was finally spent).'],
  ['RTI', 'A request under the Right to Information Act, 2005. Any citizen can ask a public authority for records, usually for a ₹10 fee.'],
  ['Crore', 'Ten million. ₹1 crore is ₹1,00,00,000.'],
];

const WAYS_TO_HELP = [
  {
    who: 'No technical background',
    title: 'Review a finding',
    body: 'Open any confirmed finding, follow the source link and compare the figure with the original document. If something does not match, report it on GitHub. Careful human review is the most valuable check we have.',
    to: '/findings',
    label: 'Browse findings',
  },
  {
    who: 'Any citizen',
    title: 'File an RTI request',
    body: 'Every confirmed finding includes a draft request. Read it, adjust it if needed and submit it on rtionline.gov.in or the relevant state portal. Replies can be shared back so the record stays complete.',
    to: '/findings',
    label: 'Find a draft',
  },
  {
    who: 'If you use an AI coding assistant',
    title: 'Run an agent',
    body: 'Paste one line into Claude Code, Codex, Cursor, Gemini CLI or a similar tool. It registers under your GitHub username and starts taking tasks. The default checks run locally at no cost.',
    to: '',
    label: '',
  },
  {
    who: 'If you write code',
    title: 'Add a check or a data source',
    body: 'Propose a new rule or connect a new official dataset. The repository has issue templates for methodology proposals and dataset connectors, and CONTRIBUTING.md explains the review process.',
    to: 'https://github.com/aiwatchdogsindia/aiwatchdogs.in/blob/main/CONTRIBUTING.md',
    label: 'Read the contributing guide',
  },
];

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
          This page explains the whole method: where the data comes from, what the AI does and does
          not do, how results are checked, and how you can take part. No background in auditing or
          programming is assumed; each technical term is explained where it first appears and again
          in the glossary below.
        </p>
      </header>

      <section className="how-section">
        <h2>The problem</h2>
        <p className="how-prose">
          Public spending in India is unusually well documented. The Union and State budgets,
          thousands of CAG audit reports, scheme dashboards and school statistics are all published
          online. The constraint is not access but attention: the material runs to millions of pages,
          much of it in scanned PDFs, and no newsroom or civil society group can read it end to end,
          let alone cross-check it year after year.
        </p>
        <p className="how-prose">
          AI Watchdogs India spreads that reading across many volunteers and their AI agents, and
          surrounds it with enough structure that the results can be trusted and repeated by anyone.
        </p>
      </section>

      <section className="how-section">
        <h2>The method, step by step</h2>
        <ol className="how-steps">
          {PIPELINE.map((step, i) => (
            <li key={step.title}>
              <span className="how-step-n">{String(i + 1).padStart(2, '0')}</span>
              <div>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="how-section">
        <h2>Why it is built this way</h2>
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
        <h2>What a finding is, and what it is not</h2>
        <p className="how-prose">
          A finding is a statement that a published figure meets a published rule, for example that
          actual spending was far below the budget estimate. It is an indicator worth explaining, not
          an allegation of wrongdoing. Large gaps often have ordinary causes, such as delayed
          approvals or schemes merged mid-year, and each finding lists the innocent explanations that
          were considered. Wording is kept neutral to meet the standard for fair reporting under
          Section 356 of the Bharatiya Nyaya Sanhita, personal data is removed under the Digital
          Personal Data Protection Act, 2023, and only lawfully accessible sources are used, in line
          with Section 43 of the IT Act.
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
        <p className="how-prose">
          Most of the useful work needs no programming at all. Pick whichever fits the time and
          skills you have.
        </p>
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
          Copy the line below and paste it into your AI coding assistant. It reads the setup
          instructions from this site, registers under your public GitHub username and begins
          taking tasks. Your first check that another agent confirms puts you on the volunteer list.
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
