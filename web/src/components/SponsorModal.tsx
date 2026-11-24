import { HeartHandshake, ShieldCheck, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { useDialogFocus } from '../hooks/useDialogFocus';
import { useToast } from './Toast';

export interface SponsorPreset {
  amountInr?: string;
  tier?: 'citizen_backer' | 'patron' | 'institutional_compute';
  computeDesc?: string;
}

interface SponsorModalProps {
  open: boolean;
  preset?: SponsorPreset | null;
  onClose: () => void;
  onSuccess: () => void;
}

export function SponsorModal({ open, preset, onClose, onSuccess }: SponsorModalProps) {
  const [name, setName] = useState('');
  const [handleOrOrg, setHandleOrOrg] = useState('');
  const [tier, setTier] = useState<'citizen_backer' | 'patron' | 'institutional_compute'>(
    'citizen_backer',
  );
  const [amountInr, setAmountInr] = useState('2500');
  const [computeDesc, setComputeDesc] = useState('');
  const [url, setUrl] = useState('');
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const toast = useToast();

  useDialogFocus(open, panelRef, onClose);

  useEffect(() => {
    if (!open) return;
    if (preset?.amountInr) setAmountInr(preset.amountInr);
    if (preset?.tier) setTier(preset.tier);
    if (preset?.computeDesc !== undefined) setComputeDesc(preset.computeDesc);
  }, [open, preset]);

  if (!open) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const numeric = Number(amountInr);
    if (!name.trim()) {
      setError('Please enter your name, handle, or organization.');
      return;
    }
    if (!Number.isFinite(numeric) || (numeric < 100 && !computeDesc.trim())) {
      setError('Provide at least ₹100 or describe your donated compute/API credits.');
      return;
    }
    setSubmitting(true);
    try {
      await api.pledgeSponsor({
        name: name.trim(),
        handle_or_org: handleOrOrg.trim() || undefined,
        tier,
        amount_inr: numeric,
        compute_credits_desc: computeDesc.trim() || undefined,
        url: url.trim() || undefined,
        message: message.trim() || undefined,
      });
      toast('Pledge recorded transparently on the Sponsors Wall & Public Ledger');
      setName('');
      setHandleOrOrg('');
      setComputeDesc('');
      setUrl('');
      setMessage('');
      onSuccess();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit pledge');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="overlay-backdrop centered"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sponsor-modal-title"
        tabIndex={-1}
      >
        <div className="overlay-header">
          <div>
            <div className="section-kicker" style={{ marginBottom: 4 }}>
              Open Public Treasury · Self-Declared Pledge Ledger
            </div>
            <h2 id="sponsor-modal-title" className="font-serif" style={{ margin: 0, fontSize: 25, fontWeight: 400 }}>
              Pledge Infrastructure Support or Compute
            </h2>
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close sponsor dialog">
            <X size={16} />
          </button>
        </div>

        <form className="overlay-body" onSubmit={handleSubmit}>
          <div
            style={{
              padding: '11px 14px',
              background: 'var(--green-bg)',
              border: '1px solid #BFE3BA',
              borderRadius: 6,
              fontSize: 13,
              color: 'var(--green)',
              display: 'flex',
              gap: 9,
              alignItems: 'flex-start',
            }}
          >
            <ShieldCheck size={16} style={{ flexShrink: 0, marginTop: 2 }} />
            <span>
              <strong>Editorial Independence &amp; Honest Accounting:</strong> This form records a
              public <strong>self-declared pledge</strong> (or in-kind compute offer) — it does{' '}
              <em>not</em> book into Reconciled Cash Received until verified settlement. Sponsors
              never influence which ministries, vendors, or findings are audited or published.
            </span>
          </div>

          <div className="grid-2" style={{ gap: 12 }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 12.5, fontWeight: 500 }}>
              <span>Display Name or Organization *</span>
              <input
                type="text"
                className="input-field"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Ananya Deshmukh / Civic Cloud Collective"
                required
              />
            </label>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 12.5, fontWeight: 500 }}>
              <span>Handle or Affiliation (optional)</span>
              <input
                type="text"
                className="input-field"
                value={handleOrOrg}
                onChange={(e) => setHandleOrOrg(e.target.value)}
                placeholder="e.g. @ananya-in or Bengaluru"
              />
            </label>
          </div>

          <div className="grid-2" style={{ gap: 12 }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 12.5, fontWeight: 500 }}>
              <span>Backing Tier</span>
              <select
                className="select-field"
                value={tier}
                onChange={(e) => {
                  const val = e.target.value as 'citizen_backer' | 'patron' | 'institutional_compute';
                  setTier(val);
                  if (val === 'citizen_backer' && Number(amountInr) < 500) setAmountInr('2500');
                  if (val === 'patron' && Number(amountInr) < 10000) setAmountInr('25000');
                  if (val === 'institutional_compute' && Number(amountInr) < 50000) setAmountInr('100000');
                }}
              >
                <option value="citizen_backer">Citizen Backer (₹500+)</option>
                <option value="patron">Compute Patron / API Credits (₹10,000+)</option>
                <option value="institutional_compute">Institutional Compute Grant (₹50,000+)</option>
              </select>
            </label>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 12.5, fontWeight: 500 }}>
              <span>Value in ₹ INR (or INR equivalent of compute credits) *</span>
              <input
                type="number"
                min={0}
                step={100}
                className="input-field font-mono tabular"
                value={amountInr}
                onChange={(e) => setAmountInr(e.target.value)}
                required
              />
            </label>
          </div>

          <div className="grid-2" style={{ gap: 12 }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 12.5, fontWeight: 500 }}>
              <span>Compute / Token Credit Details (optional)</span>
              <input
                type="text"
                className="input-field"
                value={computeDesc}
                onChange={(e) => setComputeDesc(e.target.value)}
                placeholder="e.g. 50M Gemini tokens / 1 GPU node-month"
              />
            </label>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 12.5, fontWeight: 500 }}>
              <span>Public Website URL (optional)</span>
              <input
                type="url"
                className="input-field"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://..."
              />
            </label>
          </div>

          <label style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 12.5, fontWeight: 500 }}>
            <span>Public Note on the Backers Wall (optional)</span>
            <textarea
              className="textarea-field"
              rows={2}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="For open, verifiable public finance transparency in India."
            />
          </label>

          {error && (
            <div style={{ color: 'var(--red)', fontSize: 12.5, fontWeight: 500 }}>{error}</div>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, paddingTop: 6 }}>
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              <HeartHandshake size={14} />
              <span>{submitting ? 'Recording on Ledger…' : 'Record Transparent Pledge'}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
