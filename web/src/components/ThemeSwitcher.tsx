import { Monitor, Moon, Sun } from 'lucide-react';
import { setThemePreference, useThemePreference, type ThemePreference } from '../lib/theme';

const OPTIONS: { value: ThemePreference; label: string; Icon: typeof Sun }[] = [
  { value: 'system', label: 'Match device', Icon: Monitor },
  { value: 'light', label: 'Light', Icon: Sun },
  { value: 'dark', label: 'Dark', Icon: Moon },
];

/** Footer theme picker: match the device, light, or dark. */
export function ThemeSwitcher() {
  const preference = useThemePreference();

  return (
    <div className="theme-switcher" role="radiogroup" aria-label="Theme">
      {OPTIONS.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={preference === value}
          aria-label={label}
          title={label}
          className={`theme-switcher-option${preference === value ? ' is-active' : ''}`}
          onClick={() => setThemePreference(value)}
        >
          <Icon size={14} aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}
