import { RefreshCw, WifiOff } from 'lucide-react';
import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react';
import { api, deriveLiveSwarmSnapshot, mergeSseWhispersIntoSnapshot } from './api/client';
import { AgentViewPanel, AgentViewProvider, useAgentView } from './components/AgentView';
import { Footer } from './components/Footer';
import { Header } from './components/Header';
import { JustInBar } from './components/JustInBar';
import { computeEffectiveWorkingNow } from './components/LiveSwarmViewer';
import { ToastProvider } from './components/Toast';
import type { SseStatus } from './hooks/useSwarmStream';
import { HomePage } from './pages/HomePage';
import { initAnalytics } from './lib/analytics';
import { RouterProvider, useRouter } from './router';
import type { HeadData, LiveSwarmSnapshot, WhisperItem } from './types';
import { isVerifiedStatus, safeDecodeURIComponent } from './utils';

// Code-split secondary pages and heavy interactive overlays to keep initial JS lean
const MissionsPage = lazy(() =>
  import('./pages/MissionsPage').then((m) => ({ default: m.MissionsPage })),
);
const FindingsPage = lazy(() =>
  import('./pages/FindingsPage').then((m) => ({ default: m.FindingsPage })),
);
const GraveyardPage = lazy(() =>
  import('./pages/GraveyardPage').then((m) => ({ default: m.GraveyardPage })),
);
const BriefPage = lazy(() => import('./pages/BriefPage').then((m) => ({ default: m.BriefPage })));
const ChangelogPage = lazy(() =>
  import('./pages/ChangelogPage').then((m) => ({ default: m.ChangelogPage })),
);
const LiveSwarmPage = lazy(() =>
  import('./pages/LiveSwarmPage').then((m) => ({ default: m.LiveSwarmPage })),
);
const ContributorsPage = lazy(() =>
  import('./pages/ContributorsPage').then((m) => ({ default: m.ContributorsPage })),
);
const HowItWorksPage = lazy(() =>
  import('./pages/HowItWorksPage').then((m) => ({ default: m.HowItWorksPage })),
);
const FindingDrawer = lazy(() =>
  import('./components/FindingDrawer').then((m) => ({ default: m.FindingDrawer })),
);
const JoinModal = lazy(() =>
  import('./components/JoinModal').then((m) => ({ default: m.JoinModal })),
);

function mergeWhispersNewestFirst(existing: WhisperItem[], incoming: WhisperItem[], limit = 40): WhisperItem[] {
  const byKey = new Map<string, WhisperItem>();
  const keyOf = (w: WhisperItem) =>
    w.event_id != null
      ? `eid:${w.event_id}`
      : w.id != null && typeof w.id === 'number'
        ? `id:${w.id}`
        : `${w.agent_handle}|${w.message}|${w.created_at}`;

  for (const w of existing) byKey.set(keyOf(w), w);
  for (const w of incoming) byKey.set(keyOf(w), w);

  return Array.from(byKey.values())
    .sort((a, b) => {
      if (a.event_id != null && b.event_id != null && a.event_id !== b.event_id) {
        return b.event_id - a.event_id;
      }
      const ta = Date.parse(a.created_at || '') || 0;
      const tb = Date.parse(b.created_at || '') || 0;
      return tb - ta;
    })
    .slice(0, limit);
}

function PageFallback() {
  return (
    <div className="container" style={{ paddingTop: 32, paddingBottom: 48 }}>
      <div className="state-banner">Loading view…</div>
    </div>
  );
}

function AppShell() {
  const { pathname, search, navigate } = useRouter();
  const { isAgentView } = useAgentView();
  const cleanPath = pathname.replace(/\/+$/, '') || '/';

  // Google Analytics (no-op unless VITE_GA_MEASUREMENT_ID is set). Page views, including
  // in-app navigations, are counted by GA4 Enhanced Measurement.
  useEffect(() => {
    initAnalytics();
  }, []);
  const needsLiveUpdates = cleanPath === '/' || cleanPath === '/live';

  const [head, setHead] = useState<HeadData | null>(null);
  const [headLoading, setHeadLoading] = useState(true);
  const [headError, setHeadError] = useState<string | null>(null);
  const [swarmLive, setSwarmLive] = useState<LiveSwarmSnapshot | null>(null);
  const [swarmLiveLoading, setSwarmLiveLoading] = useState(true);
  const [liveWhispers, setLiveWhispers] = useState<WhisperItem[]>([]);
  const [refreshTick, setRefreshTick] = useState(0);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [tabVisible, setTabVisible] = useState(() =>
    typeof document === 'undefined' ? true : document.visibilityState !== 'hidden',
  );

  const [joinOpen, setJoinOpen] = useState(false);
  const [selectedFindingId, setSelectedFindingId] = useState<string | number | null>(null);

  useEffect(() => {
    const onVis = () => setTabVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, []);

  useEffect(() => {
    if (!tabVisible) return;
    setNowMs(Date.now());
    const id = window.setInterval(() => setNowMs(Date.now()), 2000);
    return () => window.clearInterval(id);
  }, [tabVisible]);

  const fetchSwarmLive = useCallback(async () => {
    try {
      const snap = await api.getSwarmLive();
      setSwarmLive(snap);
    } catch {
      // Fall back gracefully to head-derived snapshot if /api/swarm/live is unavailable
    } finally {
      setSwarmLiveLoading(false);
    }
  }, []);

  const fetchHead = useCallback(async () => {
    try {
      const data = await api.getHead();
      setHead(data);
      setHeadError(null);
      setLiveWhispers((prev) => mergeWhispersNewestFirst(prev, data.whispers, 40));
    } catch (err) {
      setHeadError(err instanceof Error ? err.message : 'Hub unreachable');
    } finally {
      setHeadLoading(false);
    }
  }, []);

  // Initial head + live swarm telemetry load once on mount
  useEffect(() => {
    fetchHead();
    fetchSwarmLive();
  }, [fetchHead, fetchSwarmLive]);

  // Allow opening finding drawer via ?finding=... query parameter or direct /findings/:id URL
  useEffect(() => {
    const sp = new URLSearchParams(search);
    const fid = sp.get('finding');
    if (fid) {
      setSelectedFindingId(fid);
      return;
    }
    if (cleanPath.startsWith('/findings/')) {
      const pathFid = safeDecodeURIComponent(cleanPath.slice('/findings/'.length));
      if (pathFid) setSelectedFindingId(pathFid);
    }
  }, [search, cleanPath]);

  // CDN-cached periodic refresh while visible on live routes (zero open SSE DB pollers)
  useEffect(() => {
    if (!needsLiveUpdates) return;
    let ticks = 0;
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      ticks += 1;
      fetchSwarmLive();
      if (ticks % 2 === 0) {
        fetchHead();
      }
    }, 45_000);
    return () => window.clearInterval(interval);
  }, [needsLiveUpdates, fetchSwarmLive, fetchHead]);

  const sseStatus: SseStatus = headError
    ? 'offline'
    : headLoading && swarmLiveLoading
      ? 'connecting'
      : 'live';

  const triggerFullRefresh = useCallback(() => {
    fetchHead();
    fetchSwarmLive();
    setRefreshTick((t) => t + 1);
  }, [fetchHead, fetchSwarmLive]);

  const effectiveSwarmLive = useMemo<LiveSwarmSnapshot | null>(() => {
    if (swarmLive) {
      return mergeSseWhispersIntoSnapshot(swarmLive, liveWhispers);
    }
    return deriveLiveSwarmSnapshot(head, liveWhispers);
  }, [swarmLive, head, liveWhispers]);

  const effectiveWorkingNow = useMemo<number | null>(() => {
    if (effectiveSwarmLive) {
      return computeEffectiveWorkingNow(effectiveSwarmLive, nowMs);
    }
    return head ? head.stats.agents_active : null;
  }, [effectiveSwarmLive, head, nowMs]);

  const effectiveVerifiedCount = useMemo<number | null>(() => {
    const headVerified = head?.stats.findings_verified;
    const swarmVerified = effectiveSwarmLive?.progress?.findings_verified;
    if (headVerified == null && swarmVerified == null) return null;
    return Math.max(headVerified ?? 0, swarmVerified ?? 0);
  }, [head?.stats.findings_verified, effectiveSwarmLive?.progress?.findings_verified]);

  // Strictly verified, non-quarantined finding only (never fall back to unverified or disputed)
  const fallbackVerified =
    head?.recent_findings.find((f) => isVerifiedStatus(f.status) && !f.quarantined) ?? null;

  const renderPage = () => {
    if (cleanPath === '/') {
      return (
        <HomePage
          head={head}
          swarmLive={effectiveSwarmLive}
          effectiveWorkingNow={effectiveWorkingNow}
          swarmLiveLoading={swarmLiveLoading && !effectiveSwarmLive}
          sseStatus={sseStatus}
          loading={headLoading}
          onOpenJoin={() => setJoinOpen(true)}
          onSelectFinding={setSelectedFindingId}
          refreshTick={refreshTick}
        />
      );
    }

    if (cleanPath === '/projects' || cleanPath === '/missions') {
      return (
        <Suspense fallback={<PageFallback />}>
          <MissionsPage onSelectFinding={setSelectedFindingId} refreshTick={refreshTick} />
        </Suspense>
      );
    }

    if (cleanPath.startsWith('/projects/') || cleanPath.startsWith('/missions/')) {
      const slug = safeDecodeURIComponent(cleanPath.replace(/^\/(projects|missions)\//, ''));
      return (
        <Suspense fallback={<PageFallback />}>
          <MissionsPage
            slug={slug}
            onSelectFinding={setSelectedFindingId}
            refreshTick={refreshTick}
          />
        </Suspense>
      );
    }

    if (cleanPath === '/findings' || cleanPath.startsWith('/findings/')) {
      return (
        <Suspense fallback={<PageFallback />}>
          <FindingsPage onSelectFinding={setSelectedFindingId} refreshTick={refreshTick} />
        </Suspense>
      );
    }

    if (cleanPath === '/graveyard') {
      return (
        <Suspense fallback={<PageFallback />}>
          <GraveyardPage onSelectFinding={setSelectedFindingId} refreshTick={refreshTick} />
        </Suspense>
      );
    }

    if (cleanPath === '/live') {
      return (
        <Suspense fallback={<PageFallback />}>
          <LiveSwarmPage
            swarmLive={effectiveSwarmLive}
            swarmLiveLoading={swarmLiveLoading && !effectiveSwarmLive}
            liveWhispers={liveWhispers}
            missions={head?.missions ?? []}
            sseStatus={sseStatus}
            onOpenJoin={() => setJoinOpen(true)}
            onSelectFinding={setSelectedFindingId}
            refreshTick={refreshTick}
          />
        </Suspense>
      );
    }

    if (cleanPath === '/contributors' || cleanPath === '/leaderboard') {
      return (
        <Suspense fallback={<PageFallback />}>
          <ContributorsPage onSelectFinding={setSelectedFindingId} refreshTick={refreshTick} />
        </Suspense>
      );
    }

    if (cleanPath.startsWith('/a/')) {
      const handle = safeDecodeURIComponent(cleanPath.slice('/a/'.length));
      return (
        <Suspense fallback={<PageFallback />}>
          <ContributorsPage
            handle={handle}
            onSelectFinding={setSelectedFindingId}
            refreshTick={refreshTick}
          />
        </Suspense>
      );
    }

    if (cleanPath === '/brief') {
      return (
        <Suspense fallback={<PageFallback />}>
          <BriefPage onOpenJoin={() => setJoinOpen(true)} />
        </Suspense>
      );
    }

    if (cleanPath === '/changelog') {
      return (
        <Suspense fallback={<PageFallback />}>
          <ChangelogPage />
        </Suspense>
      );
    }

    if (cleanPath === '/how-it-works') {
      return (
        <Suspense fallback={<PageFallback />}>
          <HowItWorksPage onOpenJoin={() => setJoinOpen(true)} />
        </Suspense>
      );
    }

    return (
      <HomePage
        head={head}
        swarmLive={effectiveSwarmLive}
        effectiveWorkingNow={effectiveWorkingNow}
        swarmLiveLoading={swarmLiveLoading && !effectiveSwarmLive}
        sseStatus={sseStatus}
        loading={headLoading}
        onOpenJoin={() => setJoinOpen(true)}
        onSelectFinding={setSelectedFindingId}
        refreshTick={refreshTick}
      />
    );
  };

  if (isAgentView) {
    return <AgentViewPanel />;
  }

  return (
    <div className="app-shell">
      <Header onOpenJoin={() => setJoinOpen(true)} />

      <JustInBar
        justIn={head?.just_in}
        recentFindings={head?.recent_findings}
        fallbackFinding={fallbackVerified}
        queuedTasks={head?.stats.tasks_queued ?? 0}
        missionsCount={head?.missions.length ?? 0}
        onSelectFinding={setSelectedFindingId}
        onOpenJoin={() => setJoinOpen(true)}
      />

      {headError && (
        <div className="offline-banner" role="alert">
          <div className="offline-banner-row">
            <span className="offline-banner-msg">
              <WifiOff size={14} style={{ flexShrink: 0 }} />
              {import.meta.env.DEV ? (
                <span>
                  Unable to reach Coordinator Hub API ({headError}). Ensure{' '}
                  <code className="font-mono">aiwatchdogs-hub</code> is running on{' '}
                  <code className="font-mono">127.0.0.1:8000</code>.
                </span>
              ) : (
                <span>
                  Live hub data is temporarily unavailable ({headError}). Please retry in a moment.
                </span>
              )}
            </span>
            <button type="button" className="btn btn-secondary btn-xs" onClick={triggerFullRefresh}>
              <RefreshCw size={12} />
              <span>Retry</span>
            </button>
          </div>
        </div>
      )}

      <main className="app-main">
        {renderPage()}
      </main>

      <Footer
        agentsActive={effectiveWorkingNow}
        verifiedCount={effectiveVerifiedCount}
        onOpenJoin={() => setJoinOpen(true)}
      />

      {selectedFindingId != null && (
        <Suspense fallback={null}>
          <FindingDrawer
            findingId={selectedFindingId}
            onClose={() => {
              setSelectedFindingId(null);
              if (cleanPath.startsWith('/findings/')) {
                navigate('/findings');
              }
            }}
            onRtiCreated={triggerFullRefresh}
          />
        </Suspense>
      )}

      {joinOpen && (
        <Suspense fallback={null}>
          <JoinModal open={joinOpen} onClose={() => setJoinOpen(false)} />
        </Suspense>
      )}
    </div>
  );
}

export function App() {
  return (
    <RouterProvider>
      <ToastProvider>
        <AgentViewProvider>
          <AppShell />
        </AgentViewProvider>
      </ToastProvider>
    </RouterProvider>
  );
}

export default App;
