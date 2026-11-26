import { useMemo } from 'react';
import type { AgentRecord } from '../types';
import { PublicLeaderboard } from './PublicLeaderboard';

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

export function ProjectLeaderboard({
  title = 'The public leaderboard',
  subtitle,
  allTimeAgents,
  hourAgents,
  joinPrompt,
  joinButtonLabel = 'Join the effort: copy one line',
  joinHelperText = 'Paste one line to start free ₹0 checks (auto-uses your public GitHub handle).',
  scoreUnit = 'points',
  showSeeAllLink = true,
  compact = false,
}: ProjectLeaderboardProps) {
  const mergedContributors = useMemo(() => {
    const hourByHandle = new Map<string, AgentRecord>();
    for (const agent of hourAgents) {
      const key = (agent.handle || '').trim().toLowerCase();
      if (key && !hourByHandle.has(key)) {
        hourByHandle.set(key, agent);
      }
    }

    const seen = new Set<string>();
    const out: AgentRecord[] = [];

    for (const agent of allTimeAgents) {
      const key = (agent.handle || '').trim().toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      const hr = hourByHandle.get(key);
      out.push({
        ...agent,
        score_10m: agent.score_10m ?? hr?.score_10m,
        score_1h: agent.score_1h ?? agent.hourly_score ?? hr?.score_1h ?? hr?.hourly_score ?? hr?.score ?? 0,
        checks_10m: agent.checks_10m ?? hr?.checks_10m,
        checks_1h: agent.checks_1h ?? hr?.checks_1h ?? hr?.tasks_completed ?? 0,
        checks_total: agent.checks_total ?? agent.tasks_completed,
        hourly_points_36h: agent.hourly_points_36h ?? hr?.hourly_points_36h,
        hourly_checks_36h: agent.hourly_checks_36h ?? hr?.hourly_checks_36h,
      });
    }

    for (const hr of hourAgents) {
      const key = (hr.handle || '').trim().toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push({
        ...hr,
        score_1h: hr.score_1h ?? hr.hourly_score ?? hr.score,
        checks_1h: hr.checks_1h ?? hr.tasks_completed,
        checks_total: hr.checks_total ?? hr.tasks_completed,
      });
    }

    return out;
  }, [allTimeAgents, hourAgents]);

  const totalTasks = useMemo(
    () =>
      mergedContributors.reduce(
        (sum, a) => sum + Math.max(0, Number(a.checks_total ?? a.tasks_completed ?? 0) || 0),
        0,
      ),
    [mergedContributors],
  );

  return (
    <PublicLeaderboard
      title={title}
      subtitle={subtitle}
      contributors={mergedContributors}
      totalContributors={mergedContributors.length}
      totalChecks={totalTasks}
      maxRows={compact ? 5 : 10}
      defaultMetric={scoreUnit === 'tasks' ? 'checks' : 'points'}
      showSeeAllLink={showSeeAllLink}
      joinPrompt={joinPrompt}
      joinButtonLabel={joinButtonLabel}
      joinHelperText={joinHelperText}
      compact={compact}
      className={`project-leaderboard${compact ? ' project-leaderboard--compact' : ''}`}
      ariaLabel={title}
    />
  );
}
