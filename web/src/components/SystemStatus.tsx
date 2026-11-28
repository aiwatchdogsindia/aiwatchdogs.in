import { useEffect, useState } from 'react';

type Status = 'checking' | 'ok' | 'degraded' | 'unreachable';

const LABEL: Record<Status, string> = {
  checking: 'Checking status',
  ok: 'All systems normal.',
  degraded: 'Hub degraded.',
  unreachable: 'Hub unreachable.',
};

const RECHECK_MS = 60_000;

/**
 * Footer status line. Reads the hub's own `/health` endpoint (the same one
 * uptime monitors use) and links to it, so the line never claims more than
 * the hub reports.
 */
export function SystemStatus() {
  const [status, setStatus] = useState<Status>('checking');

  useEffect(() => {
    let cancelled = false;

    const check = async () => {
      if (document.visibilityState === 'hidden') return;
      try {
        const res = await fetch('/health', { headers: { Accept: 'application/json' } });
        const body = (await res.json()) as { status?: string };
        if (!cancelled) setStatus(res.ok && body.status === 'ok' ? 'ok' : 'degraded');
      } catch {
        if (!cancelled) setStatus('unreachable');
      }
    };

    void check();
    const timer = window.setInterval(check, RECHECK_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <a
      href="/health"
      target="_blank"
      rel="noopener noreferrer"
      className={`system-status system-status--${status}`}
      title="Live hub health check (/health)"
    >
      <span className="system-status-dot" aria-hidden="true" />
      <span role="status">{LABEL[status]}</span>
    </a>
  );
}
