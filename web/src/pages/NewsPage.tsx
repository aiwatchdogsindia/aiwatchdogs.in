import { Check, ChevronLeft, ChevronRight, Copy, ExternalLink, MessageCircle, Rss, Terminal } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { ListenButton } from '../components/ListenButton';
import { useToast } from '../components/Toast';
import { useLang } from '../lib/lang';
import { Link, useRouter } from '../router';
import type { NewsEdition, NewsSection } from '../types';
import { copyToClipboard, safeExternalUrl } from '../utils';
import '../styles/news.css';

interface NewsPageProps {
  date?: string;
  onSelectFinding: (id: string | number) => void;
}

function CopyLine({ text, label }: { text: string; label: string }) {
  const toast = useToast();
  const { t } = useLang();
  const [done, setDone] = useState(false);
  return (
    <div className="news-prompt">
      <code className="news-prompt-text">{text}</code>
      <button
        type="button"
        className="btn btn-primary btn-sm"
        onClick={async () => {
          if (await copyToClipboard(text)) {
            setDone(true);
            toast(t('copied'));
            window.setTimeout(() => setDone(false), 1800);
          }
        }}
      >
        {done ? <Check size={13} aria-hidden="true" /> : <Terminal size={13} aria-hidden="true" />}
        <span>{done ? t('copied') : label}</span>
      </button>
    </div>
  );
}

function Section({ sec, onSelectFinding }: { sec: NewsSection; onSelectFinding: (id: string | number) => void }) {
  const { t } = useLang();
  return (
    <section className={`news-section news-section-${sec.id}`}>
      <h2 className="news-section-title">{sec.title}</h2>

      {sec.big && (
        <button type="button" className="news-big" onClick={() => sec.finding_id && onSelectFinding(sec.finding_id)}>
          <span className="news-big-num">{sec.big}</span>
          <span className="news-big-text">{sec.text}</span>
        </button>
      )}

      {sec.projects && (
        <>
          {sec.text && <p className="news-muted">{sec.text}</p>}
          <ul className="news-state">
            {sec.projects.map((pr) => (
              <li key={pr.slug} className="news-state-item">
                <div className="news-state-head">
                  <Link to={`/projects/${pr.slug}`} className="news-state-name">
                    {pr.name}
                  </Link>
                </div>
                <p className="news-state-lines">{pr.lines.join(' ')}</p>
                <div className="news-state-links">
                  {pr.finding_id && (
                    <button type="button" className="inline-link" onClick={() => onSelectFinding(pr.finding_id as string)}>
                      {t('open_finding')}
                    </button>
                  )}
                  {safeExternalUrl(pr.source_url) && (
                    <a className="inline-link" href={safeExternalUrl(pr.source_url) as string} target="_blank" rel="noopener noreferrer">
                      {t('source')}
                    </a>
                  )}
                </div>
                <CopyLine text={pr.prompt} label={t('copy_prompt')} />
              </li>
            ))}
          </ul>
        </>
      )}

      {sec.stories?.map((st) => (
        <article key={st.finding_id} className="news-story">
          <button type="button" className="news-story-head" onClick={() => onSelectFinding(st.finding_id)}>
            {st.head}
          </button>
          <p className="news-kicker">{st.project}{st.amount ? ` · ${st.amount}` : ''}</p>
          <p>{st.what}</p>
          <p className="news-muted">{st.why}</p>
          <p className="news-ask">{t('ask')}: {st.ask}</p>
          <p className="news-status">{st.status}</p>
          <div className="news-story-links">
            <button type="button" className="inline-link" onClick={() => onSelectFinding(st.finding_id)}>
              {t('open_finding')}
            </button>
            {safeExternalUrl(st.source_url) && (
              <a className="inline-link" href={safeExternalUrl(st.source_url) as string} target="_blank" rel="noopener noreferrer">
                <ExternalLink size={12} aria-hidden="true" /> {t('source')}
              </a>
            )}
          </div>
        </article>
      ))}

      {sec.lines && sec.lines.length > 0 && (
        <ul className="news-lines">
          {sec.lines.map((ln, i) => (
            <li key={i}>
              {ln.finding_id ? (
                <button type="button" className="inline-link" onClick={() => onSelectFinding(ln.finding_id as string)}>
                  {ln.text}
                </button>
              ) : (
                ln.text
              )}
            </li>
          ))}
        </ul>
      )}

      {sec.heading && <h3 className="news-heading">{sec.heading}</h3>}
      {sec.text && !sec.big && !sec.projects && <p>{sec.text}</p>}
      {sec.note && <p className="news-muted">{sec.note}</p>}
      {sec.prompt && <CopyLine text={sec.prompt} label={t('copy_prompt')} />}
    </section>
  );
}

export function NewsPage({ date, onSelectFinding }: NewsPageProps) {
  const { lang, t } = useLang();
  const { navigate } = useRouter();
  const toast = useToast();
  const [edition, setEdition] = useState<NewsEdition | null>(null);
  const [dates, setDates] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getNewsEditions().then((r) => setDates(r.dates)).catch(() => setDates([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getNews(date)
      .then((r) => {
        if (!cancelled) setEdition(r);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load the edition');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [date, lang]);

  const idx = edition ? dates.indexOf(edition.date) : -1;
  const newer = idx > 0 ? dates[idx - 1] : null;
  const older = idx >= 0 && idx < dates.length - 1 ? dates[idx + 1] : null;
  const shareHref = edition ? `https://wa.me/?text=${encodeURIComponent(edition.share_text)}` : '';
  const feedHref = lang === 'en' ? '/news.atom' : `/news.atom?lang=${lang}`;

  return (
    <div className="container news-page" style={{ paddingTop: 28, paddingBottom: 48 }}>
      {loading && !edition && <div className="state-banner">…</div>}
      {error && <div className="state-banner">{error}</div>}

      {edition && (
        <article className="news-edition" dir={edition.dir} lang={edition.bcp47}>
          <header className="news-masthead">
            <div className="news-meta">
              <span>{edition.edition_label}</span>
              <span>·</span>
              <span>{edition.reading_label}</span>
              <span>·</span>
              <span className={`news-state-tag news-state-${edition.state}`}>
                {edition.state === 'live' ? t('live') : t('final')}
              </span>
            </div>
            <h1 className="news-title font-serif">{edition.title}</h1>
            <p className="news-tagline">{edition.tagline}</p>
            <div className="news-actions">
              <ListenButton text={edition.speak} className="btn btn-primary btn-sm" />
              <a className="btn btn-secondary btn-sm" href={shareHref} target="_blank" rel="noopener noreferrer">
                <MessageCircle size={13} aria-hidden="true" />
                <span>{t('share_whatsapp')}</span>
              </a>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={async () => {
                  if (await copyToClipboard(edition.share_text)) toast(t('copied'));
                }}
              >
                <Copy size={13} aria-hidden="true" />
                <span>{t('copy')}</span>
              </button>
              <a className="btn btn-ghost btn-sm" href={feedHref}>
                <Rss size={13} aria-hidden="true" />
                <span>{t('feed')}</span>
              </a>
            </div>
          </header>

          {edition.lede && <p className="news-lede">{edition.lede}</p>}
          <p className={edition.lede ? 'news-muted' : 'news-lede'}>{edition.intro}</p>

          {edition.sections.map((sec) => (
            <Section key={sec.id} sec={sec} onSelectFinding={onSelectFinding} />
          ))}

          <footer className="news-footer">
            <p>{edition.footer}</p>
            {edition.lede && edition.lede_model && <p className="news-muted">{t('model_note', { model: edition.lede_model })}</p>}
            {edition.lang !== 'en' && <p className="news-muted">{t('names_note')}</p>}
            <p className="font-mono news-fingerprint">{edition.fingerprint_label}</p>
          </footer>

          <nav className="news-nav" aria-label={t('past')}>
            <button type="button" className="btn btn-ghost btn-sm" disabled={!older} onClick={() => older && navigate(`/news/${older}`)}>
              <ChevronLeft size={14} aria-hidden="true" />
              <span>{older || ''}</span>
            </button>
            {dates.length > 1 && (
              <select
                className="select-field"
                value={edition.date}
                aria-label={t('past')}
                onChange={(e) => navigate(e.target.value === dates[0] ? '/news' : `/news/${e.target.value}`)}
              >
                {dates.map((d) => (
                  <option key={d} value={d}>
                    {d === dates[0] ? `${t('today')} · ${d}` : d}
                  </option>
                ))}
              </select>
            )}
            <button type="button" className="btn btn-ghost btn-sm" disabled={!newer} onClick={() => newer && navigate(newer === dates[0] ? '/news' : `/news/${newer}`)}>
              <span>{newer || ''}</span>
              <ChevronRight size={14} aria-hidden="true" />
            </button>
          </nav>
        </article>
      )}
    </div>
  );
}
