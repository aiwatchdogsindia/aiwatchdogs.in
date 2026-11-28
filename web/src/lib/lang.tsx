import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { api, setPlainLang } from '../api/client';
import type { PlainLanguage } from '../types';

export const FALLBACK_LANGUAGES: PlainLanguage[] = [
  { code: 'en', name: 'English', native: 'English', dir: 'ltr', bcp47: 'en-IN' },
  { code: 'hi', name: 'Hindi', native: 'हिन्दी', dir: 'ltr', bcp47: 'hi-IN' },
  { code: 'bn', name: 'Bengali', native: 'বাংলা', dir: 'ltr', bcp47: 'bn-IN' },
  { code: 'mr', name: 'Marathi', native: 'मराठी', dir: 'ltr', bcp47: 'mr-IN' },
  { code: 'te', name: 'Telugu', native: 'తెలుగు', dir: 'ltr', bcp47: 'te-IN' },
  { code: 'ta', name: 'Tamil', native: 'தமிழ்', dir: 'ltr', bcp47: 'ta-IN' },
  { code: 'gu', name: 'Gujarati', native: 'ગુજરાતી', dir: 'ltr', bcp47: 'gu-IN' },
  { code: 'ur', name: 'Urdu', native: 'اردو', dir: 'rtl', bcp47: 'ur-IN' },
  { code: 'kn', name: 'Kannada', native: 'ಕನ್ನಡ', dir: 'ltr', bcp47: 'kn-IN' },
  { code: 'or', name: 'Odia', native: 'ଓଡ଼ିଆ', dir: 'ltr', bcp47: 'or-IN' },
  { code: 'ml', name: 'Malayalam', native: 'മലയാളം', dir: 'ltr', bcp47: 'ml-IN' },
  { code: 'pa', name: 'Punjabi', native: 'ਪੰਜਾਬੀ', dir: 'ltr', bcp47: 'pa-IN' },
  { code: 'as', name: 'Assamese', native: 'অসমীয়া', dir: 'ltr', bcp47: 'as-IN' },
  { code: 'mai', name: 'Maithili', native: 'मैथिली', dir: 'ltr', bcp47: 'mai-IN' },
  { code: 'sa', name: 'Sanskrit', native: 'संस्कृतम्', dir: 'ltr', bcp47: 'sa-IN' },
  { code: 'ne', name: 'Nepali', native: 'नेपाली', dir: 'ltr', bcp47: 'ne-NP' },
  { code: 'kok', name: 'Konkani', native: 'कोंकणी', dir: 'ltr', bcp47: 'kok-IN' },
  { code: 'sd', name: 'Sindhi', native: 'سنڌي', dir: 'rtl', bcp47: 'sd-IN' },
  { code: 'doi', name: 'Dogri', native: 'डोगरी', dir: 'ltr', bcp47: 'doi-IN', beta: true },
  { code: 'ks', name: 'Kashmiri', native: 'کٲشُر', dir: 'rtl', bcp47: 'ks-IN', beta: true },
  { code: 'brx', name: 'Bodo', native: 'बड़ो', dir: 'ltr', bcp47: 'brx-IN', beta: true },
  { code: 'mni', name: 'Manipuri', native: 'মৈতৈলোন্', dir: 'ltr', bcp47: 'mni-IN', beta: true },
];

export const EN_LABELS: Record<string, string> = {
  nav_news: "News",
  language: "Language",
  simple_words: "In simple words",
  what_found: "What we found",
  why_matters: "Why it matters",
  not_mean: "What this does not mean",
  where_stands: "Where it stands",
  ask: "A fair question to ask",
  source: "See the original record",
  technical: "Technical details",
  listen: "Listen",
  stop: "Stop",
  share_whatsapp: "Share on WhatsApp",
  copy: "Copy text",
  copied: "Copied",
  today: "Today's edition",
  past: "Past editions",
  live: "Updating through the day",
  final: "Final edition",
  no_voice: "Your device has no reading voice for this language.",
  open_finding: "Open the full finding",
  none_yet: "No editions yet.",
  names_note: "Names of offices, places and schemes are kept as they appear in the original records.",
  feed: "Follow by feed",
  min_read: "{n} min read",
  edition_of: "Edition of {date}",
  copy_prompt: "Copy the line for your agent",
  model_note: "Written in plain words by {model}, then checked by machine against the original numbers.",
};

const STORAGE_KEY = 'awd-lang';

interface LangContextValue {
  lang: string;
  dir: string;
  bcp47: string;
  languages: PlainLanguage[];
  labels: Record<string, string>;
  setLang: (code: string) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
}

const LangContext = createContext<LangContextValue>({
  lang: 'en',
  dir: 'ltr',
  bcp47: 'en-IN',
  languages: FALLBACK_LANGUAGES,
  labels: EN_LABELS,
  setLang: () => {},
  t: (key) => EN_LABELS[key] || key,
});

function readStored(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStored(code: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, code);
  } catch {
    return;
  }
}

export function pickLanguage(candidates: readonly string[], supported: readonly string[]): string {
  for (const raw of candidates) {
    const code = (raw || '').toLowerCase().split(/[-_]/)[0];
    if (supported.includes(code)) return code;
  }
  return 'en';
}

function initialLang(): string {
  const supported = FALLBACK_LANGUAGES.map((l) => l.code);
  const fromUrl = new URLSearchParams(window.location.search).get('lang');
  const stored = readStored();
  const browser = typeof navigator !== 'undefined' ? navigator.languages || [navigator.language] : [];
  return pickLanguage([fromUrl || '', stored || '', ...browser], supported);
}

export function fillLabel(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{([a-z_]+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<string>(() => {
    const code = initialLang();
    setPlainLang(code);
    return code;
  });
  const [languages, setLanguages] = useState<PlainLanguage[]>(FALLBACK_LANGUAGES);
  const [labels, setLabels] = useState<Record<string, string>>(EN_LABELS);

  useEffect(() => {
    let cancelled = false;
    api
      .getPlainLanguages()
      .then((list) => {
        if (!cancelled && list.length) setLanguages(list);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (lang === 'en') {
      setLabels(EN_LABELS);
      return;
    }
    api
      .getPlainLabels(lang)
      .then((l) => {
        if (!cancelled) setLabels({ ...EN_LABELS, ...l });
      })
      .catch(() => {
        if (!cancelled) setLabels(EN_LABELS);
      });
    return () => {
      cancelled = true;
    };
  }, [lang]);

  const meta = languages.find((l) => l.code === lang) || FALLBACK_LANGUAGES[0];

  useEffect(() => {
    document.documentElement.lang = meta.bcp47 || lang;
  }, [meta.bcp47, lang]);

  const setLang = useCallback((code: string) => {
    setPlainLang(code);
    writeStored(code);
    setLangState(code);
  }, []);

  const value = useMemo<LangContextValue>(
    () => ({
      lang,
      dir: meta.dir || 'ltr',
      bcp47: meta.bcp47 || lang,
      languages,
      labels,
      setLang,
      t: (key, vars) => fillLabel(labels[key] || EN_LABELS[key] || key, vars),
    }),
    [lang, meta.dir, meta.bcp47, languages, labels, setLang],
  );

  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useLang(): LangContextValue {
  return useContext(LangContext);
}

export function LanguagePicker({ compact = false }: { compact?: boolean }) {
  const { lang, languages, setLang, t } = useLang();
  return (
    <label className={`lang-picker ${compact ? 'lang-picker-compact' : ''}`}>
      <span className="visually-hidden">{t('language')}</span>
      <select
        value={lang}
        onChange={(e) => setLang(e.target.value)}
        aria-label={t('language')}
        className="lang-picker-select"
      >
        {languages.map((l) => (
          <option key={l.code} value={l.code}>
            {l.beta ? `${l.native} (beta)` : l.native}
          </option>
        ))}
      </select>
    </label>
  );
}
