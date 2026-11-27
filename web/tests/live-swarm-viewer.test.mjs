import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as esbuild from 'esbuild';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(__dirname, '..');
const entryPath = path.join(webDir, 'src/components/LiveSwarmViewer.tsx');

// Bundle LiveSwarmViewer.tsx with CSS stubbed out so Node's built-in test runner
// executes the exact exported TypeScript helpers directly.
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lsv-test-'));
const outFile = path.join(tmpDir, 'LiveSwarmViewer.mjs');

await esbuild.build({
  entryPoints: [entryPath],
  bundle: true,
  format: 'esm',
  platform: 'neutral',
  jsx: 'automatic',
  outfile: outFile,
  plugins: [
    {
      name: 'stub-ui-deps',
      setup(build) {
        build.onLoad({ filter: /\.css$/ }, () => ({ contents: '', loader: 'js' }));
        build.onResolve({ filter: /^(react|react\/jsx-runtime|lucide-react)$/ }, (args) => ({
          path: args.path,
          namespace: 'ui-stub',
        }));
        build.onLoad({ filter: /.*/, namespace: 'ui-stub' }, () => ({
          contents: `
            const noop = () => null;
            export const jsx = noop;
            export const jsxs = noop;
            export const Fragment = noop;
            export const useEffect = noop;
            export const useCallback = (fn) => fn;
            export const useMemo = (fn) => fn();
            export const useRef = (v) => ({ current: v });
            export const useState = (v) => [v, noop];
            export const useId = () => 'id';
            export const createContext = () => ({ Provider: noop });
            export const useContext = () => ({});
            export const ArrowRight = noop;
            export const Check = noop;
            export const ChevronDown = noop;
            export const ChevronUp = noop;
            export const Copy = noop;
            export const ExternalLink = noop;
            export const Radio = noop;
            export const Sparkles = noop;
            export const Users = noop;
            export const X = noop;
            export default {};
          `,
          loader: 'js',
        }));
      },
    },
  ],
});

const mod = await import(pathToFileURL(outFile).href);
const {
  DEFAULT_LIVE_WINDOW_SECONDS,
  MAX_RADIAL_CANVAS_NODES,
  resolveLiveWindowMs,
  isTimestampFreshlyLive,
  isNodeFreshlyLive,
  normalizeNodeLiveState,
  computeEffectiveWorkingNow,
  sortLiveSwarmNodes,
  computeRadialLayout,
} = mod;

test.after(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

const BASE_NOW = Date.parse('2026-11-27T10:00:00.000Z');
const isoAgo = (sec) => new Date(BASE_NOW - sec * 1000).toISOString();

test('isNodeFreshlyLive counts leased workers AND freshly heartbeating recent nodes within 120s (+15s grace)', () => {
  assert.equal(DEFAULT_LIVE_WINDOW_SECONDS, 120);
  assert.equal(resolveLiveWindowMs(undefined), 120_000);

  // Fresh leased worker
  assert.equal(
    isNodeFreshlyLive({ handle: 'alice', status: 'working', last_seen_at: isoAgo(30) }, BASE_NOW, 120),
    true,
  );
  // Fresh verifier
  assert.equal(
    isNodeFreshlyLive({ handle: 'bob', status: 'verifying', last_seen_at: isoAgo(110) }, BASE_NOW, 120),
    true,
  );
  // Fresh recent agent between leases (within 120s + 15s grace) -> MUST be counted live!
  assert.equal(
    isNodeFreshlyLive({ handle: 'carol', status: 'recent', last_seen_at: isoAgo(45) }, BASE_NOW, 120),
    true,
  );
  assert.equal(
    isNodeFreshlyLive({ handle: 'carol-grace', status: 'recent', last_seen_at: isoAgo(135) }, BASE_NOW, 120),
    true,
  );
  // Stale recent / stale working past 135s -> NOT live
  assert.equal(
    isNodeFreshlyLive({ handle: 'carol-stale', status: 'recent', last_seen_at: isoAgo(136) }, BASE_NOW, 120),
    false,
  );
  assert.equal(
    isNodeFreshlyLive({ handle: 'alice-stale', status: 'working', last_seen_at: isoAgo(140) }, BASE_NOW, 120),
    false,
  );
  // Idle / revoked -> NEVER live even if timestamp is recent
  assert.equal(
    isNodeFreshlyLive({ handle: 'dave', status: 'idle', last_seen_at: isoAgo(10) }, BASE_NOW, 120),
    false,
  );
  assert.equal(
    isNodeFreshlyLive({ handle: 'eve', status: 'revoked', last_seen_at: isoAgo(5) }, BASE_NOW, 120),
    false,
  );
  assert.equal(isTimestampFreshlyLive(null, BASE_NOW, 120), false);
});

test('normalizeNodeLiveState keeps fresh recent nodes live and downgrades stale nodes to idle', () => {
  const freshRecent = { handle: 'carol', status: 'recent', last_seen_at: isoAgo(25) };
  assert.equal(normalizeNodeLiveState(freshRecent, BASE_NOW, 120).status, 'recent');

  const staleRecent = { handle: 'carol', status: 'recent', last_seen_at: isoAgo(160), role: 'auditor' };
  const normStaleRecent = normalizeNodeLiveState(staleRecent, BASE_NOW, 120);
  assert.equal(normStaleRecent.status, 'idle');
  assert.equal(normStaleRecent.role, null);

  const staleWorker = {
    handle: 'alice',
    status: 'working',
    role: 'auditor',
    current_task_id: 'T-42',
    progress_pct: 60,
    last_seen_at: isoAgo(200),
  };
  const normStaleWorker = normalizeNodeLiveState(staleWorker, BASE_NOW, 120);
  assert.equal(normStaleWorker.status, 'idle');
  assert.equal(normStaleWorker.current_task_id, null);
  assert.equal(normStaleWorker.progress_pct, null);
});

test('computeEffectiveWorkingNow matches backend working_now (8 leased + 3 fresh recent = 11) and decays cleanly to 0', () => {
  const makeNode = (i, status, ageSec) => ({
    handle: `agent-${i}`,
    status,
    role: status === 'verifying' ? 'peer_verifier' : status === 'working' ? 'auditor' : null,
    last_seen_at: isoAgo(ageSec),
  });

  const nodes = [
    ...Array.from({ length: 5 }, (_, i) => makeNode(i + 1, 'working', 15 + i * 5)),
    ...Array.from({ length: 3 }, (_, i) => makeNode(i + 6, 'verifying', 20 + i * 5)),
    ...Array.from({ length: 3 }, (_, i) => makeNode(i + 9, 'recent', 35 + i * 10)),
    ...Array.from({ length: 13 }, (_, i) => makeNode(i + 12, 'idle', 600 + i * 60)),
  ];

  const snapshot = {
    generated_at: isoAgo(2),
    live_window_seconds: 120,
    working_now: 11,
    active_leases_count: 8,
    total_agents: 42,
    active_nodes: nodes,
    recent_joins: [],
    live_whispers: [],
  };

  // Fresh snapshot -> 11 (exact match with backend working_now!)
  assert.equal(computeEffectiveWorkingNow(snapshot, BASE_NOW), 11);

  // 105 seconds later: nodes with ageSec > 30 age past 135s
  // Ages at +105s: working = 120..140 (4 fresh, 1 aged), verifying = 125..135 (3 fresh), recent = 140..160 (0 fresh) -> 7
  assert.equal(computeEffectiveWorkingNow(snapshot, BASE_NOW + 105_000), 7);

  // 200 seconds later: ALL heartbeats expired -> smoothly decays to 0
  assert.equal(computeEffectiveWorkingNow(snapshot, BASE_NOW + 200_000), 0);
});

test('computeEffectiveWorkingNow preserves overflow when backend caps active_nodes below working_now', () => {
  const nodes = Array.from({ length: 24 }, (_, i) => ({
    handle: `live-${i}`,
    status: i < 18 ? 'working' : 'recent',
    last_seen_at: isoAgo(10),
  }));
  const snapshot = {
    generated_at: isoAgo(1),
    live_window_seconds: 120,
    working_now: 30,
    total_agents: 55,
    active_nodes: nodes,
    recent_joins: [],
    live_whispers: [],
  };
  assert.equal(computeEffectiveWorkingNow(snapshot, BASE_NOW), 30);
  // Decays to 0 once stale
  assert.equal(computeEffectiveWorkingNow(snapshot, BASE_NOW + 160_000), 0);
});

test('sortLiveSwarmNodes deduplicates handles and orders active workers ahead of recent and idle', () => {
  const sorted = sortLiveSwarmNodes([
    { handle: 'idle-1', status: 'idle', last_seen_at: isoAgo(500) },
    { handle: 'recent-1', status: 'recent', last_seen_at: isoAgo(10) },
    { handle: 'work-1', status: 'working', last_seen_at: isoAgo(20) },
    { handle: '@WORK-1', status: 'working', last_seen_at: isoAgo(5) },
    { handle: 'verify-1', status: 'verifying', last_seen_at: isoAgo(15) },
  ]);
  assert.deepEqual(
    sorted.map((n) => n.handle),
    ['work-1', 'verify-1', 'recent-1', 'idle-1'],
  );
});

test('computeRadialLayout guarantees zero overlap, zero hub collision, zero clipping for ALL seat counts 1..16', () => {
  assert.equal(MAX_RADIAL_CANVAS_NODES, 16);

  for (let n = 1; n <= 16; n++) {
    const plan = computeRadialLayout(n);
    assert.equal(plan.slots.length, n, `expected ${n} slots`);
    assert.equal(plan.isTwoRing, n >= 9, `isTwoRing mismatch for n=${n}`);
    assert.equal(plan.rings.length, n >= 9 ? 2 : 1, `ring count mismatch for n=${n}`);

    // 1. Pairwise card non-overlap check (>= 20px margin in at least one axis)
    for (let i = 0; i < plan.slots.length; i++) {
      for (let j = i + 1; j < plan.slots.length; j++) {
        const a = plan.slots[i];
        const b = plan.slots[j];
        const gap = Math.max(
          Math.abs(a.x - b.x) - plan.cardW,
          Math.abs(a.y - b.y) - plan.cardH,
        );
        assert.ok(
          gap >= 20,
          `Overlap detected for n=${n} between slot ${i} and ${j}: gap=${gap.toFixed(1)}px`,
        );
      }
    }

    // 2. Center SWARM hub disc + floating hub pill clearance
    const hubR = plan.isTwoRing ? 44 : 48;
    const pillHalfW = plan.isTwoRing ? 104 : 112;
    const pillY = plan.hubY + hubR + 21;
    for (let i = 0; i < plan.slots.length; i++) {
      const c = plan.slots[i];
      const discGap = Math.max(
        Math.abs(c.x - plan.hubX) - (plan.cardW / 2 + hubR),
        Math.abs(c.y - plan.hubY) - (plan.cardH / 2 + hubR),
      );
      const pillGap = Math.max(
        Math.abs(c.x - plan.hubX) - (plan.cardW / 2 + pillHalfW),
        Math.abs(c.y - pillY) - (plan.cardH / 2 + 14),
      );
      assert.ok(
        Math.min(discGap, pillGap) >= 18,
        `Hub collision for n=${n} slot ${i}: discGap=${discGap.toFixed(1)}, pillGap=${pillGap.toFixed(1)}`,
      );
    }

    // 3. Stage edge padding + bottom legend/pill clearance
    for (let i = 0; i < plan.slots.length; i++) {
      const c = plan.slots[i];
      const leftPad = c.x - plan.cardW / 2;
      const rightPad = plan.stageW - (c.x + plan.cardW / 2);
      const topPad = c.y - plan.cardH / 2;
      const botPad = plan.stageH - (c.y + plan.cardH / 2);
      assert.ok(leftPad >= 18 && rightPad >= 18, `Horizontal clipping for n=${n} slot ${i}`);
      assert.ok(topPad >= 22 && botPad >= 34, `Vertical clipping for n=${n} slot ${i}`);

      const legGap = Math.max(
        Math.max(14 - (c.x + plan.cardW / 2), c.x - plan.cardW / 2 - 265),
        Math.max(plan.stageH - 46 - (c.y + plan.cardH / 2), c.y - plan.cardH / 2 - (plan.stageH - 12)),
      );
      const pillGap = Math.max(
        Math.max(plan.stageW - 270 - (c.x + plan.cardW / 2), c.x - plan.cardW / 2 - (plan.stageW - 14)),
        Math.max(plan.stageH - 48 - (c.y + plan.cardH / 2), c.y - plan.cardH / 2 - (plan.stageH - 12)),
      );
      assert.ok(
        Math.min(legGap, pillGap) >= 16,
        `Bottom chrome collision for n=${n} slot ${i}: legGap=${legGap.toFixed(1)}, pillGap=${pillGap.toFixed(1)}`,
      );
    }
  }

  // Clamping above 16
  assert.equal(computeRadialLayout(25).slots.length, 16);
});
