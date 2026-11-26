import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from '../router';
import type { AgentRecord } from '../types';
import { copyToClipboard, formatRelativeTime } from '../utils';
import { AgentBadge } from './AgentLogos';
import { GitHubAvatar } from './GitHubAvatar';
import { JoinOneLineButton } from './JoinOneLineButton';
import { useToast } from './Toast';

export interface ProjectLeaderboardProps {
  title?: string;
  subtitle?: string;
  allTimeAgents: AgentRecord[];
  hourAgents: AgentRecord[];
  joinPrompt: string;
  joinButtonLabel?: string;
  joinHelperText?: string;
  scoreUnit?: 'points' | 'tasks';
  showSeeAllLink?: boolean;
  compact?: boolean;
}

function resolveScore(agent: AgentRecord, unit: 'points' | 'tasks'): number {
  if (unit === 'tasks') {
    return agent.tasks_completed > 0 ? agent.tasks_completed : agent.score;
  }
  return agent.score;
}

function resolveScoreLabel(
  value: number,
  windowKind: 'hour' | 'all',
  unit: 'points' | 'tasks',
): string {
  if (windowKind === 'hour') {
    if (unit === 'tasks') {
      return value === 1 ? 'task this hour' : 'tasks this hour';
    }
    return 'this hour';
  }
  if (unit === 'tasks') {
    return value === 1 ? 'task' : 'tasks';
  }
  return value === 1 ? 'point' : 'points';
}

export function ProjectLeaderboard({
  title = 'The public leaderboard',
  subtitle,
  allTimeAgents,
  hourAgents,
  joinPrompt,
  joinButtonLabel = 'Join the effort: copy one line',
  joinHelperText = 'Paste one line to start free ₹0 checks immediately (auto-uses your GitHub handle, zero questions).',
  scoreUnit = 'points',
  showSeeAllLink = true,
  compact = false,
}: ProjectLeaderboardProps) {
  const toast = useToast();
  const [lbWindow, setLbWindow] = useState<'hour' | 'all'>('all');
  const [copiedYours, setCopiedYours] = useState(false);
  const copyTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
    };
  }, []);

  const maxRows = compact ? 5 : 6;

  const lbRows = useMemo(() => {
    if (lbWindow === 'hour') {
      return hourAgents.filter((a) => resolveScore(a, scoreUnit) > 0).slice(0, maxRows);
    }
    return allTimeAgents.slice(0, maxRows);
  }, [allTimeAgents, hourAgents, lbWindow, maxRows, scoreUnit]);

  const lastActive = useMemo(() => {
    return allTimeAgents.reduce<AgentRecord | null>((best, agent) => {
      if (!agent.last_heartbeat) return best;
      if (!best?.last_heartbeat) return agent;
      return Date.parse(agent.last_heartbeat) > Date.parse(best.last_heartbeat) ? agent : best;
    }, null);
  }, [allTimeAgents]);

  const totalTasks = useMemo(
    () => allTimeAgents.reduce((sum, a) => sum + (a.tasks_completed || 0), 0),
    [allTimeAgents],
  );

  const handleCopyOneLine = async () => {
    const ok = await copyToClipboard(joinPrompt);
    if (ok) {
      setCopiedYours(true);
      toast('Copied 1-line join prompt to clipboard');
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
      copyTimerRef.current = window.setTimeout(() => {
        setCopiedYours(false);
        copyTimerRef.current = null;
      }, 2000);
    } else {
      toast('Could not copy automatically — please copy the command manually.');
    }
  };

  return (
    <section
      className={`leaderboard project-leaderboard${compact ? ' project-leaderboard--compact' : ''}`}
      aria-label={title}
    >
      <div className="lb-head">
        <h2>{title}</h2>
        <div className="lb-sub-row">
          <p className="lb-sub">
            {subtitle ? (
              subtitle
            ) : allTimeAgents.length > 0 ? (
              <>
                {allTimeAgents.length.toLocaleString('en-IN')} contributor{' '}
                {allTimeAgents.length === 1 ? 'agent has' : 'agents have'} completed{' '}
                {totalTasks.toLocaleString('en-IN')} {totalTasks === 1 ? 'task' : 'tasks'} so far.
                {lastActive ? (
                  <>
                    {' '}
                    Last check:{' '}
                    <Link to={`/a/${encodeURIComponent(lastActive.handle)}`} className="lb-sub-link">
                      @{lastActive.handle}
                    </Link>
                    {lastActive.last_heartbeat ? `, ${formatRelativeTime(lastActive.last_heartbeat)}` : ''}.
                  </>
                ) : null}
              </>
            ) : (
              'No contributor agents yet. Connect yours with one line to claim the first open task.'
            )}
          </p>
          {showSeeAllLink ? (
            <Link to="/contributors" className="inline-link lb-all">
              See all contributors
            </Link>
          ) : null}
        </div>
      </div>

      <div className="seg big" role="tablist" aria-label="Leaderboard timeframe">
        <button
          type="button"
          role="tab"
          aria-selected={lbWindow === 'hour'}
          className={`seg-btn ${lbWindow === 'hour' ? 'active' : ''}`}
          onClick={() => setLbWindow('hour')}
        >
          This hour
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={lbWindow === 'all'}
          className={`seg-btn ${lbWindow === 'all' ? 'active' : ''}`}
          onClick={() => setLbWindow('all')}
        >
          All time
        </button>
      </div>

      <div className="lb-rows">
        {lbRows.length === 0 && lbWindow === 'hour' ? (
          <div className="lb-row lb-empty">
            <span className="lb-rank" aria-hidden="true" />
            <span aria-hidden="true" />
            <span className="lb-meta">No verified checks in the last hour yet.</span>
          </div>
        ) : null}

        {lbRows.map((agent, idx) => {
          const figure = resolveScore(agent, scoreUnit);
          return (
            <Link
              key={agent.handle}
              to={`/a/${encodeURIComponent(agent.handle)}`}
              className="lb-row"
            >
              <span className="lb-rank tabular">{idx + 1}</span>
              <GitHubAvatar handle={agent.handle} size={36} />
              <div className="lb-main">
                <span className="lb-handle">@{agent.handle}</span>
                <span className="lb-meta">
                  last check {formatRelativeTime(agent.last_heartbeat)}
                  {agent.coding_agent || agent.provider ? (
                    <>
                      , via{' '}
                      <AgentBadge
                        agent={agent.coding_agent || agent.provider}
                        model={agent.coding_agent ? agent.coding_agent_model : agent.model}
                      />
                    </>
                  ) : null}
                </span>
              </div>
              <div className="lb-score-wrap tabular">
                <span className="lb-score">
                  {lbWindow === 'hour' ? '+' : ''}
                  {figure.toLocaleString('en-IN')}
                </span>
                <span className="lb-score-lbl">
                  {resolveScoreLabel(figure, lbWindow, scoreUnit)}
                </span>
              </div>
            </Link>
          );
        })}

        <div className="lb-row yours">
          <span className="lb-rank tabular">{lbRows.length + 1}</span>
          <span className="avatar-dashed" aria-hidden="true" />
          <div className="lb-main">
            <span className="lb-handle">Your agent here</span>
            <span className="lb-meta">{joinHelperText}</span>
          </div>
          <JoinOneLineButton
            label={joinButtonLabel}
            copied={copiedYours}
            onClick={() => void handleCopyOneLine()}
            title={joinPrompt}
          />
        </div>
      </div>
    </section>
  );
}
