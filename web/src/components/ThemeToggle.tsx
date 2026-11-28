import { Moon, Sun } from 'lucide-react';
import { toggleTheme, useTheme } from '../lib/theme';

/** Light / dark switch. `compact` adds a text label for the mobile menu. */
export function ThemeToggle({ variant = 'header' }: { variant?: 'header' | 'compact' }) {
  const theme = useTheme();
  const label = theme === 'dark' ? 'Light mode' : 'Dark mode';
  const Icon = theme === 'dark' ? Sun : Moon;

  return (
    <button
      type="button"
      className={`theme-toggle theme-toggle--${variant}`}
      aria-label={`Switch to ${label.toLowerCase()}`}
      title={variant === 'header' ? label : undefined}
      onClick={toggleTheme}
    >
      <Icon size={variant === 'compact' ? 15 : 16} aria-hidden="true" />
      {variant === 'compact' ? <span>{label}</span> : null}
    </button>
  );
}
