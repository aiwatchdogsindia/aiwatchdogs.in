import { useEffect, useRef, useState } from 'react';
import { buildEnlistPrompt, copyToClipboard } from '../utils';
import { JoinOneLineButton } from './JoinOneLineButton';
import { useToast } from './Toast';

export interface ListProjectRowProps {
  rank?: number | null;
  variant?: 'paper' | 'race';
  title?: string;
  helperText?: string;
  buttonLabel?: string;
  className?: string;
}

export function ListProjectRow({
  rank = null,
  variant = 'paper',
  title = 'Your project here',
  helperText = 'Need compute to audit the government? Paste one line to draft your project and get one GitHub link to submit it.',
  buttonLabel = 'List your project: copy one line',
  className = '',
}: ListProjectRowProps) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);
  const toast = useToast();

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    };
  }, []);

  const enlistPrompt = buildEnlistPrompt();

  const handleCopy = async () => {
    const ok = await copyToClipboard(enlistPrompt);
    if (ok) {
      setCopied(true);
      toast('Copied 1-line project listing prompt to clipboard');
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        setCopied(false);
        timerRef.current = null;
      }, 2000);
    } else {
      toast('Could not copy automatically — please copy the prompt manually.');
    }
  };

  const classes = [
    'lb-peq-card',
    'lb-peq-yours',
    'enlist-row',
    `enlist-row--${variant}`,
    rank == null ? 'enlist-row--no-rank' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes}>
      {rank != null ? <span className="lb-peq-rank tabular">{rank}</span> : null}
      <span className="lb-peq-avatar-dashed" aria-hidden="true">
        +
      </span>
      <div className="lb-peq-main">
        <span className="lb-peq-handle">{title}</span>
        <span className="lb-peq-meta">{helperText}</span>
      </div>
      <div className="lb-peq-yours-cta">
        <JoinOneLineButton
          label={buttonLabel}
          copied={copied}
          onClick={() => void handleCopy()}
          title={enlistPrompt}
          aria-label={buttonLabel}
        />
      </div>
    </div>
  );
}
