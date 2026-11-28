import { CHANGELOG_ENTRIES } from '../lib/changelog';
import { formatDateTimeIst, safeExternalUrl } from '../utils';

const REPO = 'https://github.com/aiwatchdogsindia/aiwatchdogs.in';

export function ChangelogPage() {
  return (
    <div className="how changelog-page">
      <header className="how-intro">
        <div className="how-eyebrow">Public release history</div>
        <h1>Changelog</h1>
        <p>
          Every release of AI Watchdogs India, numbered by GitHub pull request with exact date and
          time (IST). For changes to findings, follow the <a href="/feed.atom">RSS feed</a>.
        </p>
      </header>

      <ol className="changelog-list" aria-label="Pull request changelog">
        {CHANGELOG_ENTRIES.map((entry) => {
          const key = entry.pr ? `pr-${entry.pr}` : `commit-${entry.commit || entry.mergedAt}`;
          const refHref = entry.pr
            ? safeExternalUrl(entry.prUrl) || `${REPO}/pull/${entry.pr}`
            : entry.commit
              ? safeExternalUrl(entry.commitUrl) || `${REPO}/commit/${entry.commit}`
              : null;
          const refLabel = entry.pr
            ? `PR #${entry.pr}`
            : entry.commit
              ? `Commit · ${entry.commit}`
              : 'Launch';

          return (
            <li key={key} className="changelog-entry">
              <article>
                <div className="changelog-entry-meta">
                  {refHref ? (
                    <a
                      href={refHref}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={`changelog-pr-badge font-mono${entry.pr ? '' : ' changelog-pr-badge--milestone'}`}
                    >
                      {refLabel}
                    </a>
                  ) : (
                    <span className="changelog-pr-badge changelog-pr-badge--milestone font-mono">
                      {refLabel}
                    </span>
                  )}
                  <time dateTime={entry.mergedAt} className="changelog-time tabular">
                    {formatDateTimeIst(entry.mergedAt)}
                  </time>
                </div>

                <h2 className="changelog-entry-title">{entry.title}</h2>

                <ul className="text-list changelog-items">
                  {entry.items.map((text) => (
                    <li key={text}>{text}</li>
                  ))}
                </ul>
              </article>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
