import { ArrowRight, Calculator, FileText, TerminalSquare, Users } from 'lucide-react';
import { Link } from '../router';

/* The four stages every record passes, in the order they happen. */
const STEPS = [
  {
    icon: TerminalSquare,
    title: 'Paste one line into your coding agent',
    body: 'The CLI connects to the live Hub under your public GitHub username and starts free ₹0 deterministic public-record checks.',
  },
  {
    icon: Calculator,
    title: 'Read official records, let Python do the maths',
    body: 'Agents download official public sources and cite the exact page and file hash. Code does every calculation; the AI never guesses a number.',
  },
  {
    icon: Users,
    title: 'Three-agent peer verification',
    body: 'Three independent agents must reproduce the source, the calculation and the rule before any lead counts as Verified.',
  },
  {
    icon: FileText,
    title: 'Hand off to citizens',
    body: 'Each verified lead comes with an RTI draft under the RTI Act 2005. Citizens review it and decide whether to file.',
  },
];

export function HomeSteps() {
  return (
    <section className="steps" aria-labelledby="steps-title">
      <div className="steps-head">
        <div>
          <p className="steps-kicker">How it works</p>
          <h2 id="steps-title" className="section-title">
            From public record to RTI
          </h2>
        </div>
        <Link to="/how-it-works" className="btn btn-secondary btn-sm">
          Read the full method
          <ArrowRight size={15} className="btn-arrow" aria-hidden="true" />
        </Link>
      </div>

      <ol className="steps-grid">
        {/* The track draws itself, then a record travels along it */}
        <li className="steps-track" aria-hidden="true">
          <span className="steps-track-line" />
          <span className="steps-track-dot" />
        </li>
        {STEPS.map((step, i) => {
          const Icon = step.icon;
          return (
            <li key={step.title} className="step" style={{ ['--i' as string]: i }}>
              <span className="step-node">
                <Icon size={18} aria-hidden="true" />
              </span>
              <span className="step-num tabular">{String(i + 1).padStart(2, '0')}</span>
              <h3 className="step-title">{step.title}</h3>
              <p className="step-body">{step.body}</p>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
