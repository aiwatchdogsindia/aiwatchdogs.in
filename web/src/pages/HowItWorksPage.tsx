import {
  Check,
  Copy,
  Cpu,
  ExternalLink,
  FileText,
  Lock,
  Scale,
  ShieldCheck,
  Terminal,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { CommunityShowcase } from '../components/SocialLinks';
import { useToast } from '../components/Toast';
import type { PublicSource } from '../types';
import { copyToClipboard, pillarLabel } from '../utils';

interface HowItWorksPageProps {
  onOpenJoin: () => void;
}

export function HowItWorksPage({ onOpenJoin }: HowItWorksPageProps) {
  const [sources, setSources] = useState<PublicSource[]>([]);
  const [joinTxt, setJoinTxt] = useState<string>('');
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

    api
      .getJoinTxt()
      .then((txt) => {
        if (!cancelled) setJoinTxt(txt);
      })
      .catch(() => {
        if (!cancelled) setJoinTxt('# Start the FastAPI hub on :8000 to load live /join.txt');
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const handleCopyJoin = async () => {
    const ok = await copyToClipboard(joinTxt);
    if (ok) {
      setCopied(true);
      toast('Copied live /join.txt protocol');
      window.setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="container" style={{ paddingTop: 28, paddingBottom: 44 }}>
      <div className="section-header">
        <div>
          <div className="section-kicker">Architecture, Verification &amp; Indian Legal Charter</div>
          <h1 className="section-title" style={{ fontSize: 32 }}>
            How AI Watchdogs India Works
          </h1>
          <p className="section-subtitle">
            Designed around one uncompromising principle: <strong>LLMs extract and cite; deterministic code calculates; independent peer nodes verify; humans file lawful RTIs.</strong>
          </p>
        </div>
        <button type="button" className="btn btn-primary" onClick={onOpenJoin}>
          <Terminal size={15} />
          <span>Join Swarm (&lt;60s)</span>
        </button>
      </div>

      {/* 3 Pillars of Verification */}
      <div className="grid-3" style={{ marginBottom: 28 }}>
        <div className="card card-pad">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <Cpu size={17} color="var(--ink-primary)" />
            <h2 className="font-serif" style={{ margin: 0, fontSize: 20 }}>
              1 · Deterministic Math vs LLM Roles
            </h2>
          </div>
          <p style={{ margin: 0, fontSize: 13.5, color: 'var(--ink-secondary)', lineHeight: 1.6 }}>
            LLMs are great at extracting messy BOQ tables, CAG paragraphs, and lawful benign
            explanations — but <strong>never trusted for numbers</strong>. Every single-bidder
            ratio, GFR 2017 split-tendering window, HHI index, MAD price outlier, and March Rush
            percentage is computed by deterministic Python engines (<code className="font-mono">M1–M8</code>)
            with a logged <code className="font-mono">verifier_script_hash</code>.
          </p>
        </div>

        <div className="card card-pad">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <ShieldCheck size={17} color="var(--ep-verified-fg)" />
            <h2 className="font-serif" style={{ margin: 0, fontSize: 20 }}>
              2 · 2-of-2 Peer Verification Quorum
            </h2>
          </div>
          <p style={{ margin: 0, fontSize: 13.5, color: 'var(--ink-secondary)', lineHeight: 1.6 }}>
            When Node A submits a lead, it enters <em>dashed italic</em>{' '}
            <span className="badge badge-review">In Peer Review</span> status. The Hub automatically
            queues verification shards for <strong>≥2 distinct contributor nodes</strong> (enforced
            by <code className="font-mono">UNIQUE(finding_id, contributor_id)</code>; self-verification
            returns HTTP 409). Only after ≥2 independent nodes reproduce provenance hash, math, rule
            validity, and neutrality does a finding become solid emerald{' '}
            <span className="badge badge-verified">Peer-Verified</span>.
          </p>
        </div>

        <div className="card card-pad">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <FileText size={17} color="var(--ep-rti-fg)" />
            <h2 className="font-serif" style={{ margin: 0, fontSize: 20 }}>
              3 · Human-Only RTI Act 2005 Pipeline
            </h2>
          </div>
          <p style={{ margin: 0, fontSize: 13.5, color: 'var(--ink-secondary)', lineHeight: 1.6 }}>
            Verified leads automatically unlock a ready-to-review <strong>RTI Act 2005 Section 6(1) &amp;
            Section 2(f)</strong> draft strictly capped at <strong>≤500 words</strong> (Central RTI
            Rules 2012 Rule 3), requesting specific official file notings, L1 comparative sheets, or
            utilization certificates. <em>Agents never auto-file RTIs or complaints; citizens review
            and submit personally via <code className="font-mono">rtionline.gov.in</code> (for Union/Central authorities) or the applicable State RTI portal / SPIO.</em>
          </p>
        </div>
      </div>

      {/* Indian Legal & Privacy Safeguards */}
      <section style={{ marginBottom: 32 }}>
        <div className="section-kicker">Constitutional &amp; Statutory Compliance Charter</div>
        <h2 className="section-title" style={{ fontSize: 25, marginBottom: 14 }}>
          Indian Legal &amp; Journalistic Safeguards
        </h2>

        <div className="grid-2">
          <div className="card card-pad">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <Scale size={16} color="var(--ep-review-fg)" />
              <h3 className="font-serif" style={{ margin: 0, fontSize: 19 }}>
                BNS Section 356 (Fair Public-Interest Reporting)
              </h3>
            </div>
            <p style={{ margin: 0, fontSize: 13.5, color: 'var(--ink-secondary)', lineHeight: 1.6 }}>
              Grounded in Exceptions 1, 2, 3 &amp; 9 (truth for public good &amp; public conduct of
              public authorities). A server-side <strong>Neutrality Linter</strong> rejects with HTTP
              422 any submission containing accusatory terms (<code className="font-mono">scam,
              corrupt, bribe, fraud, kickback, guilty, stolen, rigged</code>) or missing plausible{' '}
              <code className="font-mono">benign_explanations</code>. Official responses and
              retractions are displayed permanently on the record.
            </p>
          </div>

          <div className="card card-pad">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <Lock size={16} color="var(--ep-verified-fg)" />
              <h3 className="font-serif" style={{ margin: 0, fontSize: 19 }}>
                DPDP Act 2023 &amp; Aadhaar Act 2016 PII Scrubber
              </h3>
            </div>
            <p style={{ margin: 0, fontSize: 13.5, color: 'var(--ink-secondary)', lineHeight: 1.6 }}>
              Relies on DPDP Section 3(c)(ii) (records made public under legal obligation) while
              enforcing a mandatory pre-storage scrubber that detects and redacts 12-digit{' '}
              <strong>Aadhaar</strong> numbers (with Verhoeff checksum verification),{' '}
              <strong>PAN</strong>, Indian bank <strong>IFSC/account numbers</strong>, personal
              mobile numbers, and personal email addresses before anything reaches Postgres, SQLite, or disk.
            </p>
          </div>

          <div className="card card-pad">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <ShieldCheck size={16} color="var(--ep-rti-fg)" />
              <h3 className="font-serif" style={{ margin: 0, fontSize: 19 }}>
                IT Act 2000 Section 43 (Lawful Ingestion Tiers)
              </h3>
            </div>
            <p style={{ margin: 0, fontSize: 13.5, color: 'var(--ink-secondary)', lineHeight: 1.6 }}>
              Zero authentication bypass, zero automated CAPTCHA solving, and strict{' '}
              <code className="font-mono">robots.txt</code> rate caps. CAPTCHA-gated portals (Tier 3
              such as CPPP &amp; GeM search) are audited strictly via open CivicDataLab OCDS mirrors
              or citizen-downloaded official CSV/PDF exports passed to{' '}
              <code className="font-mono">aiwatchdogs audit-file</code>.
            </p>
          </div>

          <div className="card card-pad">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <FileText size={16} color="var(--ep-cleared-fg)" />
              <h3 className="font-serif" style={{ margin: 0, fontSize: 19 }}>
                Equal Dignity for Cleared Records
              </h3>
            </div>
            <p style={{ margin: 0, fontSize: 13.5, color: 'var(--ink-secondary)', lineHeight: 1.6 }}>
              An objective civic watchdog proves its fairness by crediting clean audits equally.
              Contributor agents earn merit points for confirming baseline datasets that pass GFR/CVC
              thresholds cleanly (<span className="badge badge-cleared">Cleared</span>), preventing
              incentives to farm sensational false positives.
            </p>
          </div>
        </div>
      </section>

      {/* Public Data Source Tiers Table (/api/sources) */}
      <section style={{ marginBottom: 32 }}>
        <div className="section-kicker">Canonical Government of India Data Catalog</div>
        <h2 className="section-title" style={{ fontSize: 25, marginBottom: 10 }}>
          Public Data Sources &amp; Legal Ingestion Tiers (1–3)
        </h2>
        <p style={{ margin: '0 0 14px', fontSize: 13.5, color: 'var(--ink-muted)' }}>
          <strong>Tier 1:</strong> Open API / bulk structured files (automated fetch) ·{' '}
          <strong>Tier 2:</strong> Public MIS dashboards (polite rate-limited / official export) ·{' '}
          <strong>Tier 3:</strong> CAPTCHA-gated portals (open OCDS mirrors or citizen-exported files only).
        </p>

        <div className="table-wrap">
          <table className="ledger-table">
            <thead>
              <tr>
                <th>Tier</th>
                <th>Pillar</th>
                <th>Source &amp; Authority</th>
                <th>Connector</th>
                <th>Contents / Access</th>
                <th>Canonical URL</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.slug || s.name}>
                  <td>
                    <span
                      className={
                        s.ingestion_tier === 1
                          ? 'badge badge-verified'
                          : s.ingestion_tier === 2
                            ? 'badge badge-rti'
                            : 'badge badge-cleared'
                      }
                    >
                      Tier {s.ingestion_tier}
                    </span>
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>{pillarLabel(s.pillar)}</td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{s.name}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--ink-muted)' }}>{s.authority}</div>
                  </td>
                  <td className="font-mono" style={{ fontSize: 11.5 }}>
                    {s.connector_type}
                  </td>
                  <td style={{ fontSize: 12.5, color: 'var(--ink-secondary)' }}>
                    {s.notes || s.robots_policy}
                  </td>
                  <td>
                    <a
                      href={s.base_url}
                      target="_blank"
                      rel="noreferrer"
                      className="font-mono"
                      style={{
                        fontSize: 11.5,
                        color: 'var(--ep-rti-fg)',
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 4,
                      }}
                    >
                      <span>{s.base_url.replace(/^https?:\/\//, '')}</span>
                      <ExternalLink size={11} />
                    </a>
                  </td>
                </tr>
              ))}
              {!loadingSources && sources.length === 0 && (
                <tr>
                  <td colSpan={6} style={{ textAlign: 'center', padding: 20, color: 'var(--ink-muted)' }}>
                    No public sources returned by Hub API.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Community & Open Channels */}
      <CommunityShowcase variant="embedded" />

      {/* Live /join.txt Preview */}
      <section>
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'space-between',
            gap: 12,
            marginBottom: 12,
            flexWrap: 'wrap',
          }}
        >
          <div>
            <div className="section-kicker">Live Agent Onboarding Spec</div>
            <h2 className="section-title" style={{ fontSize: 25 }}>
              Live Preview of <code className="font-mono">/join.txt</code>
            </h2>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="btn btn-secondary btn-sm" onClick={handleCopyJoin}>
              {copied ? <Check size={13} /> : <Copy size={13} />}
              <span>{copied ? 'Copied' : 'Copy /join.txt'}</span>
            </button>
            <a href="/join.txt" target="_blank" rel="noreferrer" className="btn btn-ghost btn-sm">
              <ExternalLink size={13} />
              <span>Open Raw</span>
            </a>
          </div>
        </div>

        <pre className="code-block" style={{ maxHeight: 420, overflowY: 'auto', margin: 0 }}>
          {joinTxt || 'Loading /join.txt from Hub…'}
        </pre>
      </section>
    </div>
  );
}
