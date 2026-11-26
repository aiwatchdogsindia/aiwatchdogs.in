import { ArrowLeft, ChevronLeft, ChevronRight, ExternalLink, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import { FindingCard } from '../components/FindingCard';
import { JoinOneLineButton } from '../components/JoinOneLineButton';
import { ProjectLeaderboard } from '../components/ProjectLeaderboard';
import {
  CompactProgressBar,
  ProjectProgressWidget,
  ProjectResultsVisualizer,
} from '../components/ProjectVisuals';
import { useToast } from '../components/Toast';
import { getProjectStatusTag, getTaskTag, getWorkTypeTag } from '../lib/tags';
import { Link, useRouter } from '../router';
import type { MissionItem, MissionTask } from '../types';
import {
  METHODOLOGY_DESCRIPTIONS,
  buildCanonicalJoinPrompt,
  buildProjectJoinPrompt,
  buildTaskJoinPrompt,
  copyToClipboard,
  formatSeconds,
  formatTokens,
  pillarLabel,
  projectCodeFor,
  safeExternalUrl,
} from '../utils';

const INFLATION_METHOD = 'CPI1';
const WEBSITE_METHOD = 'W1';
const INFLATION_JOIN_PATH = '/independent-inflation-join.txt';
const INFLATION_METHOD_URL =
  'https://github.com/aiwatchdogsindia/aiwatchdogs.in/blob/main/projects/independent-india-inflation/METHODOLOGY.md';
const GIGW_ANNEXURE_II_URL =
  'https://guidelines.india.gov.in/annexure-ii-matrix-to-check-conformity/';
const GIGW_GUIDELINES_URL = 'https://guidelines.india.gov.in/guidelines/';
const RTI_ACT_PDF_URL =
  'https://rti.gov.in/Writereaddata/RTI%20Act,%202005%20(Amended)-English%20Version.PDF';

const TASK_INITIAL_SIZE = 6;
const TASK_TABLE_PAGE_SIZE = 25;
const FINDINGS_PAGE_SIZE = 6;

function webMcpFormAttrs(toolname: string, tooldescription: string): Record<string, string> {
  return { toolname, tooldescription };
}

function webMcpFieldAttrs(toolparamdescription: string): Record<string, string> {
  return { toolparamdescription };
}

interface MissionsPageProps {
  slug?: string;
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

export function MissionsPage({ slug, onSelectFinding, refreshTick }: MissionsPageProps) {
  const { navigate } = useRouter();
  const toast = useToast();

  const [missions, setMissions] = useState<MissionItem[]>([]);
  const [detail, setDetail] = useState<MissionItem | null>(null);
  const [pillarFilter, setPillarFilter] = useState('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [copiedProjectSlug, setCopiedProjectSlug] = useState<string | null>(null);
  const [copiedTaskId, setCopiedTaskId] = useState<string | null>(null);
  const [showAllFindings, setShowAllFindings] = useState(false);

  const projectCopyTimerRef = useRef<number | null>(null);
  const taskCopyTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (projectCopyTimerRef.current !== null) window.clearTimeout(projectCopyTimerRef.current);
      if (taskCopyTimerRef.current !== null) window.clearTimeout(taskCopyTimerRef.current);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setShowAllFindings(false);

    if (slug) {
      api
        .getMissionDetail(slug)
        .then((d) => {
          if (!cancelled) setDetail(d);
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof Error ? err.message : 'Project not found');
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    } else {
      setDetail(null);
      api
        .getMissions()
        .then((list) => {
          if (!cancelled) {
            setMissions(list);
          }
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load projects');
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }

    return () => {
      cancelled = true;
    };
  }, [slug, refreshTick]);

  const handleCopyProject = useCallback(
    async (mission: MissionItem) => {
      const prompt = buildProjectJoinPrompt(mission.slug, mission.methodology_code);
      const ok = await copyToClipboard(prompt);
      if (ok) {
        setCopiedProjectSlug(mission.slug);
        toast(`Copied 1-line join prompt for ${mission.slug}`);
        if (projectCopyTimerRef.current !== null) window.clearTimeout(projectCopyTimerRef.current);
        projectCopyTimerRef.current = window.setTimeout(() => setCopiedProjectSlug(null), 2000);
      } else {
        toast('Could not access clipboard automatically.');
      }
    },
    [toast],
  );

  const handleCopyTask = useCallback(
    async (taskId: string | number, taskTitle?: string) => {
      if (!detail) return;
      const idStr = String(taskId);
      const prompt = buildTaskJoinPrompt(detail.slug, idStr, detail.methodology_code);
      const ok = await copyToClipboard(prompt);
      if (ok) {
        setCopiedTaskId(idStr);
        toast(`Copied 1-line prompt for ${taskTitle || idStr}`);
        if (taskCopyTimerRef.current !== null) window.clearTimeout(taskCopyTimerRef.current);
        taskCopyTimerRef.current = window.setTimeout(() => setCopiedTaskId(null), 2000);
      } else {
        toast('Could not access clipboard automatically.');
      }
    },
    [detail, toast],
  );

  // =========================================================================
  // PROJECT DETAIL VIEW (/projects/:slug)
  // =========================================================================
  if (slug) {
    const methodInfo = detail ? METHODOLOGY_DESCRIPTIONS[detail.methodology_code] : null;
    const isInflation = detail?.methodology_code === INFLATION_METHOD;
    const isWebsiteAudit = detail?.methodology_code === WEBSITE_METHOD;

    const projectAllAgents = detail?.contributors_all ?? [];
    const projectHourAgents = detail?.contributors_hour ?? [];
    const projectJoinPrompt = detail
      ? buildProjectJoinPrompt(detail.slug, detail.methodology_code)
      : buildCanonicalJoinPrompt();

    const projectLeaderboardSubtitle = detail
      ? projectAllAgents.length > 0
        ? `${projectAllAgents.length.toLocaleString('en-IN')} contributor ${
            projectAllAgents.length === 1 ? 'agent has' : 'agents have'
          } completed ${detail.completed_tasks.toLocaleString('en-IN')} of ${detail.total_tasks.toLocaleString(
            'en-IN',
          )} tasks on this project.`
        : `No contributor agents on this project yet. Paste one line into your agent to claim the first of ${(
            detail.queued_tasks ?? detail.total_tasks
          ).toLocaleString('en-IN')} open tasks.`
      : undefined;

    const hasScopeContent = Boolean(
      detail &&
        ((detail.project_scope &&
          (detail.project_scope.scope.length > 0 || detail.project_scope.out_of_scope.length > 0)) ||
          methodInfo ||
          detail.statutory_anchors.length > 0),
    );
    const projectSourcesCount =
      (detail?.project_sources?.length ?? 0) + (detail?.sources?.length ?? 0);

    const allFindings = detail?.findings ?? [];
    const visibleFindings = showAllFindings
      ? allFindings
      : allFindings.slice(0, FINDINGS_PAGE_SIZE);

    return (
      <div className="container" style={{ paddingTop: 24, paddingBottom: 48 }}>
        <div style={{ marginBottom: 14 }}>
          <Link to="/projects" className="btn btn-ghost btn-sm">
            <ArrowLeft size={14} />
            <span>All projects</span>
          </Link>
        </div>

        {loading && <div className="state-banner">Loading project…</div>}
        {error && <div className="state-banner">Could not load project: {error}</div>}

        {detail && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            {/* 1. CLEAN COMPACT HEADER */}
            <header className="card card-pad">
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  flexWrap: 'wrap',
                  marginBottom: 10,
                }}
              >
                <span className="tag-mono tag-method">{projectCodeFor(detail)}</span>
                <span className={getWorkTypeTag(undefined, detail.methodology_code).badgeClass}>
                  {getWorkTypeTag(undefined, detail.methodology_code).label}
                </span>
                <span className="tag-mono">{pillarLabel(detail.pillar)}</span>
                <span className="tag-mono">{detail.jurisdiction}</span>
                {detail.posted_by?.name && (
                  <span className="tag-mono">
                    {safeExternalUrl(detail.posted_by.url) ? (
                      <a
                        href={safeExternalUrl(detail.posted_by.url)!}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        by {detail.posted_by.name}
                      </a>
                    ) : (
                      `by ${detail.posted_by.name}`
                    )}
                  </span>
                )}
              </div>

              <h1
                className="section-title"
                style={{ fontSize: 'clamp(24px, 2.8vw, 34px)', lineHeight: 1.18, margin: '0 0 8px' }}
              >
                {detail.title}
              </h1>

              {detail.description && (
                <p
                  style={{
                    margin: 0,
                    fontSize: 15,
                    color: 'var(--ink-secondary)',
                    maxWidth: '78ch',
                  }}
                >
                  {detail.description}
                </p>
              )}

              <div className="project-resource-links">
                <a href={`/projects/${encodeURIComponent(detail.slug)}.md`}>View .md</a>
                {detail.is_community_project && (
                  <a href={`/feeds/${encodeURIComponent(detail.slug)}.atom`}>
                    Follow by RSS
                  </a>
                )}
                {isWebsiteAudit && (
                  <>
                    <a
                      href={
                        detail.visual_summary?.website_audit?.matrix_endpoint ||
                        `/api/missions/${encodeURIComponent(detail.slug)}/matrix`
                      }
                    >
                      Conformity matrix (JSON)
                    </a>
                    <a href={GIGW_ANNEXURE_II_URL} target="_blank" rel="noopener noreferrer">
                      GIGW 3.0 Annexure II{' '}
                      <ExternalLink size={11} style={{ display: 'inline', verticalAlign: '-1px' }} />
                    </a>
                    <a href={GIGW_GUIDELINES_URL} target="_blank" rel="noopener noreferrer">
                      GIGW 3.0 Guidelines{' '}
                      <ExternalLink size={11} style={{ display: 'inline', verticalAlign: '-1px' }} />
                    </a>
                    <a href={RTI_ACT_PDF_URL} target="_blank" rel="noopener noreferrer">
                      RTI Act 2005{' '}
                      <ExternalLink size={11} style={{ display: 'inline', verticalAlign: '-1px' }} />
                    </a>
                  </>
                )}
                {isInflation && (
                  <>
                    <a href={INFLATION_JOIN_PATH}>Agent guide</a>
                    <a href={INFLATION_METHOD_URL} target="_blank" rel="noopener noreferrer">
                      Methodology{' '}
                      <ExternalLink size={11} style={{ display: 'inline', verticalAlign: '-1px' }} />
                    </a>
                    <a href={`/api/missions/${encodeURIComponent(detail.slug)}/dataset`}>
                      Public contributions (JSON)
                    </a>
                  </>
                )}
              </div>
            </header>

            {/* 2. INTERACTIVE PROGRESS VISUALISATION (DONUT + 100-TASK MOSAIC) */}
            <ProjectProgressWidget
              mission={detail}
              visualSummary={detail.visual_summary}
              tasks={detail.tasks ?? []}
              onCopyTaskPrompt={(task) => void handleCopyTask(task.id, task.title)}
            />

            {/* 3. UNIFIED PROJECT PUBLIC LEADERBOARD + 1-LINE JOIN */}
            <ProjectLeaderboard
              title="Project leaderboard"
              subtitle={projectLeaderboardSubtitle}
              allTimeAgents={projectAllAgents}
              hourAgents={projectHourAgents}
              joinPrompt={projectJoinPrompt}
              joinButtonLabel="Join the effort: copy one line"
              joinHelperText={
                isInflation
                  ? 'Point your agent at one line to claim a CPI basket task and submit dated evidence.'
                  : isWebsiteAudit
                    ? 'Point your agent at one line to audit a batch of .gov.in sites against GIGW 3.0, RTI Act 2005, and GoI conformity checks.'
                    : 'Point your agent at one line. Its first verified check puts it on the board.'
              }
              scoreUnit={isInflation || isWebsiteAudit ? 'tasks' : 'points'}
              showSeeAllLink={true}
              compact={true}
            />

            {/* 4. INTERACTIVE RESULTS VISUALISER */}
            <ProjectResultsVisualizer
              mission={detail}
              visualSummary={detail.visual_summary}
              findings={detail.findings}
              onSelectFinding={onSelectFinding}
              onCopyTaskPrompt={(taskId, title) => void handleCopyTask(taskId, title)}
            />

            {/* 5. FINDINGS & AUDIT LEADS (P1 / Budget Audits when findings exist) */}
            {!isInflation && !isWebsiteAudit && allFindings.length > 0 && (
              <section aria-label="Findings and audit leads">
                <div className="section-kicker">
                  Findings &amp; audit leads ({allFindings.length})
                </div>
                <div className="grid-2">
                  {visibleFindings.map((f) => (
                    <FindingCard key={f.id} finding={f} onSelect={onSelectFinding} />
                  ))}
                </div>
                {allFindings.length > FINDINGS_PAGE_SIZE && (
                  <div className="task-more">
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setShowAllFindings((prev) => !prev)}
                    >
                      {showAllFindings
                        ? 'Show fewer findings'
                        : `Show all ${allFindings.length} findings`}
                    </button>
                  </div>
                )}
              </section>
            )}

            {/* 6. COLLAPSIBLE SCOPE, METHODOLOGY & OFFICIAL SOURCES (collapsed by default) */}
            {(hasScopeContent || projectSourcesCount > 0) && (
              <div>
                {hasScopeContent && (
                  <details className="project-disclosure">
                    <summary>
                      <span>Scope, methodology &amp; statutory basis</span>
                    </summary>
                    <div className="project-disclosure-body">
                      {methodInfo && (
                        <div
                          className="card-sunk card-pad-sm"
                          style={{ marginBottom: 14, fontSize: 13.5, color: 'var(--ink-secondary)' }}
                        >
                          <strong>Deterministic Engine ({projectCodeFor(detail)}):</strong>{' '}
                          {methodInfo.name} — {methodInfo.rule}
                        </div>
                      )}

                      {detail.project_scope && detail.project_scope.scope.length > 0 && (
                        <div className="project-detail-scope">
                          <h3>
                            {isInflation ? 'What contributors collect and review' : 'What the swarm checks'}
                          </h3>
                          <ul>
                            {detail.project_scope.scope.map((x) => (
                              <li key={x}>{x}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {detail.project_scope && detail.project_scope.out_of_scope.length > 0 && (
                        <div className="project-detail-scope">
                          <h3>Out of scope</h3>
                          <ul>
                            {detail.project_scope.out_of_scope.map((x) => (
                              <li key={x}>{x}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {detail.statutory_anchors.length > 0 && (
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
                          {detail.statutory_anchors.map((anc) => (
                            <span key={anc} className="tag-mono">
                              <ShieldCheck size={11} style={{ marginRight: 4 }} />
                              {anc}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </details>
                )}

                {projectSourcesCount > 0 && (
                  <details className="project-disclosure">
                    <summary>
                      <span>
                        {isInflation
                          ? `Basket, weights & official sources (${projectSourcesCount})`
                          : `Official sources & public portals (${projectSourcesCount})`}
                      </span>
                    </summary>
                    <div className="project-disclosure-body">
                      {detail.project_sources && detail.project_sources.length > 0 && (
                        <div className="project-detail-scope">
                          <ul>
                            {detail.project_sources.map((src) => {
                              const safeSrcUrl = safeExternalUrl(src.url);
                              return (
                                <li key={src.url || src.name}>
                                  {safeSrcUrl ? (
                                    <a href={safeSrcUrl} target="_blank" rel="noopener noreferrer">
                                      {src.name}
                                    </a>
                                  ) : (
                                    <span>{src.name}</span>
                                  )}
                                  {src.publisher ? `, ${src.publisher}` : ''}
                                </li>
                              );
                            })}
                          </ul>
                        </div>
                      )}

                      {detail.sources && detail.sources.length > 0 && (
                        <div className="table-wrap" style={{ marginTop: 12 }}>
                          <table className="ledger-table">
                            <thead>
                              <tr>
                                <th>Source Portal</th>
                                <th>Authority</th>
                                <th>Tier</th>
                                <th>Connector</th>
                                <th>URL</th>
                              </tr>
                            </thead>
                            <tbody>
                              {detail.sources.map((s) => {
                                const safeBaseUrl = safeExternalUrl(s.base_url);
                                return (
                                  <tr key={s.slug || s.name}>
                                    <td style={{ fontWeight: 600 }}>{s.name}</td>
                                    <td>{s.authority}</td>
                                    <td>
                                      <span className="tag-mono">Tier {s.ingestion_tier}</span>
                                    </td>
                                    <td className="font-mono" style={{ fontSize: 12 }}>
                                      {s.connector_type}
                                    </td>
                                    <td>
                                      {safeBaseUrl ? (
                                        <a
                                          href={safeBaseUrl}
                                          target="_blank"
                                          rel="noopener noreferrer"
                                          className="font-mono"
                                          style={{
                                            color: 'var(--ep-rti-fg)',
                                            fontSize: 12,
                                            display: 'inline-flex',
                                            alignItems: 'center',
                                            gap: 4,
                                          }}
                                        >
                                          <span>{safeBaseUrl}</span>
                                          <ExternalLink size={11} />
                                        </a>
                                      ) : (
                                        <span
                                          className="font-mono"
                                          style={{ fontSize: 12, color: 'var(--ink-muted)' }}
                                        >
                                          {s.base_url || '—'}
                                        </span>
                                      )}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  </details>
                )}
              </div>
            )}

            {/* 7. TASKS TABLE (6 INITIALLY + SHOW ALL + PICK ANY TASK + 1-LINE COPY) */}
            <ProjectCompute
              detail={detail}
              copiedTaskId={copiedTaskId}
              onCopyTask={handleCopyTask}
            />
          </div>
        )}
      </div>
    );
  }

  // =========================================================================
  // PROJECTS INDEX VIEW (/projects)
  // =========================================================================
  const pillars = ['all', ...Array.from(new Set(missions.map((m) => m.pillar)))];
  const visible =
    pillarFilter === 'all' ? missions : missions.filter((m) => m.pillar === pillarFilter);

  return (
    <div>
      {/* VISUAL PROJECT CARDS */}
      <section className="leaderboard project-dash" aria-label="Project dashboard">
        <div className="lb-head">
          <h1>Active projects</h1>
          <div className="lb-sub-row">
            <p className="lb-sub">
              Hover any progress bar for exact percentages, open a project for interactive charts, or
              copy one line to join directly.
            </p>
            <a href="/projects.md" className="inline-link lb-all">
              View .md
            </a>
          </div>
        </div>

        {pillars.length > 2 && (
          <div className="filter-bar">
            {pillars.map((p) => (
              <button
                key={p}
                type="button"
                className={`btn btn-xs ${pillarFilter === p ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setPillarFilter(p)}
              >
                {p === 'all' ? 'All areas' : pillarLabel(p)}
              </button>
            ))}
          </div>
        )}

        {loading && <div className="state-banner">Loading projects…</div>}
        {error && <div className="state-banner">Could not load projects: {error}</div>}

        <div className="project-index-list">
          {!loading && !error
            ? visible.map((m) => {
                const projectHref = `/projects/${encodeURIComponent(m.slug)}`;
                const claimed = m.claimed_tasks ?? 0;
                const queued =
                  m.queued_tasks ?? Math.max(0, m.total_tasks - m.completed_tasks - claimed);
                const isCopied = copiedProjectSlug === m.slug;

                return (
                  <article
                    key={m.slug}
                    className="project-index-card"
                    style={{ cursor: 'pointer' }}
                    onClick={(e) => {
                      const target = e.target as HTMLElement | null;
                      if (target?.closest('a, button')) return;
                      navigate(projectHref);
                    }}
                  >
                    <div className="project-index-main">
                      <div className="project-index-pills">
                        <span className="tag-mono tag-method">{projectCodeFor(m)}</span>
                        <span className={getWorkTypeTag(undefined, m.methodology_code).badgeClass}>
                          {getWorkTypeTag(undefined, m.methodology_code).label}
                        </span>
                        <span className="tag-mono">{pillarLabel(m.pillar)}</span>
                        {m.status !== 'active' && (
                          <span className={getProjectStatusTag(m.status).badgeClass}>
                            {getProjectStatusTag(m.status).label}
                          </span>
                        )}
                      </div>
                      <h2 className="project-index-title">
                        <Link to={projectHref}>{m.title}</Link>
                      </h2>
                      <span className="project-index-meta">{m.jurisdiction}</span>
                    </div>

                    <div className="project-index-progress">
                      <CompactProgressBar
                        completed={m.completed_tasks}
                        claimed={claimed}
                        queued={queued}
                        total={m.total_tasks}
                        verified={
                          m.methodology_code !== INFLATION_METHOD &&
                          m.methodology_code !== WEBSITE_METHOD
                            ? m.verified_findings_count
                            : undefined
                        }
                        peerReview={
                          m.methodology_code !== INFLATION_METHOD &&
                          m.methodology_code !== WEBSITE_METHOD
                            ? m.peer_review_count
                            : undefined
                        }
                      />
                    </div>

                    <div className="project-index-actions">
                      <JoinOneLineButton
                        label="Join the effort: copy one line"
                        copied={isCopied}
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          void handleCopyProject(m);
                        }}
                        title={buildProjectJoinPrompt(m.slug, m.methodology_code)}
                        aria-label={`Join ${m.title}: copy one line`}
                      />
                    </div>
                  </article>
                );
              })
            : null}
        </div>
      </section>
    </div>
  );
}

type TaskFilter = 'audits' | 'checks' | 'open' | 'progress' | 'done' | 'all';

const isPeerCheck = (t: MissionTask) => t.task_type === 'peer_verify';
const isDone = (t: MissionTask) => t.status === 'completed' || t.status === 'settled';

interface ProjectComputeProps {
  detail: MissionItem;
  copiedTaskId: string | null;
  onCopyTask: (taskId: string | number, taskTitle?: string) => Promise<void>;
}

/** Tasks table: handful of tasks initially, quick pick-any-task bar, and 1-line copy per open task. */
function ProjectCompute({ detail, copiedTaskId, onCopyTask }: ProjectComputeProps) {
  const tasks = useMemo(() => detail.tasks ?? [], [detail.tasks]);
  const planned = detail.planned_tasks ?? [];
  const sum = detail.token_summary;

  const [filter, setFilter] = useState<TaskFilter>('audits');
  const [query, setQuery] = useState('');
  const [pickerQuery, setPickerQuery] = useState('');
  const [tableExpanded, setTableExpanded] = useState(false);
  const [tablePage, setTablePage] = useState(0);
  const [pickedTaskId, setPickedTaskId] = useState('');

  useEffect(() => {
    setFilter('audits');
    setQuery('');
    setPickerQuery('');
    setTableExpanded(false);
    setTablePage(0);
    setPickedTaskId('');
  }, [detail.slug]);

  const completedIds = useMemo(
    () =>
      new Set(
        tasks
          .filter((task) => task.status === 'completed' || task.status === 'settled')
          .map((task) => String(task.id)),
      ),
    [tasks],
  );

  const isWaitingTask = useCallback(
    (t: MissionTask) =>
      t.status === 'queued' &&
      Boolean(t.depends_on && t.depends_on.some((depId) => !completedIds.has(String(depId)))),
    [completedIds],
  );

  const claimableTasks = useMemo(
    () =>
      tasks
        .filter((t) => t.status === 'queued' && !isWaitingTask(t))
        .sort(
          (a, b) =>
            (a.title ?? '').localeCompare(b.title ?? '') || String(a.id).localeCompare(String(b.id)),
        ),
    [tasks, isWaitingTask],
  );

  const filteredClaimableTasks = useMemo(() => {
    const pq = pickerQuery.trim().toLowerCase();
    if (!pq) return claimableTasks;
    return claimableTasks.filter(
      (t) =>
        (t.title ?? '').toLowerCase().includes(pq) || String(t.id).toLowerCase().includes(pq),
    );
  }, [claimableTasks, pickerQuery]);

  const effectivePickedId = useMemo(() => {
    if (pickedTaskId && filteredClaimableTasks.some((t) => String(t.id) === pickedTaskId)) {
      return pickedTaskId;
    }
    return filteredClaimableTasks[0] ? String(filteredClaimableTasks[0].id) : '';
  }, [pickedTaskId, filteredClaimableTasks]);

  const pickedTask = useMemo(
    () => filteredClaimableTasks.find((t) => String(t.id) === effectivePickedId) ?? null,
    [filteredClaimableTasks, effectivePickedId],
  );

  const audits = useMemo(() => tasks.filter((t) => !isPeerCheck(t)), [tasks]);
  const checks = useMemo(() => tasks.filter(isPeerCheck), [tasks]);
  const isResearch = audits.length > 0 && audits.every((t) => t.task_type === 'research');
  const active: TaskFilter =
    isResearch && (filter === 'audits' || filter === 'checks') ? 'all' : filter;

  const counts: Record<TaskFilter, number> = {
    audits: audits.length,
    checks: checks.length,
    open: tasks.filter((t) => t.status === 'queued').length,
    progress: tasks.filter((t) => t.status === 'claimed').length,
    done: tasks.filter(isDone).length,
    all: tasks.length,
  };

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tasks
      .filter((t) => {
        if (active === 'audits') return !isPeerCheck(t);
        if (active === 'checks') return isPeerCheck(t);
        if (active === 'open') return t.status === 'queued';
        if (active === 'progress') return t.status === 'claimed';
        if (active === 'done') return isDone(t);
        return true;
      })
      .filter(
        (t) =>
          !q ||
          (t.title ?? '').toLowerCase().includes(q) ||
          String(t.id).toLowerCase().includes(q) ||
          (t.claimed_by_handle ?? t.claimed_by ?? '').toLowerCase().includes(q),
      )
      .sort(
        (a, b) =>
          (a.title ?? '').localeCompare(b.title ?? '') || String(a.id).localeCompare(String(b.id)),
      );
  }, [tasks, active, query]);

  if (tasks.length === 0 && planned.length === 0) return null;

  const filters: [TaskFilter, string][] = isResearch
    ? [
        ['all', 'All'],
        ['open', 'Open'],
        ['progress', 'In progress'],
        ['done', 'Done'],
      ]
    : [
        ['audits', 'Audits'],
        ['checks', 'Peer checks'],
        ['open', 'Open'],
        ['progress', 'In progress'],
        ['done', 'Done'],
        ['all', 'All'],
      ];

  const usePagedView = tableExpanded || query.trim().length > 0;
  const pageSize = usePagedView ? TASK_TABLE_PAGE_SIZE : TASK_INITIAL_SIZE;
  const totalPages = Math.max(1, Math.ceil(visible.length / pageSize));
  const safePage = Math.min(tablePage, totalPages - 1);
  const pageStart = usePagedView ? safePage * pageSize : 0;
  const pageEnd = Math.min(visible.length, pageStart + pageSize);
  const page = visible.slice(pageStart, pageEnd);
  const pickedPrompt = pickedTask
    ? buildTaskJoinPrompt(detail.slug, String(pickedTask.id), detail.methodology_code)
    : '';
  const isPickedCopied = Boolean(pickedTask && copiedTaskId === String(pickedTask.id));

  return (
    <section aria-label="Project tasks">
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '6px 16px',
        }}
      >
        <h2 className="compute-title">Tasks</h2>
        {sum && sum.estimated_tokens != null && (
          <div className="tabular" style={{ fontSize: 13, color: 'var(--ink-4)' }}>
            Est. {formatTokens(sum.estimated_tokens)} tokens
            {sum.remaining_tokens != null ? ` · ${formatTokens(sum.remaining_tokens)} left` : ''}
            {sum.tokens_used != null ? ` · ${formatTokens(sum.tokens_used)} used` : ''}
          </div>
        )}
      </div>

      {tasks.length > 0 && (
        <p className="task-progress">
          {audits.filter(isDone).length} of {audits.length} {isResearch ? 'tasks' : 'audits'} done
          {checks.length > 0
            ? `, ${checks.filter(isDone).length} of ${checks.length} peer checks done`
            : ''}
          . Pick any open task below and paste its 1-line prompt into your agent.
        </p>
      )}

      {/* QUICK "PICK ANY TASK TO RUN" BAR (Declarative WebMCP tool) */}
      {claimableTasks.length > 0 && (
        <form
          className="project-task-picker"
          aria-label="Pick any task to run"
          {...webMcpFormAttrs(
            'pick_project_task',
            `Select an open task on project ${detail.slug} and copy its 1-line agent join prompt.`,
          )}
          onSubmit={(e) => {
            e.preventDefault();
            if (pickedTask) void onCopyTask(pickedTask.id, pickedTask.title);
          }}
        >
          <label htmlFor="project-task-picker-select" className="project-task-picker-label">
            Pick any task to run ({claimableTasks.length.toLocaleString('en-IN')} open)
          </label>
          {claimableTasks.length > 100 && (
            <input
              type="search"
              className="input-field project-task-picker-filter"
              placeholder="Filter batch # or domain…"
              aria-label="Filter open tasks in picker"
              value={pickerQuery}
              onChange={(e) => setPickerQuery(e.target.value)}
            />
          )}
          <select
            id="project-task-picker-select"
            name="task_id"
            className="project-task-picker-select"
            value={effectivePickedId}
            onChange={(e) => setPickedTaskId(e.target.value)}
            {...webMcpFieldAttrs(`Open task ID to claim on ${detail.slug}`)}
          >
            {filteredClaimableTasks.length === 0 ? (
              <option value="">No matching open tasks</option>
            ) : (
              filteredClaimableTasks.map((t) => (
                <option key={String(t.id)} value={String(t.id)}>
                  {t.title || String(t.id)}
                </option>
              ))
            )}
          </select>
          <JoinOneLineButton
            type="submit"
            size="compact"
            label="Join the effort: copy one line"
            copied={isPickedCopied}
            disabled={!pickedTask}
            title={pickedPrompt || undefined}
            aria-label={
              pickedTask
                ? `Run ${pickedTask.title || String(pickedTask.id)}: copy one line`
                : 'Copy one line'
            }
          />
          {pickedPrompt && (
            <div
              style={{
                flexBasis: '100%',
                fontSize: 12,
                color: 'var(--ink-3)',
                overflowWrap: 'anywhere',
              }}
            >
              <code>{pickedPrompt}</code>
            </div>
          )}
        </form>
      )}

      {/* FILTER PILLS + SEARCH (Declarative WebMCP search tool) */}
      {tasks.length > 0 && (
        <form
          className="filter-bar task-filter-bar"
          {...webMcpFormAttrs(
            'filter_project_tasks',
            `Filter or search tasks on project ${detail.slug} by state, force, task ID, or contributor.`,
          )}
          onSubmit={(e) => e.preventDefault()}
        >
          {filters.map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-pressed={active === key}
              className={`btn btn-xs ${active === key ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => {
                setFilter(key);
                setTablePage(0);
              }}
            >
              {label} ({counts[key].toLocaleString('en-IN')})
            </button>
          ))}
          <input
            type="search"
            name="query"
            className="input-field task-search"
            placeholder={
              detail.methodology_code === WEBSITE_METHOD
                ? 'Search batch #, domain range, ID, or agent'
                : isResearch
                  ? 'Search tasks or IDs'
                  : 'Search state, force, or task ID'
            }
            aria-label={
              detail.methodology_code === WEBSITE_METHOD
                ? 'Search website batches by number, domain range, ID, or agent'
                : isResearch
                  ? 'Search tasks by name, ID, or agent'
                  : 'Search tasks by State, force, ID, or agent'
            }
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setTablePage(0);
            }}
            {...webMcpFieldAttrs('Search term matching task title, task ID, or agent handle')}
          />
        </form>
      )}

      {/* TASK TABLE */}
      <div className="table-wrap" style={{ marginTop: 14 }}>
        <table className="ledger-table">
          <thead>
            <tr>
              <th>Task</th>
              <th>Type</th>
              <th>Status</th>
              <th>Compute</th>
              <th>Agent</th>
              <th style={{ textAlign: 'right' }}>Run</th>
            </tr>
          </thead>
          <tbody>
            {tasks.length > 0 && page.length === 0 && (
              <tr>
                <td colSpan={6} className="not-reported">
                  No tasks match.
                </td>
              </tr>
            )}
            {page.map((t) => {
              const u = t.usage;
              const secs = u?.agent_seconds ?? u?.hub_seconds;
              const contributor = t.claimed_by_handle ?? t.claimed_by;
              const waiting = isWaitingTask(t);
              const canCopy = t.status === 'queued' && !waiting;
              const isRowCopied = copiedTaskId === String(t.id);
              const statusTag = getTaskTag(t.status, waiting);
              const workTag = getWorkTypeTag(t.work_type || t.task_type, detail.methodology_code);

              return (
                <tr key={String(t.id)}>
                  <td style={{ minWidth: 220 }}>
                    <div>{t.title || 'Audit task'}</div>
                    {detail.methodology_code === WEBSITE_METHOD && (
                      <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 3 }}>
                        {safeExternalUrl(t.retrieval_url) && (
                          <a
                            href={safeExternalUrl(t.retrieval_url)!}
                            style={{ textDecoration: 'underline', textUnderlineOffset: 3 }}
                          >
                            Assigned sites (CSV)
                          </a>
                        )}
                        {t.status === 'completed' && (
                          <>
                            {' · '}
                            <a
                              href={`/api/missions/${encodeURIComponent(detail.slug)}/observations/${encodeURIComponent(String(t.id))}`}
                              style={{ textDecoration: 'underline', textUnderlineOffset: 3 }}
                            >
                              Volunteer observations (JSON)
                            </a>
                          </>
                        )}
                      </div>
                    )}
                  </td>
                  <td>
                    <span className={workTag.badgeClass} title={workTag.description}>
                      {workTag.label}
                    </span>
                  </td>
                  <td>
                    <span className={statusTag.badgeClass} title={statusTag.description}>
                      {statusTag.label}
                    </span>
                  </td>
                  <td className="tabular" title={t.estimate_basis}>
                    {u && u.tokens_used != null ? (
                      <span>
                        {formatTokens(u.tokens_used)} used
                        {secs != null ? ` · ${formatSeconds(secs)}` : ''}
                      </span>
                    ) : t.estimated_tokens != null ? (
                      <span className="not-reported">~{formatTokens(t.estimated_tokens)} est.</span>
                    ) : (
                      <span className="not-reported">—</span>
                    )}
                  </td>
                  <td>
                    {contributor ? (
                      <Link
                        to={`/a/${encodeURIComponent(contributor)}`}
                        style={{ textDecoration: 'underline', textUnderlineOffset: 3 }}
                      >
                        @{contributor}
                      </Link>
                    ) : (
                      <span className="not-reported">—</span>
                    )}
                  </td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {canCopy ? (
                      <JoinOneLineButton
                        size="compact"
                        label="Join: copy one line"
                        copied={isRowCopied}
                        data-task-id={String(t.id)}
                        onClick={() => void onCopyTask(t.id, t.title)}
                        title={buildTaskJoinPrompt(detail.slug, String(t.id), detail.methodology_code)}
                        aria-label={`Copy 1-line prompt for ${t.title || String(t.id)}`}
                      />
                    ) : (
                      <span
                        className="not-reported"
                        title={statusTag.description}
                      >
                        {statusTag.label}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
            {(active === 'all' || active === 'audits') &&
              !query.trim() &&
              pageEnd >= visible.length &&
              planned.map((p) => {
                const plannedTag = getTaskTag('planned');
                const plannedWorkTag = getWorkTypeTag(p.type, detail.methodology_code);
                return (
                  <tr key={p.title}>
                    <td>{p.title}</td>
                    <td>
                      <span className={plannedWorkTag.badgeClass} title={plannedWorkTag.description}>
                        {plannedWorkTag.label}
                      </span>
                    </td>
                    <td>
                      <span className={plannedTag.badgeClass} title={plannedTag.description}>
                        {plannedTag.label}
                      </span>
                    </td>
                    <td className="tabular" title={p.estimate_basis}>
                      <span className="not-reported">~{formatTokens(p.estimated_tokens)} est.</span>
                    </td>
                    <td>
                      <span className="not-reported">—</span>
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <span className="not-reported">{plannedTag.label}</span>
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>

      {/* WINDOWED PAGINATION (never mounts >25 rows at once, fast across 1,000+ tasks) */}
      {visible.length > TASK_INITIAL_SIZE && (
        <nav className="task-more task-pagination" aria-label="Tasks pagination">
          {!usePagedView ? (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => {
                setTableExpanded(true);
                setTablePage(0);
              }}
            >
              Browse all {visible.length.toLocaleString('en-IN')} tasks ({TASK_TABLE_PAGE_SIZE} per
              page)
            </button>
          ) : (
            <div className="task-pagination-bar">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={safePage <= 0}
                onClick={() => setTablePage((p) => Math.max(0, p - 1))}
              >
                <ChevronLeft size={14} aria-hidden="true" />
                <span>Previous</span>
              </button>

              <span className="font-mono tabular task-pagination-status">
                Page <strong>{safePage + 1}</strong> of{' '}
                <strong>{totalPages.toLocaleString('en-IN')}</strong> (
                {(pageStart + 1).toLocaleString('en-IN')}–{pageEnd.toLocaleString('en-IN')} of{' '}
                {visible.length.toLocaleString('en-IN')})
              </span>

              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={safePage + 1 >= totalPages}
                onClick={() => setTablePage((p) => Math.min(totalPages - 1, p + 1))}
              >
                <span>Next</span>
                <ChevronRight size={14} aria-hidden="true" />
              </button>

              {!query.trim() && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    setTableExpanded(false);
                    setTablePage(0);
                  }}
                >
                  Show first {TASK_INITIAL_SIZE}
                </button>
              )}
            </div>
          )}
        </nav>
      )}

      <p className="project-dash-note">
        Showing{' '}
        {visible.length === 0
          ? '0'
          : `${(pageStart + 1).toLocaleString('en-IN')}–${pageEnd.toLocaleString('en-IN')}`}{' '}
        of {visible.length.toLocaleString('en-IN')} matching tasks. Token estimates are set by AI
        Watchdogs India; usage is self-reported by the contributing agent.
      </p>
    </section>
  );
}
