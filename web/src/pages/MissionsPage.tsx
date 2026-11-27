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
import type {
  MissionItem,
  MissionTask,
  ProjectTaskPage,
  ProjectTaskStatusFilter,
} from '../types';
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

const WEBSITE_METHOD = 'W1';
const UNION_BUDGET_METHOD = 'UB1';
const ONE_ROAD_METHOD = 'OR1';
const ONE_TEACHER_METHOD = 'P5';
const PARLIAMENT_METHOD = 'P6';
const SANSAD_LS_ASSURANCES_URL = 'https://sansad.in/ls/business/government-assurances';
const SANSAD_RS_ASSURANCES_URL = 'https://sansad.in/rs/house-business/government-assurances';
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
  const [taskFilterRequest, setTaskFilterRequest] = useState<TaskFilterRequest | null>(null);

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

  useEffect(() => {
    setTaskFilterRequest(null);
  }, [slug]);

  const handleFilterTasks = useCallback((text: string) => {
    setTaskFilterRequest((prev) => ({ text, nonce: (prev?.nonce ?? 0) + 1 }));
    document
      .getElementById(PROJECT_TASKS_SECTION_ID)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

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
    const isWebsiteAudit = detail?.methodology_code === WEBSITE_METHOD;
    const isUnionBudget = detail?.methodology_code === UNION_BUDGET_METHOD;
    const isOneRoad = detail?.methodology_code === ONE_ROAD_METHOD;
    const isOneTeacher = detail?.methodology_code === ONE_TEACHER_METHOD;
    const isParliament = detail?.methodology_code === PARLIAMENT_METHOD;

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
      <div className="container project-detail-page">
        <div className="project-detail-back">
          <Link to="/projects" className="btn btn-ghost btn-sm">
            <ArrowLeft size={14} />
            <span>All projects</span>
          </Link>
        </div>

        {loading && <div className="state-banner">Loading project…</div>}
        {error && <div className="state-banner">Could not load project: {error}</div>}

        {detail && (
          <div className="project-detail-stack">
            {/* 1. CLEAN COMPACT HEADER */}
            <header className="card card-pad project-detail-header">
              <div className="project-detail-tags">
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

              <h1 className="section-title project-detail-title">{detail.title}</h1>

              {detail.description && (
                <p className="project-detail-desc">{detail.description}</p>
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
                {isParliament && (
                  <>
                    <a href={SANSAD_LS_ASSURANCES_URL} target="_blank" rel="noopener noreferrer">
                      Digital Sansad Lok Sabha Assurances{' '}
                      <ExternalLink size={11} style={{ display: 'inline', verticalAlign: '-1px' }} />
                    </a>
                    <a href={SANSAD_RS_ASSURANCES_URL} target="_blank" rel="noopener noreferrer">
                      Digital Sansad Rajya Sabha Assurances{' '}
                      <ExternalLink size={11} style={{ display: 'inline', verticalAlign: '-1px' }} />
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
                isUnionBudget
                  ? 'Paste one line into your coding agent to start right away at ₹0: reads both budget PDFs in code and submits a lead or cleared result.'
                  : isOneRoad
                    ? 'Paste one line into your coding agent to start right away at ₹0: reads the assigned CAG Union report PDF in code and submits duplicate-billing leads or cleared results.'
                  : isOneTeacher
                    ? 'Paste one line into your coding agent to start right away at ₹0: reads the assigned UDISE+ national booklet PDF in code and maps single-teacher and primary staffing counts.'
                  : isParliament
                    ? 'Paste one line into your coding agent to start right away at ₹0: verifies a Digital Sansad ministerial assurance and cites the original promise, recorded status, and follow-up answer.'
                  : isWebsiteAudit
                    ? 'Paste one line into your coding agent to start right away at ₹0: audits a batch of .gov.in sites against GIGW 3.0, RTI Act 2005, and GoI checks.'
                    : 'Paste one line into your coding agent to start free ₹0 checks immediately (auto-uses your GitHub handle).'
              }
              scoreUnit={isWebsiteAudit ? 'tasks' : 'points'}
              showSeeAllLink={true}
              compact={true}
            />

            {/* 4. INTERACTIVE RESULTS VISUALISER */}
            <ProjectResultsVisualizer
              mission={detail}
              visualSummary={detail.visual_summary}
              findings={detail.findings}
              onSelectFinding={onSelectFinding}
              onFilterTasks={handleFilterTasks}
            />

            {/* 5. FINDINGS & AUDIT LEADS (P1 / Budget Audits when findings exist) */}
            {!isWebsiteAudit && allFindings.length > 0 && (
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
                          <h3>What the swarm checks</h3>
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
                        <div className="project-statutory-anchors">
                          {detail.statutory_anchors.map((anc) => (
                            <span key={anc} className="tag-mono">
                              <ShieldCheck size={11} style={{ marginRight: 4, flexShrink: 0 }} />
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
                      <span>Official sources &amp; public portals ({projectSourcesCount})</span>
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
                        <>
                          <p className="pv-scroll-hint" aria-hidden="true">
                            Swipe table horizontally to see all columns →
                          </p>
                          <div className="table-wrap project-sources-table-wrap">
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
                                            className="font-mono project-source-url"
                                          >
                                            <span>{safeBaseUrl}</span>
                                            <ExternalLink size={11} style={{ flexShrink: 0 }} />
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
                        </>
                      )}
                    </div>
                  </details>
                )}
              </div>
            )}

            {/* 7. TASKS TABLE (6 INITIALLY + SHOW ALL + PICK ANY TASK + 1-LINE COPY) */}
            <ProjectCompute
              key={detail.slug}
              detail={detail}
              copiedTaskId={copiedTaskId}
              onCopyTask={handleCopyTask}
              filterRequest={taskFilterRequest}
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
          <div className="filter-bar project-pillar-filter">
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
                          m.methodology_code !== WEBSITE_METHOD ? m.verified_findings_count : undefined
                        }
                        peerReview={
                          m.methodology_code !== WEBSITE_METHOD ? m.peer_review_count : undefined
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

const PROJECT_TASKS_SECTION_ID = 'project-tasks';
/** Most open tasks listed in the "pick any task" menu of a large project. */
const PICKER_MAX_OPTIONS = 100;
const SEARCH_DEBOUNCE_MS = 300;

const isPeerCheck = (t: MissionTask) => t.task_type === 'peer_verify';
const isDone = (t: MissionTask) => t.status === 'completed' || t.status === 'settled';
const byTitleThenId = (a: MissionTask, b: MissionTask) =>
  (a.title ?? '').localeCompare(b.title ?? '') || String(a.id).localeCompare(String(b.id));

/** A request from elsewhere on the page to search the task table (e.g. a financial year). */
interface TaskFilterRequest {
  text: string;
  nonce: number;
}

function taskSearchCopy(methodologyCode: string): { placeholder: string; label: string } {
  if (methodologyCode === WEBSITE_METHOD) {
    return {
      placeholder: 'Search batch #, domain range, ID, or agent',
      label: 'Search website batches by number, domain range, ID, or agent',
    };
  }
  if (methodologyCode === UNION_BUDGET_METHOD) {
    return {
      placeholder: 'Search a ministry, scheme or year…',
      label: 'Search tasks by ministry, scheme, financial year or task ID',
    };
  }
  if (methodologyCode === ONE_ROAD_METHOD) {
    return {
      placeholder: 'Search a CAG report, ministry, sector or year…',
      label: 'Search tasks by CAG report, ministry, sector, year or task ID',
    };
  }
  if (methodologyCode === ONE_TEACHER_METHOD) {
    return {
      placeholder: 'Search a State, UT, or academic year…',
      label: 'Search tasks by State, UT, academic year, or task ID',
    };
  }
  if (methodologyCode === PARLIAMENT_METHOD) {
    return {
      placeholder: 'Search a ministry, house (LS/RS), assurance ref, status, or subject…',
      label: 'Search tasks by ministry, house, assurance reference, recorded status, subject, or task ID',
    };
  }
  return {
    placeholder: 'Search state, force, or task ID',
    label: 'Search tasks by State, force, ID, or agent',
  };
}

function pickerFilterPlaceholder(methodologyCode: string): string {
  if (methodologyCode === WEBSITE_METHOD) return 'Filter batch # or domain…';
  if (methodologyCode === UNION_BUDGET_METHOD) return 'Search a ministry, scheme or year…';
  if (methodologyCode === ONE_ROAD_METHOD) return 'Search a CAG report, sector or year…';
  if (methodologyCode === ONE_TEACHER_METHOD) return 'Search a State, UT or academic year…';
  if (methodologyCode === PARLIAMENT_METHOD) return 'Search a ministry, assurance ref, or status…';
  return 'Filter open tasks…';
}

interface ProjectComputeProps {
  detail: MissionItem;
  copiedTaskId: string | null;
  onCopyTask: (taskId: string | number, taskTitle?: string) => Promise<void>;
  filterRequest?: TaskFilterRequest | null;
}

/**
 * Tasks section. Projects whose detail payload carries every task filter it in the browser;
 * large projects (`tasks_truncated`) page through the hub's tasks endpoint instead.
 */
function ProjectCompute(props: ProjectComputeProps) {
  return props.detail.tasks_truncated ? (
    <ServerPagedTasks {...props} />
  ) : (
    <ClientFilteredTasks {...props} />
  );
}

function TasksHeading({ detail }: { detail: MissionItem }) {
  const sum = detail.token_summary;
  return (
    <div className="project-tasks-head">
      <h2 className="compute-title">Tasks</h2>
      {sum && sum.estimated_tokens != null && (
        <div className="tabular project-tasks-tokens">
          Est. {formatTokens(sum.estimated_tokens)} tokens
          {sum.remaining_tokens != null ? ` · ${formatTokens(sum.remaining_tokens)} left` : ''}
          {sum.tokens_used != null ? ` · ${formatTokens(sum.tokens_used)} used` : ''}
        </div>
      )}
    </div>
  );
}

function TaskProgressLine({
  auditsDone,
  auditsTotal,
  checksDone,
  checksTotal,
}: {
  auditsDone: number;
  auditsTotal: number;
  checksDone: number;
  checksTotal: number;
}) {
  return (
    <p className="task-progress">
      {auditsDone.toLocaleString('en-IN')} of {auditsTotal.toLocaleString('en-IN')} audits done
      {checksTotal > 0
        ? `, ${checksDone.toLocaleString('en-IN')} of ${checksTotal.toLocaleString('en-IN')} peer checks done`
        : ''}
      . Pick any open task below and paste its 1-line prompt into your agent.
    </p>
  );
}

interface TaskPickerProps {
  detail: MissionItem;
  /** Open tasks offered in the menu. */
  claimableTasks: MissionTask[];
  /** Every open task on the project (may exceed `claimableTasks` for a large project). */
  openTotal: number;
  /** True when `claimableTasks` is only the first slice of the open tasks. */
  capped: boolean;
  copiedTaskId: string | null;
  onCopyTask: (taskId: string | number, taskTitle?: string) => Promise<void>;
}

/** Quick "pick any task to run" bar (declarative WebMCP tool). */
function TaskPicker({
  detail,
  claimableTasks,
  openTotal,
  capped,
  copiedTaskId,
  onCopyTask,
}: TaskPickerProps) {
  const [pickerQuery, setPickerQuery] = useState('');
  const [pickedTaskId, setPickedTaskId] = useState('');

  const filteredTasks = useMemo(() => {
    const pq = pickerQuery.trim().toLowerCase();
    if (!pq) return claimableTasks;
    return claimableTasks.filter(
      (t) =>
        (t.title ?? '').toLowerCase().includes(pq) || String(t.id).toLowerCase().includes(pq),
    );
  }, [claimableTasks, pickerQuery]);

  const effectivePickedId =
    pickedTaskId && filteredTasks.some((t) => String(t.id) === pickedTaskId)
      ? pickedTaskId
      : filteredTasks[0]
        ? String(filteredTasks[0].id)
        : '';
  const pickedTask = filteredTasks.find((t) => String(t.id) === effectivePickedId) ?? null;
  const pickedPrompt = pickedTask
    ? buildTaskJoinPrompt(detail.slug, String(pickedTask.id), detail.methodology_code)
    : '';
  const isPickedCopied = Boolean(pickedTask && copiedTaskId === String(pickedTask.id));

  if (claimableTasks.length === 0) return null;

  return (
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
        Pick any task to run ({openTotal.toLocaleString('en-IN')} open)
      </label>
      {!capped && claimableTasks.length > PICKER_MAX_OPTIONS && (
        <input
          type="search"
          className="input-field project-task-picker-filter"
          placeholder={pickerFilterPlaceholder(detail.methodology_code)}
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
        aria-describedby={capped ? 'project-task-picker-hint' : undefined}
        {...webMcpFieldAttrs(`Open task ID to claim on ${detail.slug}`)}
      >
        {filteredTasks.length === 0 ? (
          <option value="">No matching open tasks</option>
        ) : (
          filteredTasks.map((t) => (
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
      {capped && (
        <p id="project-task-picker-hint" className="project-task-picker-hint">
          This menu lists the first {claimableTasks.length.toLocaleString('en-IN')} of{' '}
          {openTotal.toLocaleString('en-IN')} open tasks. Use the search below to find any other
          task and copy its line from the table.
        </p>
      )}
      {pickedPrompt && (
        <div className="project-task-picker-cmd">
          <code>{pickedPrompt}</code>
        </div>
      )}
    </form>
  );
}

function TaskTableHead() {
  return (
    <thead>
      <tr>
        <th scope="col">Task</th>
        <th scope="col">Type</th>
        <th scope="col">Status</th>
        <th scope="col">Compute</th>
        <th scope="col">Agent</th>
        <th scope="col" style={{ textAlign: 'right' }}>
          Run
        </th>
      </tr>
    </thead>
  );
}

function TaskRow({
  detail,
  task: t,
  copiedTaskId,
  onCopyTask,
}: {
  detail: MissionItem;
  task: MissionTask;
  copiedTaskId: string | null;
  onCopyTask: (taskId: string | number, taskTitle?: string) => Promise<void>;
}) {
  const u = t.usage;
  const secs = u?.agent_seconds ?? u?.hub_seconds;
  const contributor = t.claimed_by_handle ?? t.claimed_by;
  const canCopy = t.status === 'queued';
  const isRowCopied = copiedTaskId === String(t.id);
  const statusTag = getTaskTag(t.status);
  const workTag = getWorkTypeTag(t.work_type || t.task_type, detail.methodology_code);
  const retrievalUrl = safeExternalUrl(t.retrieval_url);

  return (
    <tr className="project-task-row">
      <td className="project-task-cell">
        <div className="project-task-title">{t.title || 'Audit task'}</div>
        {detail.methodology_code === WEBSITE_METHOD && (
          <div className="project-task-links">
            {retrievalUrl && (
              <a href={retrievalUrl}>Assigned sites (CSV)</a>
            )}
            {t.status === 'completed' && (
              <>
                <span aria-hidden="true">·</span>
                <a
                  href={`/api/missions/${encodeURIComponent(detail.slug)}/observations/${encodeURIComponent(String(t.id))}`}
                >
                  Volunteer observations (JSON)
                </a>
              </>
            )}
          </div>
        )}
      </td>
      <td className="project-task-type">
        <span className={workTag.badgeClass} title={workTag.description}>
          {workTag.label}
        </span>
      </td>
      <td className="project-task-status">
        <span className={statusTag.badgeClass} title={statusTag.description}>
          {statusTag.label}
        </span>
      </td>
      <td className="tabular project-task-compute" data-label="Compute" title={t.estimate_basis}>
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
      <td className="project-task-agent" data-label="Agent">
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
      <td
        className={`project-task-run${canCopy ? ' project-task-run--action' : ' project-task-run--static'}`}
      >
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
          <span className="not-reported" title={statusTag.description}>
            {statusTag.label}
          </span>
        )}
      </td>
    </tr>
  );
}

function TaskTableNote({ start, end, total }: { start: number; end: number; total: number }) {
  return (
    <p className="project-dash-note" aria-live="polite">
      Showing{' '}
      {total === 0 || end <= start
        ? '0'
        : `${(start + 1).toLocaleString('en-IN')}–${end.toLocaleString('en-IN')}`}{' '}
      of {total.toLocaleString('en-IN')} matching tasks. Token estimates are set by AI Watchdogs
      India; usage is self-reported by the contributing agent.
    </p>
  );
}

/** Small projects: every task is in the detail payload, so filter and page in the browser. */
function ClientFilteredTasks({ detail, copiedTaskId, onCopyTask, filterRequest }: ProjectComputeProps) {
  const tasks = useMemo(() => detail.tasks ?? [], [detail.tasks]);
  const planned = detail.planned_tasks ?? [];

  const [filter, setFilter] = useState<TaskFilter>('audits');
  const [query, setQuery] = useState('');
  const [tableExpanded, setTableExpanded] = useState(false);
  const [tablePage, setTablePage] = useState(0);

  useEffect(() => {
    if (!filterRequest) return;
    setQuery(filterRequest.text);
    setFilter('all');
    setTablePage(0);
  }, [filterRequest]);

  const claimableTasks = useMemo(
    () => tasks.filter((t) => t.status === 'queued').sort(byTitleThenId),
    [tasks],
  );

  const audits = useMemo(() => tasks.filter((t) => !isPeerCheck(t)), [tasks]);
  const checks = useMemo(() => tasks.filter(isPeerCheck), [tasks]);

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
        if (filter === 'audits') return !isPeerCheck(t);
        if (filter === 'checks') return isPeerCheck(t);
        if (filter === 'open') return t.status === 'queued';
        if (filter === 'progress') return t.status === 'claimed';
        if (filter === 'done') return isDone(t);
        return true;
      })
      .filter(
        (t) =>
          !q ||
          (t.title ?? '').toLowerCase().includes(q) ||
          String(t.id).toLowerCase().includes(q) ||
          (t.claimed_by_handle ?? t.claimed_by ?? '').toLowerCase().includes(q),
      )
      .sort(byTitleThenId);
  }, [tasks, filter, query]);

  if (tasks.length === 0 && planned.length === 0) return null;

  const filters: [TaskFilter, string][] = [
    ['audits', 'Audits'],
    ['checks', 'Peer checks'],
    ['open', 'Open'],
    ['progress', 'In progress'],
    ['done', 'Done'],
    ['all', 'All'],
  ];
  const search = taskSearchCopy(detail.methodology_code);

  const usePagedView = tableExpanded || query.trim().length > 0;
  const pageSize = usePagedView ? TASK_TABLE_PAGE_SIZE : TASK_INITIAL_SIZE;
  const totalPages = Math.max(1, Math.ceil(visible.length / pageSize));
  const safePage = Math.min(tablePage, totalPages - 1);
  const pageStart = usePagedView ? safePage * pageSize : 0;
  const pageEnd = Math.min(visible.length, pageStart + pageSize);
  const page = visible.slice(pageStart, pageEnd);

  return (
    <section id={PROJECT_TASKS_SECTION_ID} aria-label="Project tasks">
      <TasksHeading detail={detail} />

      {tasks.length > 0 && (
        <TaskProgressLine
          auditsDone={audits.filter(isDone).length}
          auditsTotal={audits.length}
          checksDone={checks.filter(isDone).length}
          checksTotal={checks.length}
        />
      )}

      <TaskPicker
        detail={detail}
        claimableTasks={claimableTasks}
        openTotal={claimableTasks.length}
        capped={false}
        copiedTaskId={copiedTaskId}
        onCopyTask={onCopyTask}
      />

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
              aria-pressed={filter === key}
              className={`btn btn-xs ${filter === key ? 'btn-primary' : 'btn-ghost'}`}
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
            placeholder={search.placeholder}
            aria-label={search.label}
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
      <p className="pv-scroll-hint pv-scroll-hint--tablet-only" aria-hidden="true">
        Swipe table horizontally for status, compute &amp; run action →
      </p>
      <div className="table-wrap project-tasks-table-wrap">
        <table className="ledger-table project-tasks-table">
          <TaskTableHead />
          <tbody>
            {tasks.length > 0 && page.length === 0 && (
              <tr className="project-task-empty-row">
                <td colSpan={6} className="not-reported project-task-empty">
                  No tasks match.
                </td>
              </tr>
            )}
            {page.map((t) => (
              <TaskRow
                key={String(t.id)}
                detail={detail}
                task={t}
                copiedTaskId={copiedTaskId}
                onCopyTask={onCopyTask}
              />
            ))}
            {(filter === 'all' || filter === 'audits') &&
              !query.trim() &&
              pageEnd >= visible.length &&
              planned.map((p) => {
                const plannedTag = getTaskTag('planned');
                const plannedWorkTag = getWorkTypeTag(p.type, detail.methodology_code);
                return (
                  <tr key={p.title} className="project-task-row">
                    <td className="project-task-cell">
                      <div className="project-task-title">{p.title}</div>
                    </td>
                    <td className="project-task-type">
                      <span className={plannedWorkTag.badgeClass} title={plannedWorkTag.description}>
                        {plannedWorkTag.label}
                      </span>
                    </td>
                    <td className="project-task-status">
                      <span className={plannedTag.badgeClass} title={plannedTag.description}>
                        {plannedTag.label}
                      </span>
                    </td>
                    <td
                      className="tabular project-task-compute"
                      data-label="Compute"
                      title={p.estimate_basis}
                    >
                      <span className="not-reported">~{formatTokens(p.estimated_tokens)} est.</span>
                    </td>
                    <td className="project-task-agent" data-label="Agent">
                      <span className="not-reported">—</span>
                    </td>
                    <td className="project-task-run project-task-run--static">
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

      <TaskTableNote start={pageStart} end={pageEnd} total={visible.length} />
    </section>
  );
}

const SERVER_STATUS_TABS: [ProjectTaskStatusFilter, string][] = [
  ['all', 'All'],
  ['open', 'Open'],
  ['claimed', 'In progress'],
  ['completed', 'Done'],
];

/**
 * Large projects: the detail payload holds only the first open tasks, so the table pages
 * through `GET /api/missions/{slug}/tasks` and every count comes from the hub.
 */
function ServerPagedTasks({ detail, copiedTaskId, onCopyTask, filterRequest }: ProjectComputeProps) {
  const inlineTasks = useMemo(() => detail.tasks ?? [], [detail.tasks]);
  const sc = detail.visual_summary?.status_counts;

  const [status, setStatus] = useState<ProjectTaskStatusFilter>('all');
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [page, setPage] = useState(0);
  const [result, setResult] = useState<ProjectTaskPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!filterRequest) return;
    setQuery(filterRequest.text);
    setDebouncedQuery(filterRequest.text.trim());
    setStatus('all');
    setPage(0);
  }, [filterRequest]);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed === debouncedQuery) return;
    const id = window.setTimeout(() => {
      setDebouncedQuery(trimmed);
      setPage(0);
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [query, debouncedQuery]);

  // `detail` changes on every live refresh of the page, which re-reads the current page.
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api
      .getProjectTasks(
        detail.slug,
        {
          status,
          q: debouncedQuery,
          kind: 'all',
          offset: page * TASK_TABLE_PAGE_SIZE,
          limit: TASK_TABLE_PAGE_SIZE,
        },
        controller.signal,
      )
      .then((res) => {
        if (!controller.signal.aborted) setResult(res);
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : 'Could not load tasks');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [detail, status, debouncedQuery, page]);

  const openTotal = sc?.queued ?? detail.queued_tasks ?? 0;
  const claimableTasks = useMemo(
    () => inlineTasks.filter((t) => t.status === 'queued').slice(0, PICKER_MAX_OPTIONS),
    [inlineTasks],
  );

  const tabCounts: Record<ProjectTaskStatusFilter, number> = result?.counts ?? {
    all: sc?.total ?? detail.total_tasks,
    open: openTotal,
    claimed: sc?.claimed ?? detail.claimed_tasks ?? 0,
    completed: sc?.completed ?? detail.completed_tasks,
  };

  const rows = result?.tasks ?? [];
  const total = result?.total ?? 0;
  const pageStart = result?.offset ?? page * TASK_TABLE_PAGE_SIZE;
  const pageEnd = pageStart + rows.length;
  const search = taskSearchCopy(detail.methodology_code);

  return (
    <section id={PROJECT_TASKS_SECTION_ID} aria-label="Project tasks">
      <TasksHeading detail={detail} />

      <TaskProgressLine
        auditsDone={sc?.audits_done ?? detail.completed_tasks}
        auditsTotal={sc?.audits_total ?? detail.total_tasks}
        checksDone={sc?.checks_done ?? detail.peer_checks_done ?? 0}
        checksTotal={sc?.checks_total ?? detail.peer_checks_total ?? 0}
      />

      <TaskPicker
        detail={detail}
        claimableTasks={claimableTasks}
        openTotal={Math.max(openTotal, claimableTasks.length)}
        capped={openTotal > claimableTasks.length}
        copiedTaskId={copiedTaskId}
        onCopyTask={onCopyTask}
      />

      {/* STATUS TABS + SERVER-SIDE SEARCH (Declarative WebMCP search tool) */}
      <form
        className="filter-bar task-filter-bar"
        {...webMcpFormAttrs(
          'filter_project_tasks',
          `Filter or search all tasks on project ${detail.slug} by state, title, or task ID.`,
        )}
        onSubmit={(e) => {
          e.preventDefault();
          setDebouncedQuery(query.trim());
          setPage(0);
        }}
      >
        {SERVER_STATUS_TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-pressed={status === key}
            className={`btn btn-xs ${status === key ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => {
              setStatus(key);
              setPage(0);
            }}
          >
            {label} ({tabCounts[key].toLocaleString('en-IN')})
          </button>
        ))}
        <input
          type="search"
          name="query"
          className="input-field task-search"
          placeholder={search.placeholder}
          aria-label={search.label}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          {...webMcpFieldAttrs('Search term matching task title or task ID')}
        />
      </form>

      <p className="pv-scroll-hint pv-scroll-hint--tablet-only" aria-hidden="true">
        Swipe table horizontally for status, compute &amp; run action →
      </p>
      <div className="table-wrap project-tasks-table-wrap" aria-busy={loading}>
        <table className="ledger-table project-tasks-table">
          <TaskTableHead />
          <tbody>
            {error ? (
              <tr className="project-task-empty-row">
                <td colSpan={6} className="not-reported project-task-empty">
                  Could not load tasks: {error}
                </td>
              </tr>
            ) : !result ? (
              <tr className="project-task-empty-row">
                <td colSpan={6} className="not-reported project-task-empty">
                  Loading tasks…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr className="project-task-empty-row">
                <td colSpan={6} className="not-reported project-task-empty">
                  No tasks match.
                </td>
              </tr>
            ) : (
              rows.map((t) => (
                <TaskRow
                  key={String(t.id)}
                  detail={detail}
                  task={t}
                  copiedTaskId={copiedTaskId}
                  onCopyTask={onCopyTask}
                />
              ))
            )}
          </tbody>
        </table>
      </div>

      {total > TASK_TABLE_PAGE_SIZE && (
        <nav className="task-more task-pagination" aria-label="Tasks pagination">
          <div className="task-pagination-bar">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={loading || page <= 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              <ChevronLeft size={14} aria-hidden="true" />
              <span>Previous</span>
            </button>
            <span className="font-mono tabular task-pagination-status">
              Page <strong>{(page + 1).toLocaleString('en-IN')}</strong> of{' '}
              <strong>{Math.ceil(total / TASK_TABLE_PAGE_SIZE).toLocaleString('en-IN')}</strong>
            </span>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={loading || pageEnd >= total}
              onClick={() => setPage((p) => p + 1)}
            >
              <span>Next</span>
              <ChevronRight size={14} aria-hidden="true" />
            </button>
          </div>
        </nav>
      )}

      {result && <TaskTableNote start={pageStart} end={pageEnd} total={total} />}
    </section>
  );
}
