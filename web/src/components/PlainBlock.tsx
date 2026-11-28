import { ExternalLink } from 'lucide-react';
import { useLang } from '../lib/lang';
import type { PlainCard } from '../types';
import { safeExternalUrl } from '../utils';
import { ListenButton } from './ListenButton';

export function PlainBlock({ plain, sourceUrl }: { plain: PlainCard; sourceUrl?: string }) {
  const { t } = useLang();
  const href = safeExternalUrl(sourceUrl);
  return (
    <section className="plain-block" dir={plain.dir} lang={plain.lang} aria-label={t('simple_words')}>
      <div className="plain-block-top">
        <span className="plain-kicker">{t('simple_words')}</span>
        <span className="plain-project">{plain.project}</span>
      </div>
      <p className="plain-head">{plain.head}</p>
      <h4 className="plain-label">{t('what_found')}</h4>
      <p>{plain.what}</p>
      <h4 className="plain-label">{t('why_matters')}</h4>
      <p>{plain.why}</p>
      <h4 className="plain-label">{t('where_stands')}</h4>
      <p>{plain.status}</p>
      <h4 className="plain-label">{t('not_mean')}</h4>
      <p>{plain.not}</p>
      <h4 className="plain-label">{t('ask')}</h4>
      <p className="plain-ask">{plain.ask}</p>
      <div className="plain-actions">
        <ListenButton text={plain.speak} />
        {href && (
          <a className="btn btn-ghost btn-sm" href={href} target="_blank" rel="noopener noreferrer">
            <ExternalLink size={13} aria-hidden="true" />
            <span>{t('source')}</span>
          </a>
        )}
      </div>
      {plain.source === 'model' && plain.model && (
        <p className="plain-note">{t('model_note', { model: plain.model })}</p>
      )}
      {plain.lang !== 'en' && <p className="plain-note">{t('names_note')}</p>}
    </section>
  );
}
