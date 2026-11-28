import { useEffect, useRef, useState } from 'react';
import { buildEnlistPrompt, copyToClipboard } from '../utils';
import { JoinOneLineButton } from './JoinOneLineButton';
import { useToast } from './Toast';

export interface ListProjectRowProps {
  nextCode?: string;
  variant?: 'paper' | 'race';
  title?: string;
  helperText?: string;
  buttonLabel?: string;
  className?: string;
}

export function ListProjectRow({
  nextCode,
  variant = 'paper',
  title = 'Your project here',
  helperText = 'Need compute to audit or scrutinise the government? Paste one line into any coding agent — it drafts your project and gives you one GitHub link to submit it.',
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
    'project-index-card',
    'project-index-card--enlist',
    variant === 'race' ? 'project-index-card--enlist-race' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <article className={classes} aria-label="List your audit project">
      <div className="project-index-main">
        <div className="project-index-pills">
          <span className="tag-mono tag-method tag-method--enlist">
            {nextCode ? `+ ${nextCode}` : '+ NEW'}
          </span>
          <span className="tag-mono">Open submission</span>
          <span className="tag-mono">Public records</span>
        </div>
        <h2 className="project-index-title">{title}</h2>
        <span className="project-index-meta">
          {helperText}{' '}
          <a
            href="/enlist.txt"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-link"
          >
            /enlist.txt
          </a>
        </span>
      </div>

      <div className="project-index-actions">
        <JoinOneLineButton
          label={buttonLabel}
          copied={copied}
          onClick={() => void handleCopy()}
          title={enlistPrompt}
          aria-label={buttonLabel}
        />
      </div>
    </article>
  );
}
