import { Check, Copy } from 'lucide-react';
import type React from 'react';
import { AgentLogoStack } from './AgentLogos';

export interface JoinOneLineButtonProps {
  /** Visible text label when idle. Defaults to the public leaderboard copy. */
  label?: string;
  /** Visible text label while copied is true. */
  copiedLabel?: string;
  /** Whether the prompt was just copied. */
  copied?: boolean;
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  onMouseEnter?: React.MouseEventHandler<HTMLButtonElement>;
  type?: 'button' | 'submit';
  disabled?: boolean;
  /**
   * 'default' matches the exact public leaderboard pill (24px logos, 14px serif, 26px square copy icon).
   * 'compact' preserves the exact same 5 circular logos + serif text + square copy button for tighter slots.
   */
  size?: 'default' | 'compact';
  title?: string;
  className?: string;
  'aria-label'?: string;
  'aria-expanded'?: boolean;
  'data-task-id'?: string;
}

export function JoinOneLineButton({
  label = 'Join the effort: copy one line',
  copiedLabel = 'Copied!',
  copied = false,
  onClick,
  onMouseEnter,
  type = 'button',
  disabled = false,
  size = 'default',
  title,
  className,
  'aria-label': ariaLabel,
  'aria-expanded': ariaExpanded,
  'data-task-id': dataTaskId,
}: JoinOneLineButtonProps) {
  const isCompact = size === 'compact';
  const iconSize = isCompact ? 12 : 13;
  const logoSize = isCompact ? 20 : 24;

  const classes = [
    'join-cta',
    isCompact ? 'join-cta--compact' : '',
    copied ? 'is-copied' : '',
    className || '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <button
      type={type}
      className={classes}
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      disabled={disabled}
      title={title}
      aria-label={ariaLabel}
      aria-expanded={ariaExpanded}
      data-task-id={dataTaskId}
    >
      <AgentLogoStack size={logoSize} />
      <span className="join-cta-label">{copied ? copiedLabel : label}</span>
      <span className="join-cta-icon" aria-hidden="true">
        {copied ? <Check size={iconSize} color="var(--green)" /> : <Copy size={iconSize} />}
      </span>
    </button>
  );
}
