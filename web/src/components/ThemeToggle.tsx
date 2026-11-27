import { useTheme } from '../lib/theme';

/** Sun/moon switch. The icon morphs between the two states in CSS. */
export function ThemeToggle({ className = '' }: { className?: string }) {
  const { theme, toggleTheme } = useTheme();
  const next = theme === 'dark' ? 'light' : 'dark';

  return (
    <button
      type="button"
      className={`theme-toggle ${className}`.trim()}
      onClick={toggleTheme}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
    >
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <mask id="theme-toggle-moon">
          <rect x="0" y="0" width="24" height="24" fill="white" />
          <circle className="theme-toggle-cut" cx="24" cy="6" r="7" fill="black" />
        </mask>
        <circle
          className="theme-toggle-core"
          cx="12"
          cy="12"
          r="5"
          fill="currentColor"
          mask="url(#theme-toggle-moon)"
        />
        <g className="theme-toggle-rays" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
          <line x1="12" y1="2.5" x2="12" y2="4.5" />
          <line x1="12" y1="19.5" x2="12" y2="21.5" />
          <line x1="2.5" y1="12" x2="4.5" y2="12" />
          <line x1="19.5" y1="12" x2="21.5" y2="12" />
          <line x1="5.3" y1="5.3" x2="6.7" y2="6.7" />
          <line x1="17.3" y1="17.3" x2="18.7" y2="18.7" />
          <line x1="5.3" y1="18.7" x2="6.7" y2="17.3" />
          <line x1="17.3" y1="6.7" x2="18.7" y2="5.3" />
        </g>
      </svg>
    </button>
  );
}
