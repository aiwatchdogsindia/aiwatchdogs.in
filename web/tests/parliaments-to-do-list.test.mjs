import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as esbuild from 'esbuild';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(__dirname, '..');

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'p6-web-test-'));
const utilsOut = path.join(tmpDir, 'utils.mjs');
const visualsOut = path.join(tmpDir, 'ProjectVisuals.mjs');
const clientOut = path.join(tmpDir, 'client.mjs');

const uiStubPlugin = {
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
        export const BarChart3 = noop;
        export const Check = noop;
        export const Copy = noop;
        export const ExternalLink = noop;
        export const Globe = noop;
        export const Layers = noop;
        export const RotateCcw = noop;
        export const Search = noop;
        export default {};
      `,
      loader: 'js',
    }));
  },
};

await Promise.all([
  esbuild.build({
    entryPoints: [path.join(webDir, 'src/utils.ts')],
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    outfile: utilsOut,
  }),
  esbuild.build({
    entryPoints: [path.join(webDir, 'src/components/ProjectVisuals.tsx')],
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    jsx: 'automatic',
    outfile: visualsOut,
    plugins: [uiStubPlugin],
  }),
  esbuild.build({
    entryPoints: [path.join(webDir, 'src/api/client.ts')],
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    outfile: clientOut,
  }),
]);

const utilsMod = await import(pathToFileURL(utilsOut).href);
const visualsMod = await import(pathToFileURL(visualsOut).href);
const clientMod = await import(pathToFileURL(clientOut).href);

test.after(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test('P6 project metadata helpers resolve slug, code, work type, pillar, and official Digital Sansad hosts', () => {
  assert.equal(utilsMod.projectCodeFor({ slug: 'parliaments-to-do-list' }), 'P6');
  assert.equal(utilsMod.projectCodeFor({ methodology_code: 'P6' }), 'P6');
  assert.equal(utilsMod.pillarLabel('parliament'), 'Parliament & RTI');
  assert.equal(utilsMod.workTypeLabel('anomaly_scan', 'P6'), 'Parliament assurances');
  assert.ok(utilsMod.METHODOLOGY_DESCRIPTIONS.P6?.name.includes("Parliament's To-Do List"));

  // Digital Sansad hosts must be recognized as official government sources
  assert.equal(
    utilsMod.isOfficialGovUrl(
      'https://sansad.in/api_ls/govtAssurance/assurances?loksabha=18&session=7&qNo=544&qType=SQ&page=1&size=20&locale=en',
    ),
    true,
  );
  assert.equal(
    utilsMod.isOfficialGovUrl('https://eparlib.sansad.in/handle/123456789/1'),
    true,
  );
  assert.equal(utilsMod.isOfficialGovUrl('https://oams.nic.in/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://evil-sansad.in/fake'), false);
  assert.equal(utilsMod.isOfficialGovUrl('http://sansad.in/insecure'), false);
});

test('formatAssuranceStatus and extractAssuranceMetrics parse Jhunjhunu-Fatehpur P6 payload cleanly', () => {
  assert.equal(utilsMod.formatAssuranceStatus('PENDING').key, 'pending');
  assert.equal(utilsMod.formatAssuranceStatus('FULLY IMPLEMENTED').key, 'fulfilled');
  assert.equal(utilsMod.formatAssuranceStatus('FIMP').key, 'fulfilled');
  assert.equal(utilsMod.formatAssuranceStatus('PARTLY IMPLEMENTED').key, 'partly');
  assert.equal(utilsMod.formatAssuranceStatus('DROPPED').key, 'dropped');

  const extracted = utilsMod.extractAssuranceMetrics({
    ministry: 'Road Transport and Highways',
    house: 'Lok Sabha',
    session: '7',
    assurance_ref: 'SQ-18/07/544',
    promise_date: '2026-04-02',
    recorded_status: 'pending',
    days_elapsed: 239,
    original_promise:
      '(a) to (c) The process for preparation of Detailed Project Report (DPR) for development of NH-11 from Jhunjhunu to Fatehpur to four-lanes has been initiated in January, 2026.',
    followup_answer:
      'No Implementation Statement has been laid on the Table of the Lok Sabha; Digital Sansad still lists SQ-18/07/544 as PENDING.',
    member: 'Shri Brijendra Singh Ola',
    minister: 'Shri Nitin Jairam Gadkari',
    signal_category: 'overdue_pending_assurance',
  });

  assert.ok(extracted);
  assert.equal(extracted.assuranceRef, 'SQ-18/07/544');
  assert.equal(extracted.daysElapsed, 239);
  assert.equal(extracted.recordedStatus, 'pending');
  assert.match(extracted.originalPromise, /Jhunjhunu to Fatehpur/);
  assert.match(extracted.followupAnswer, /No Implementation Statement/);

  assert.equal(utilsMod.extractAssuranceMetrics({ unrelated: 1 }), null);
});

test('P6 visualiser helpers normalize recorded statuses, house counts, signals, and ministry filters', () => {
  assert.equal(
    visualsMod.P6_DEFAULT_HOOK,
    'A minister promised Parliament an answer. Did it ever get one?',
  );
  assert.equal(visualsMod.p6SignalLabel('overdue_pending_assurance'), 'Pending past 90-day norm');
  assert.equal(visualsMod.p6SignalLabel('partly_implemented_assurance'), 'Partly implemented');
  assert.equal(visualsMod.p6SignalLabel('dropped_without_fulfillment'), 'Dropped without fulfillment');
  assert.equal(
    visualsMod.p6SignalLabel('delayed_or_unreadable_followup'),
    'Delayed or unreadable follow-up',
  );

  const normalized = visualsMod.p6NormalizeRecordedStatuses({
    pending: 420,
    fully_implemented: 480,
    partly_implemented: 65,
    dropped: 35,
  });
  assert.deepEqual(normalized, {
    pending: 420,
    fulfilled: 480,
    partly: 65,
    dropped: 35,
    other: 0,
    total: 1000,
  });

  // Also accepts `fulfilled` alias
  assert.equal(visualsMod.p6NormalizeRecordedStatuses({ fulfilled: 12 }).fulfilled, 12);

  // Supports both object and numeric by_house shapes
  assert.deepEqual(
    visualsMod.p6SummarizeHouses({
      'Lok Sabha': { tasks: 650, pending: 280 },
      'Rajya Sabha': { tasks: 350, pending: 140 },
    }),
    { lokSabha: 650, rajyaSabha: 350 },
  );
  assert.deepEqual(
    visualsMod.p6SummarizeHouses({ lok_sabha: 600, rajya_sabha: 400 }),
    { lokSabha: 600, rajyaSabha: 400 },
  );

  const ministries = [
    { ministry: 'Road Transport and Highways', tasks: 22, done: 2, claimed: 0, open: 20, pct: 9.1, pending_count: 14, fulfilled_count: 8, leads: 1 },
    { ministry: 'Space', tasks: 12, done: 1, claimed: 0, open: 11, pct: 8.3, pending_count: 0, fulfilled_count: 12, leads: 0 },
  ];
  assert.equal(visualsMod.p6FilterMinistries(ministries, 'road', 'all').length, 1);
  assert.equal(visualsMod.p6FilterMinistries(ministries, '', 'pending').length, 1);
  assert.equal(visualsMod.p6FilterMinistries(ministries, '', 'fulfilled').length, 2);
});

test('API client normalizes P6 mission fallback code and citation role cleanly', () => {
  const mission = clientMod.normalizeMission({
    slug: 'parliaments-to-do-list',
    title: "Parliament's To-Do List",
    methodology_code: 'P6',
    pillar: 'parliament',
    total_tasks: 1000,
    completed_tasks: 0,
    verified_findings_count: 0,
  });
  assert.equal(mission.project_code, 'P6');
  assert.equal(mission.pillar, 'parliament');

  const finding = clientMod.normalizeFinding({
    id: 'f-p6-1',
    methodology_code: 'P6',
    title: 'Pending assurance SQ-18/07/544',
    summary: 'Original promise vs recorded status vs follow-up.',
    citations: [
      {
        role: 'original_promise',
        document: 'Original Promise',
        source_url: 'https://sansad.in/api_ls/govtAssurance/assurances?loksabha=18&session=7&qNo=544&qType=SQ&page=1&size=20&locale=en',
        printed_line: 'DPR initiated in January 2026.',
      },
    ],
  });
  assert.equal(finding.citations[0].role, 'original_promise');
  assert.equal(finding.citations[0].title, 'Original Promise');
  assert.equal(finding.citations[0].excerpt, 'DPR initiated in January 2026.');
});

test('P6 drawer and card headline metrics show recorded status ahead of monetary_exposure_inr: 0 while leaving non-P6 unchanged', () => {
  const pendingP6 = {
    methodology_code: 'P6',
    monetary_exposure_inr: 0,
    amount_label: null,
    anomaly_metrics: {
      recorded_status: 'pending',
      days_elapsed: 480,
      original_promise: 'The Government is actively engaged with manuscript stakeholders.',
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(pendingP6), {
    label: 'Recorded Status',
    value: 'Pending · 480d',
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(pendingP6), {
    value: 'Pending · 480d',
    prominent: true,
  });

  const fulfilledP6 = {
    methodology_code: 'P6',
    monetary_exposure_inr: 0,
    anomaly_metrics: {
      recorded_status: 'FULLY IMPLEMENTED',
      days_elapsed: null,
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(fulfilledP6), {
    label: 'Recorded Status',
    value: 'Fully implemented',
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(fulfilledP6), {
    value: 'Fully implemented',
    prominent: true,
  });

  // Even if monetary_exposure_inr were non-zero, P6 status still takes precedence
  const droppedP6 = {
    methodology_code: 'P6',
    monetary_exposure_inr: 100000,
    anomaly_metrics: {
      recorded_status: 'DROPPED',
      days_elapsed: 215,
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(droppedP6), {
    label: 'Recorded Status',
    value: 'Dropped · 215d',
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(droppedP6), {
    value: 'Dropped · 215d',
    prominent: true,
  });

  // Quarantined P6 finding stays withheld on card
  assert.deepEqual(
    utilsMod.resolveFindingCardMetric({ ...pendingP6, quarantined: true }, 'Withheld'),
    { value: 'Withheld', prominent: false },
  );

  // Non-P6 findings remain completely unchanged
  const p1Monetary = {
    methodology_code: 'P1',
    monetary_exposure_inr: 180_000_000,
    amount_label: 'budget_variance',
    anomaly_metrics: { pct_difference: -16.5 },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(p1Monetary), {
    label: 'budget variance',
    value: utilsMod.formatInr(180_000_000),
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(p1Monetary), {
    value: utilsMod.formatInr(180_000_000),
    prominent: true,
  });

  const nonP6Zero = {
    methodology_code: 'P5',
    monetary_exposure_inr: 0,
    amount_label: null,
    anomaly_metrics: { single_teacher_schools: 120 },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(nonP6Zero), {
    label: 'Reported Figure',
    value: '₹0',
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(nonP6Zero), {
    value: 'Disclosure lead',
    prominent: false,
  });

  const nonP6Null = {
    methodology_code: 'W1',
    monetary_exposure_inr: null,
    amount_label: null,
    anomaly_metrics: {},
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(nonP6Null), {
    label: 'Reported Figure',
    value: 'Disclosure Lead',
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(nonP6Null), {
    value: 'Disclosure lead',
    prominent: false,
  });
});

test('p6MinistryFilterQuery formats exact `${ministry} ·` prefix and has zero substring collisions across all 1,000 P6 tasks', () => {
  assert.equal(visualsMod.p6MinistryFilterQuery('Culture'), 'Culture ·');
  assert.equal(
    visualsMod.p6MinistryFilterQuery('  Road Transport and Highways  '),
    'Road Transport and Highways ·',
  );
  assert.equal(visualsMod.p6MinistryFilterQuery('   '), '');

  const projectPath = path.resolve(webDir, '../projects/parliaments-to-do-list.json');
  const project = JSON.parse(fs.readFileSync(projectPath, 'utf8'));
  const tasks = project.tasks;
  assert.equal(tasks.length, 1000);

  const byMinistry = new Map();
  for (const t of tasks) {
    const ministry = t.payload.context.parliaments_to_do_list.ministry;
    assert.ok(
      t.title.startsWith(`${ministry} · `),
      `Task ${t.id} title ${JSON.stringify(t.title)} must start with "${ministry} · "`,
    );
    if (!byMinistry.has(ministry)) byMinistry.set(ministry, []);
    byMinistry.get(ministry).push(t);
  }
  assert.equal(byMinistry.size, 56);

  // Verify zero substring collisions across all 56 ministries using the same case-insensitive
  // substring matching used by `desk.py` (`LOWER(t.title) LIKE '%needle%'`).
  for (const [ministry, expectedTasks] of byMinistry.entries()) {
    const needle = visualsMod.p6MinistryFilterQuery(ministry).toLowerCase();
    const matched = tasks.filter(
      (t) =>
        t.title.toLowerCase().includes(needle) ||
        String(t.id).toLowerCase().includes(needle),
    );
    assert.equal(
      matched.length,
      expectedTasks.length,
      `Filter query ${JSON.stringify(needle)} matched ${matched.length} tasks instead of ${expectedTasks.length} for ${ministry}`,
    );
    for (const m of matched) {
      assert.equal(m.payload.context.parliaments_to_do_list.ministry, ministry);
    }
  }

  // Explicit regression check for the reported Culture vs Agriculture and Farmers Welfare collision
  const cultureNeedle = visualsMod.p6MinistryFilterQuery('Culture').toLowerCase();
  const cultureMatches = tasks.filter((t) => t.title.toLowerCase().includes(cultureNeedle));
  assert.ok(cultureMatches.length > 0);
  assert.equal(
    cultureMatches.some((t) =>
      t.payload.context.parliaments_to_do_list.ministry.includes('Agriculture'),
    ),
    false,
  );
});
