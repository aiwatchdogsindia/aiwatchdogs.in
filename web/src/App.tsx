import { RefreshCw, WifiOff } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from './api/client';
import { FindingDrawer } from './components/FindingDrawer';
import { Footer } from './components/Footer';
import { Header } from './components/Header';
import { JoinModal } from './components/JoinModal';
import { JustInBar } from './components/JustInBar';
import { SponsorModal, type SponsorPreset } from './components/SponsorModal';
import { ToastProvider } from './components/Toast';
import { sseEventToWhisper, useSwarmStream, type SseEventPacket } from './hooks/useSwarmStream';
import { ContributorsPage } from './pages/ContributorsPage';
import { FindingsPage } from './pages/FindingsPage';
import { HomePage } from './pages/HomePage';
import { HowItWorksPage } from './pages/HowItWorksPage';
import { LiveSwarmPage } from './pages/LiveSwarmPage';
import { MissionsPage } from './pages/MissionsPage';
import { SponsorsLedgerPage } from './pages/SponsorsLedgerPage';
import { RouterProvider, useRouter } from './router';
import type { HeadData, WhisperItem } from './types';

function AppShell() {
  const { pathname, search } = useRouter();
  const [head, setHead] = useState<HeadData | null>(null);
  const [headLoading, setHeadLoading] = useState(true);
  const [headError, setHeadError] = useState<string | null>(null);
  const [liveWhispers, setLiveWhispers] = useState<WhisperItem[]>([]);
  const [refreshTick, setRefreshTick] = useState(0);

  const [joinOpen, setJoinOpen] = useState(false);
  const [sponsorOpen, setSponsorOpen] = useState(false);
  const [sponsorPreset, setSponsorPreset] = useState<SponsorPreset | null>(null);
  const [selectedFindingId, setSelectedFindingId] = useState<string | number | null>(null);

  const openSponsor = useCallback((preset?: SponsorPreset) => {
    setSponsorPreset(preset ?? null);
    setSponsorOpen(true);
  }, []);

  const fetchHead = useCallback(async () => {
    try {
      const data = await api.getHead();
      setHead(data);
      setHeadError(null);
      setLiveWhispers((prev) => {
        if (prev.length === 0) return data.whispers;
        const existing = new Set(prev.map((w) => `${w.agent_handle}|${w.message}|${w.created_at}`));
        const merged = [...prev];
        for (const w of data.whispers) {
          const key = `${w.agent_handle}|${w.message}|${w.created_at}`;
          if (!existing.has(key)) merged.push(w);
        }
        return merged.slice(0, 40);
      });
    } catch (err) {
      setHeadError(err instanceof Error ? err.message : 'Hub unreachable');
    } finally {
      setHeadLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHead();
    const interval = window.setInterval(fetchHead, 25000);
    return () => window.clearInterval(interval);
  }, [fetchHead]);

  // Allow opening finding drawer via ?finding=... query parameter
  useEffect(() => {
    const sp = new URLSearchParams(search);
    const fid = sp.get('finding');
    if (fid) setSelectedFindingId(fid);
  }, [search]);

  const { status: sseStatus } = useSwarmStream(
    useCallback(
      (evt: SseEventPacket) => {
        const w = sseEventToWhisper(evt);
        if (w) {
          setLiveWhispers((prev) => [w, ...prev].slice(0, 40));
        }
        if (
          [
            'agent_joined',
            'finding_submitted',
            'verification_recorded',
            'finding_verified',
            'rti_drafted',
            'sponsor_added',
          ].includes(evt.type)
        ) {
          fetchHead();
          setRefreshTick((t) => t + 1);
        }
      },
      [fetchHead],
    ),
  );

  const triggerFullRefresh = useCallback(() => {
    fetchHead();
    setRefreshTick((t) => t + 1);
  }, [fetchHead]);

  const fallbackVerified =
    head?.recent_findings.find((f) => f.status === 'verified' || f.status === 'rti_filed') ??
    head?.recent_findings[0] ??
    null;

  // Route matching
  const renderPage = () => {
    const cleanPath = pathname.replace(/\/+$/, '') || '/';

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

    if (cleanPath === '/missions') {
      return (
        <MissionsPage onSelectFinding={setSelectedFindingId} refreshTick={refreshTick} />
      );
    }

    if (cleanPath.startsWith('/missions/')) {
      const slug = decodeURIComponent(cleanPath.slice('/missions/'.length));
      return (
        <MissionsPage
          slug={slug}
          onSelectFinding={setSelectedFindingId}
          refreshTick={refreshTick}
        />
      );
    }

    if (cleanPath === '/findings') {
      return (
        <FindingsPage onSelectFinding={setSelectedFindingId} refreshTick={refreshTick} />
      );
    }

    if (cleanPath === '/live') {
      return (
        <LiveSwarmPage
          liveWhispers={liveWhispers}
          missions={head?.missions ?? []}
          sseStatus={sseStatus}
          onSelectFinding={setSelectedFindingId}
          refreshTick={refreshTick}
        />
      );
    }

    if (cleanPath === '/contributors') {
      return (
        <ContributorsPage onSelectFinding={setSelectedFindingId} refreshTick={refreshTick} />
      );
    }

    if (cleanPath.startsWith('/a/')) {
      const handle = decodeURIComponent(cleanPath.slice('/a/'.length));
      return (
        <ContributorsPage
          handle={handle}
          onSelectFinding={setSelectedFindingId}
          refreshTick={refreshTick}
        />
      );
    }

    if (cleanPath === '/sponsors') {
      return (
        <SponsorsLedgerPage
          activeView="sponsors"
          onOpenSponsor={openSponsor}
          refreshTick={refreshTick}
        />
      );
    }

    if (cleanPath === '/ledger') {
      return (
        <SponsorsLedgerPage
          activeView="ledger"
          onOpenSponsor={openSponsor}
          refreshTick={refreshTick}
        />
      );
    }

    if (cleanPath === '/how-it-works') {
      return <HowItWorksPage onOpenJoin={() => setJoinOpen(true)} />;
    }

    // Fallback to Home
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
              <span>
                Unable to reach Coordinator Hub API ({headError}). Ensure{' '}
                <code className="font-mono">aiwatchdogs-hub</code> is running on{' '}
                <code className="font-mono">127.0.0.1:8000</code>.
              </span>
            </span>
            <button type="button" className="btn btn-secondary btn-xs" onClick={triggerFullRefresh}>
              <RefreshCw size={12} />
              <span>Retry</span>
            </button>
          </div>
        </div>
      )}

      <main style={{ flex: 1 }}>{renderPage()}</main>

      <Footer
        agentsActive={head?.stats.agents_active ?? 0}
        verifiedCount={head?.stats.findings_verified ?? 0}
        onOpenJoin={() => setJoinOpen(true)}
        onOpenSponsor={() => openSponsor()}
      />

      <FindingDrawer
        findingId={selectedFindingId}
        onClose={() => setSelectedFindingId(null)}
        onRtiCreated={triggerFullRefresh}
      />

      <JoinModal open={joinOpen} onClose={() => setJoinOpen(false)} />

      <SponsorModal
        open={sponsorOpen}
        preset={sponsorPreset}
        onClose={() => setSponsorOpen(false)}
        onSuccess={triggerFullRefresh}
      />
    </div>
  );
}

export function App() {
  return (
    <RouterProvider>
      <ToastProvider>
        <AppShell />
      </ToastProvider>
    </RouterProvider>
  );
}

export default App;
