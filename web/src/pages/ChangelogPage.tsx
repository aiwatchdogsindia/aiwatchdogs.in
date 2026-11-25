import { CHANGELOG } from '../lib/changelog';

const REPO = 'https://github.com/aiwatchdogsindia/aiwatchdogs.in';

export function ChangelogPage() {
  return (
    <div className="how">
      <header className="how-intro">
        <h1>Changelog</h1>
        <p>
          What changed on this site and when. For changes to findings, follow the{' '}
          <a href="/feed.atom">RSS feed</a>.
        </p>
      </header>
      {CHANGELOG.map((day) => (
        <section key={day.date} className="how-section">
          <h2>{day.date}</h2>
          <ul className="text-list">
            {day.items.map((item) => (
              <li key={item.text}>
                {item.text}
                {item.pr ? (
                  <>
                    {' '}
                    <a href={`${REPO}/pull/${item.pr}`} target="_blank" rel="noopener noreferrer">
                      #{item.pr}
                    </a>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
