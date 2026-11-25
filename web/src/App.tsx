import { RefreshCw, WifiOff } from 'lucide-react';
import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api/client';
import { AgentViewProvider, AgentViewSlot } from './components/AgentView';
import { Footer } from './components/Footer';
import { Header } from './components/Header';
import { JustInBar } from './components/JustInBar';
import type { SponsorPreset } from './components/SponsorModal';
import { ToastProvider } from './components/Toast';
import { sseEventToWhisper, useSwarmStream, type SseEventPacket } from './hooks/useSwarmStream';
import { HomePage } from './pages/HomePage';
import { initAnalytics } from './lib/analytics';
import { RouterProvider, useRouter } from './router';
import type { HeadData, WhisperItem } from './types';
import { isVerifiedStatus, safeDecodeURIComponent } from './utils';

// Code-split secondary pages and heavy interactive overlays to keep initial JS lean
const MissionsPage = lazy(() =>
  import('./pages/MissionsPage').then((m) => ({ default: m.MissionsPage })),
);
const FindingsPage = lazy(() =>
  import('./pages/FindingsPage').then((m) => ({ default: m.FindingsPage })),
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
const SponsorsLedgerPage = lazy(() =>
  import('./pages/SponsorsLedgerPage').then((m) => ({ default: m.SponsorsLedgerPage })),
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
const SponsorModal = lazy(() =>
  import('./components/SponsorModal').then((m) => ({ default: m.SponsorModal })),
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
  const { pathname, search } = useRouter();
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
  const [liveWhispers, setLiveWhispers] = useState<WhisperItem[]>([]);
  const [refreshTick, setRefreshTick] = useState(0);
  const [latestEventId, setLatestEventId] = useState<number>(0);
  const latestEventIdRef = useRef<number>(0);
  const eventsSupportedRef = useRef<boolean>(false);
  const sseRefreshTimerRef = useRef<number | null>(null);

  const [joinOpen, setJoinOpen] = useState(false);
  const [sponsorOpen, setSponsorOpen] = useState(false);
  const [sponsorPreset, setSponsorPreset] = useState<SponsorPreset | null>(null);
  const [selectedFindingId, setSelectedFindingId] = useState<string | number | null>(null);

  const openSponsor = useCallback((preset?: SponsorPreset) => {
    setSponsorPreset(preset ?? null);
    setSponsorOpen(true);
  }, []);

  const recordLatestEventId = useCallback((candidate?: number) => {
    if (candidate != null && candidate > latestEventIdRef.current) {
      latestEventIdRef.current = candidate;
      setLatestEventId(candidate);
    }
  }, []);

  const fetchHead = useCallback(async () => {
    try {
      const data = await api.getHead();
      setHead(data);
      setHeadError(null);
      if (data.latest_event_id != null) {
        eventsSupportedRef.current = true;
        recordLatestEventId(data.latest_event_id);
      }
      for (const w of data.whispers) {
        if (w.event_id != null && eventsSupportedRef.current) {
          recordLatestEventId(w.event_id);
        }
      }
      setLiveWhispers((prev) => mergeWhispersNewestFirst(prev, data.whispers, 40));
    } catch (err) {
      setHeadError(err instanceof Error ? err.message : 'Hub unreachable');
    } finally {
      setHeadLoading(false);
    }
  }, [recordLatestEventId]);

  const pollIncrementalEvents = useCallback(async () => {
    if (document.visibilityState === 'hidden') return;
    if (!eventsSupportedRef.current) {
      await fetchHead();
      return;
    }
    try {
      const { events, latest_event_id } = await api.getEvents(latestEventIdRef.current);
      if (latest_event_id != null) recordLatestEventId(latest_event_id);
      if (events.length > 0) {
        setLiveWhispers((prev) => mergeWhispersNewestFirst(prev, events, 40));
        await fetchHead();
        setRefreshTick((t) => t + 1);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message.toLowerCase() : '';
      if (msg.includes('404') || msg.includes('not found')) {
        eventsSupportedRef.current = false;
      }
      // Fallback to snapshot refresh if /api/events unavailable
      await fetchHead();
    }
  }, [fetchHead, recordLatestEventId]);

  // Initial head load once on mount
  useEffect(() => {
    fetchHead();
  }, [fetchHead]);

  // Coalesce SSE-triggered refreshes so bursts never amplify into parallel refetches
  const scheduleSseRefresh = useCallback(() => {
    if (sseRefreshTimerRef.current !== null) return;
    sseRefreshTimerRef.current = window.setTimeout(() => {
      sseRefreshTimerRef.current = null;
      fetchHead();
      setRefreshTick((t) => t + 1);
    }, 2000);
  }, [fetchHead]);

  useEffect(() => {
    return () => {
      if (sseRefreshTimerRef.current !== null) {
        window.clearTimeout(sseRefreshTimerRef.current);
        sseRefreshTimerRef.current = null;
      }
    };
  }, []);

  // Allow opening finding drawer via ?finding=... query parameter (stable dossier URLs)
  useEffect(() => {
    const sp = new URLSearchParams(search);
    const fid = sp.get('finding');
    if (fid) setSelectedFindingId(fid);
  }, [search]);

  // Scope SSE connection strictly to live-telemetry routes (/ and /live)
  const { status: sseStatus } = useSwarmStream(
    useCallback(
      (evt: SseEventPacket) => {
        if (evt.id != null) recordLatestEventId(evt.id);
        const w = sseEventToWhisper(evt);
        if (w) {
          setLiveWhispers((prev) => mergeWhispersNewestFirst(prev, [w], 40));
        }
        if (
          [
            'agent_joined',
            'finding_submitted',
            'verification_recorded',
            'finding_verified',
            'finding_corrected',
            'finding_quarantined',
            'rti_drafted',
            'sponsor_added',
          ].includes(evt.type)
        ) {
          scheduleSseRefresh();
        }
      },
      [recordLatestEventId, scheduleSseRefresh],
    ),
    { enabled: needsLiveUpdates, sinceId: latestEventId },
  );

  // Scope background fallback polling strictly to live-telemetry routes when SSE is not live
  useEffect(() => {
    if (!needsLiveUpdates || sseStatus === 'live') return;
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      pollIncrementalEvents();
    }, 25000);
    return () => window.clearInterval(interval);
  }, [needsLiveUpdates, sseStatus, pollIncrementalEvents]);

  const triggerFullRefresh = useCallback(() => {
    if (sseRefreshTimerRef.current !== null) {
      window.clearTimeout(sseRefreshTimerRef.current);
      sseRefreshTimerRef.current = null;
    }
    fetchHead();
    setRefreshTick((t) => t + 1);
  }, [fetchHead]);

  // Strictly verified, non-quarantined finding only (never fall back to unverified or disputed)
  const fallbackVerified =
    head?.recent_findings.find((f) => isVerifiedStatus(f.status) && !f.quarantined) ?? null;

  const renderPage = () => {
    if (cleanPath === '/') {
      return (
        <HomePage
          head={head}
          liveWhispers={liveWhispers}
          sseStatus={sseStatus}
          loading={headLoading}
          onOpenJoin={() => setJoinOpen(true)}
          onOpenSponsor={openSponsor}
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

    if (cleanPath === '/findings') {
      return (
        <Suspense fallback={<PageFallback />}>
          <FindingsPage onSelectFinding={setSelectedFindingId} refreshTick={refreshTick} />
        </Suspense>
      );
    }

    if (cleanPath === '/live') {
      return (
        <Suspense fallback={<PageFallback />}>
          <LiveSwarmPage
            liveWhispers={liveWhispers}
            missions={head?.missions ?? []}
            sseStatus={sseStatus}
            onSelectFinding={setSelectedFindingId}
            refreshTick={refreshTick}
          />
        </Suspense>
      );
    }

    if (cleanPath === '/contributors') {
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

    if (cleanPath === '/sponsors') {
      return (
        <Suspense fallback={<PageFallback />}>
          <SponsorsLedgerPage
            activeView="sponsors"
            onOpenSponsor={openSponsor}
            refreshTick={refreshTick}
          />
        </Suspense>
      );
    }

    if (cleanPath === '/ledger') {
      return (
        <Suspense fallback={<PageFallback />}>
          <SponsorsLedgerPage
            activeView="ledger"
            onOpenSponsor={openSponsor}
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
        liveWhispers={liveWhispers}
        sseStatus={sseStatus}
        loading={headLoading}
        onOpenJoin={() => setJoinOpen(true)}
        onOpenSponsor={openSponsor}
        onSelectFinding={setSelectedFindingId}
        refreshTick={refreshTick}
      />
    );
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <Header onOpenJoin={() => setJoinOpen(true)} />

      <JustInBar
        justIn={head?.just_in}
        fallbackFinding={fallbackVerified}
        queuedTasks={head?.stats.tasks_queued ?? 0}
        missionsCount={head?.missions.length ?? 0}
        onSelectFinding={setSelectedFindingId}
        onOpenJoin={() => setJoinOpen(true)}
      />

      {headError && (
        <div className="offline-banner" role="alert">
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 10,
              flexWrap: 'wrap',
            }}
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
              <WifiOff size={14} />
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

      <main style={{ flex: 1 }}>
        <AgentViewSlot>{renderPage()}</AgentViewSlot>
      </main>

      <Footer
        agentsActive={head ? head.stats.agents_active : null}
        verifiedCount={head ? head.stats.findings_verified : null}
        onOpenJoin={() => setJoinOpen(true)}
        onOpenSponsor={() => openSponsor()}
      />

      {selectedFindingId != null && (
        <Suspense fallback={null}>
          <FindingDrawer
            findingId={selectedFindingId}
            onClose={() => setSelectedFindingId(null)}
            onRtiCreated={triggerFullRefresh}
          />
        </Suspense>
      )}

      {joinOpen && (
        <Suspense fallback={null}>
          <JoinModal open={joinOpen} onClose={() => setJoinOpen(false)} />
        </Suspense>
      )}

      {sponsorOpen && (
        <Suspense fallback={null}>
          <SponsorModal
            open={sponsorOpen}
            preset={sponsorPreset}
            onClose={() => setSponsorOpen(false)}
            onSuccess={triggerFullRefresh}
          />
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
