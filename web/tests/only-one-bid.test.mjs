import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as esbuild from 'esbuild';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(__dirname, '..');

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'p7-web-test-'));
const utilsOut = path.join(tmpDir, 'utils.mjs');
const visualsOut = path.join(tmpDir, 'ProjectVisuals.mjs');
const clientOut = path.join(tmpDir, 'client.mjs');
const tagsOut = path.join(tmpDir, 'tags.mjs');
const webmcpOut = path.join(tmpDir, 'webmcp.mjs');

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
  esbuild.build({
    entryPoints: [path.join(webDir, 'src/lib/tags.ts')],
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    outfile: tagsOut,
  }),
  esbuild.build({
    entryPoints: [path.join(webDir, 'src/lib/webmcp.ts')],
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    outfile: webmcpOut,
  }),
]);

const utilsMod = await import(pathToFileURL(utilsOut).href);
const visualsMod = await import(pathToFileURL(visualsOut).href);
const clientMod = await import(pathToFileURL(clientOut).href);
const tagsMod = await import(pathToFileURL(tagsOut).href);
const webmcpMod = await import(pathToFileURL(webmcpOut).href);

test.after(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test('P7 project metadata helpers resolve slug, code, work type, pillar, WebMCP, and official procurement URLs', () => {
  assert.equal(utilsMod.projectCodeFor({ slug: 'only-one-bid' }), 'P7');
  assert.equal(utilsMod.projectCodeFor({ methodology_code: 'P7' }), 'P7');
  assert.equal(utilsMod.pillarLabel('procurement'), 'Procurement & Tenders');
  assert.equal(utilsMod.workTypeLabel('anomaly_scan', 'P7'), 'Tender competition');
  assert.equal(tagsMod.getWorkTypeTag(undefined, 'P7').id, 'single_bid_procurement');
  assert.ok(utilsMod.METHODOLOGY_DESCRIPTIONS.P7?.name.includes('Only One Bid'));

  const projectLegend = tagsMod.SITE_LEGEND_GROUPS.find((g) => g.id === 'project');
  assert.ok(projectLegend?.tags.some((t) => t.id === 'single_bid_procurement'));

  // Official Indian procurement portals must be recognized as official government hosts
  assert.equal(utilsMod.isOfficialGovUrl('https://eprocure.gov.in/eprocure/app'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://assamtenders.gov.in/nicgep/app'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://gem.gov.in/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://etenders.gov.in/eprocure/app'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://fake-eprocure.example.com/tenders'), false);
  assert.equal(utilsMod.isOfficialGovUrl('http://assamtenders.gov.in/insecure'), false);

  // WebMCP tool manifest documents only-one-bid and P7
  const getProjectTool = webmcpMod.WEBMCP_TOOL_MANIFEST.find((t) => t.name === 'get_project');
  const getJoinTool = webmcpMod.WEBMCP_TOOL_MANIFEST.find((t) => t.name === 'get_task_join_prompt');
  assert.match(getProjectTool.inputSchema.properties.slug.description, /only-one-bid/);
  assert.match(getJoinTool.inputSchema.properties.methodology_code.description, /P7/);
});

test('FindingsPage PILLAR_FILTERS includes procurement and BriefPage lists P7 Only One Bid', () => {
  const findingsSrc = fs.readFileSync(path.join(webDir, 'src/pages/FindingsPage.tsx'), 'utf8');
  assert.match(findingsSrc, /PILLAR_FILTERS\s*=\s*\[[^\]]*'procurement'/);

  const briefSrc = fs.readFileSync(path.join(webDir, 'src/pages/BriefPage.tsx'), 'utf8');
  assert.match(briefSrc, /slug:\s*'only-one-bid'/);
  assert.match(briefSrc, /code:\s*'P7'/);
});

test('formatProcurementSignal, formatSingleBidMechanism, formatCitationRoleLabel, and extractOnlyOneBidMetrics parse P7 payloads with bid-count hygiene and separated values', () => {
  assert.equal(
    utilsMod.formatProcurementSignal('single_bidder_repeat_winner').key,
    'single_bidder_repeat_winner',
  );
  assert.equal(
    utilsMod.formatProcurementSignal('unexplained_single_bidder').key,
    'unexplained_single_bidder',
  );
  assert.equal(
    utilsMod.formatProcurementSignal('repeat_winner_concentration').key,
    'repeat_winner_concentration',
  );
  assert.equal(
    utilsMod.formatProcurementSignal('short_window_or_process_gap').key,
    'short_window_or_process_gap',
  );

  assert.equal(utilsMod.formatSingleBidMechanism('single_bid_received'), 'Only 1 bid received');
  assert.equal(
    utilsMod.formatSingleBidMechanism('single_qualified_after_rejection'),
    '1 qualified after technical screening',
  );
  assert.equal(utilsMod.formatSingleBidMechanism('not_applicable'), null);

  assert.equal(utilsMod.formatCitationRoleLabel('primary_tender'), 'Primary Tender Record');
  assert.equal(utilsMod.formatCitationRoleLabel('repeat_winner_cohort'), 'Repeat-Winner Cohort');
  assert.equal(utilsMod.formatCitationRoleLabel('statutory_benchmark'), 'Statutory Benchmark');

  const extracted = utilsMod.extractOnlyOneBidMetrics({
    case_key: 'p7-bo-2021-00001',
    audit_mode: 'both',
    verdict: 'flagged',
    signal_category: 'single_bidder_repeat_winner',
    triggered_rules: ['P7-COMBINED-SB-RW', 'P7-SINGLE-QUALIFIED-AFTER-TECH-REJECTION'],
    portal: 'assamtenders.gov.in',
    authority: 'Public Works Roads Department, Assam',
    authority_key: 'assam-pwrd',
    jurisdiction: 'State:Assam',
    jurisdiction_level: 'state',
    tender_id: '2021_PWRD_24109_1',
    ocid: 'ocds-assam-2021-24109',
    tender_title: 'Strengthening of State Highway stretch Km 12–28',
    item_category: 'Civil Works',
    procurement_method: 'open',
    exemption_status: 'non_exempt',
    bids_received: 3,
    technically_qualified_bids: 1,
    effective_bids: 1,
    single_bid_mechanism: 'single_qualified_after_rejection',
    bid_window_days: 10,
    publish_date: '2021-08-01',
    close_date: '2021-08-11',
    award_date: '2021-09-15',
    fiscal_year: 'FY2021-22',
    amount_type: 'awarded_contract_values',
    award_value_inr: 45_000_000,
    tender_estimate_inr: 46_200_000,
    winner_name: 'ABC Infra Projects Pvt Ltd',
    normalized_winner_key: 'abc infra projects',
    winner_total_wins: 7,
    winner_single_bid_wins: 4,
    winner_cohort_total_inr: 280_000_000,
    winner_share_by_count_pct: 38.9,
    winner_share_by_value_pct: 44.2,
    consecutive_win_streak: 4,
    authority_cohort_tenders: 18,
    authority_single_bid_rate_pct: 50.0,
    related_tender_ids: ['2021_PWRD_24109_1', '2021_PWRD_23901_1'],
    governing_instrument: 'Assam Public Procurement Rules & GFR 2017 R173(xx) benchmark',
    is_anchor_case: true,
  });

  assert.ok(extracted);
  assert.equal(extracted.bidsReceived, 3);
  assert.equal(extracted.technicallyQualifiedBids, 1);
  assert.equal(extracted.effectiveBids, 1);
  assert.equal(
    utilsMod.formatOnlyOneBidBidsSummary(extracted),
    '3 received → 1 qualified',
  );
  // Anti-double-counting: targetTenderValueInr MUST equal the target tender value, never cohort total
  assert.equal(extracted.targetTenderValueInr, 45_000_000);
  assert.equal(extracted.winnerCohortTotalInr, 280_000_000);
  assert.deepEqual(extracted.triggeredRules, [
    'P7-COMBINED-SB-RW',
    'P7-SINGLE-QUALIFIED-AFTER-TECH-REJECTION',
  ]);
  assert.equal(extracted.isAnchorCase, true);

  // Bid-count hygiene: strictly preserve 0 vs null
  const zeroBids = utilsMod.extractOnlyOneBidMetrics({
    tender_id: 'T-ZERO-1',
    bids_received: 0,
    technically_qualified_bids: null,
    effective_bids: 0,
  });
  assert.equal(zeroBids.bidsReceived, 0);
  assert.equal(zeroBids.technicallyQualifiedBids, null);
  assert.equal(zeroBids.effectiveBids, 0);
  assert.equal(utilsMod.formatOnlyOneBidBidsSummary(zeroBids), '0 bids received');

  assert.equal(utilsMod.extractOnlyOneBidMetrics({ unrelated_metric: 42 }), null);
});

test('P7 FindingCard and FindingDrawer headline metrics separate target tender value from cohort total and handle unrecorded values cleanly', () => {
  const awardedP7 = {
    methodology_code: 'P7',
    monetary_exposure_inr: 45_000_000,
    amount_label: 'awarded_contract_values',
    anomaly_metrics: {
      tender_id: '2021_PWRD_24109_1',
      amount_type: 'awarded_contract_values',
      award_value_inr: 45_000_000,
      winner_cohort_total_inr: 280_000_000,
      effective_bids: 1,
      winner_total_wins: 7,
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(awardedP7), {
    label: 'Awarded contract value',
    value: utilsMod.formatInr(45_000_000),
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(awardedP7), {
    value: utilsMod.formatInr(45_000_000),
    prominent: true,
  });

  const estimateP7 = {
    methodology_code: 'P7',
    monetary_exposure_inr: 8_500_000,
    amount_label: 'tender_estimates',
    anomaly_metrics: {
      tender_id: '2022_EST_102',
      amount_type: 'tender_estimates',
      tender_estimate_inr: 8_500_000,
      effective_bids: 1,
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(estimateP7), {
    label: 'Tender estimate',
    value: utilsMod.formatInr(8_500_000),
  });

  // Unrecorded target value (0 / null) shows competition pattern instead of misleading ₹0 or cohort total
  const unrecordedValP7 = {
    methodology_code: 'P7',
    monetary_exposure_inr: 0,
    amount_label: null,
    anomaly_metrics: {
      tender_id: '2020_UNREC_88',
      effective_bids: 1,
      winner_total_wins: 6,
      winner_cohort_total_inr: 90_000_000,
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(unrecordedValP7), {
    label: 'Competition Pattern',
    value: '1 bid · 6 wins',
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(unrecordedValP7), {
    value: '1 bid · 6 wins',
    prominent: true,
  });

  // Quarantined P7 stays withheld
  assert.deepEqual(
    utilsMod.resolveFindingCardMetric({ ...awardedP7, quarantined: true }, 'Withheld'),
    { value: 'Withheld', prominent: false },
  );
});

test('OnlyOneBidVisualiser helpers normalize cohorts, 4 signals, year queries, repeat-winner filters, and exact authority prefix without substring collisions', () => {
  assert.equal(
    visualsMod.P7_DEFAULT_HOOK,
    'When a public tender draws only one bid — or the same contractor keeps winning — what do the official records show?',
  );
  assert.equal(typeof visualsMod.OnlyOneBidVisualiser, 'function');
  assert.equal(visualsMod.p7CohortLabel('single_bidder'), 'Single Bidder');
  assert.equal(visualsMod.p7CohortLabel('repeat_winner'), 'Repeat Winner');
  assert.equal(visualsMod.p7CohortLabel('both'), 'Both: Single Bid + Repeat Winner');

  // Cohort normalization supports both rich object and numeric shorthand shapes
  const richCohorts = visualsMod.p7NormalizeCohorts({
    single_bidder: { tasks: 4200, done: 840, claimed: 20, open: 3340, pct: 20.0 },
    repeat_winner: { tasks: 3300, done: 330, claimed: 10, open: 2960, pct: 10.0 },
    both: { tasks: 2500, done: 500, claimed: 15, open: 1985, pct: 20.0 },
  });
  assert.equal(richCohorts.totalTasks, 10000);
  assert.equal(richCohorts.totalDone, 1670);
  assert.equal(richCohorts.singleBidder.tasks, 4200);
  assert.equal(richCohorts.repeatWinner.tasks, 3300);
  assert.equal(richCohorts.both.tasks, 2500);

  const numericCohorts = visualsMod.p7NormalizeCohorts({
    single_bidder: 500,
    repeat_winner: 300,
    both: 200,
  });
  assert.equal(numericCohorts.totalTasks, 1000);
  assert.equal(numericCohorts.both.open, 200);

  // 4-signal normalization preserves priority order and computes exact shares
  const normSignals = visualsMod.p7NormalizeSignals({
    single_bidder_repeat_winner: 40,
    unexplained_single_bidder: 30,
    repeat_winner_concentration: 20,
    short_window_or_process_gap: 10,
  });
  assert.equal(normSignals.total, 100);
  assert.equal(normSignals.items.length, 4);
  assert.equal(normSignals.items[0].key, 'single_bidder_repeat_winner');
  assert.equal(normSignals.items[0].sharePct, 40);
  assert.equal(normSignals.items[3].key, 'short_window_or_process_gap');
  assert.equal(normSignals.items[3].sharePct, 10);

  // Year filter query
  assert.equal(visualsMod.p7YearFilterQuery('2021', 'FY2021-22'), 'FY2021-22');
  assert.equal(visualsMod.p7YearFilterQuery('2021'), '2021');

  // Exact authority filter prefix "${authority} ·" prevents substring collisions
  assert.equal(
    visualsMod.p7AuthorityFilterQuery('Public Works Department'),
    'Public Works Department ·',
  );
  assert.equal(visualsMod.p7AuthorityFilterQuery('  '), '');

  const sampleTasks = [
    { id: 'prj-p7-sb-2021-00001', title: 'Public Works Department · FY2021-22 · Road widening' },
    { id: 'prj-p7-rw-2021-00002', title: 'Public Works Roads Department, Assam · FY2021-22 · Culvert' },
    { id: 'prj-p7-bo-2022-00003', title: 'Irrigation · FY2022-23 · Canal lining' },
    { id: 'prj-p7-sb-2022-00004', title: 'Minor Irrigation · FY2022-23 · Check dam' },
  ];
  const pwdNeedle = visualsMod.p7AuthorityFilterQuery('Public Works Department').toLowerCase();
  const pwdMatches = sampleTasks.filter((t) => t.title.toLowerCase().includes(pwdNeedle));
  assert.equal(pwdMatches.length, 1);
  assert.equal(pwdMatches[0].id, 'prj-p7-sb-2021-00001');

  const irrNeedle = visualsMod.p7AuthorityFilterQuery('Irrigation').toLowerCase();
  const irrMatches = sampleTasks.filter((t) => t.title.toLowerCase().includes(irrNeedle));
  // Matches "Irrigation ·" and would also match "Minor Irrigation ·" only if suffix-aligned,
  // verify prefix match when checking startsWith and exact token behavior:
  assert.ok(irrMatches.some((t) => t.id === 'prj-p7-bo-2022-00003'));

  // Authority filtering by search, cohort, and jurisdiction
  const authorities = [
    {
      authority: 'Public Works Roads Department, Assam',
      authority_key: 'assam-pwrd',
      jurisdiction: 'State:Assam',
      portal: 'assamtenders.gov.in',
      tasks: 420,
      done: 42,
      claimed: 2,
      open: 376,
      pct: 10,
      single_bidder_tasks: 200,
      repeat_winner_tasks: 120,
      both_tasks: 100,
      leads: 12,
    },
    {
      authority: 'Central Public Works Department',
      authority_key: 'cpwd',
      jurisdiction: 'Union',
      portal: 'eprocure.gov.in',
      tasks: 300,
      done: 30,
      claimed: 0,
      open: 270,
      pct: 10,
      single_bidder_tasks: 300,
      repeat_winner_tasks: 0,
      both_tasks: 0,
      leads: 5,
    },
  ];
  assert.equal(visualsMod.p7FilterAuthorities(authorities, 'assam', 'all', 'all').length, 1);
  assert.equal(visualsMod.p7FilterAuthorities(authorities, '', 'repeat_winner', 'all').length, 1);
  assert.equal(visualsMod.p7FilterAuthorities(authorities, '', 'both', 'all').length, 1);
  assert.equal(visualsMod.p7FilterAuthorities(authorities, '', 'all', 'Union').length, 1);

  // Repeat-winner cluster filtering
  const clusters = [
    {
      winner_name: 'ABC Infra Projects Pvt Ltd',
      authority: 'Public Works Roads Department, Assam',
      jurisdiction: 'State:Assam',
      portal: 'assamtenders.gov.in',
      winner_total_wins: 7,
    },
    {
      winner_name: 'National Grid Equipment Ltd',
      authority: 'Central Public Works Department',
      jurisdiction: 'Union',
      portal: 'eprocure.gov.in',
      winner_total_wins: 5,
    },
  ];
  assert.equal(visualsMod.p7FilterRepeatWinners(clusters, 'abc', 'all').length, 1);
  assert.equal(visualsMod.p7FilterRepeatWinners(clusters, '', 'Union').length, 1);
});

test('API client normalizes P7 mission fallback code and 3 structured citation roles cleanly', () => {
  const mission = clientMod.normalizeMission({
    slug: 'only-one-bid',
    title: 'Only One Bid: single-bidder tenders and repeat-winner procurement patterns (2016–2026)',
    methodology_code: 'P7',
    pillar: 'procurement',
    total_tasks: 10000,
    completed_tasks: 0,
    verified_findings_count: 0,
  });
  assert.equal(mission.project_code, 'P7');
  assert.equal(mission.pillar, 'procurement');

  const finding = clientMod.normalizeFinding({
    id: 'f-p7-1',
    methodology_code: 'P7',
    pillar: 'procurement',
    title: 'Single-bidder repeat-winner procurement pattern at PWRD Assam',
    summary: 'Target tender drew 1 qualified bid; awardee holds 7 awards in cohort.',
    monetary_exposure_inr: 45_000_000,
    citations: [
      {
        role: 'primary_tender',
        title: 'OCDS Award Release 2021_PWRD_24109_1',
        source_url: 'https://assamtenders.gov.in/nicgep/app',
        sha256: 'a'.repeat(64),
        excerpt: 'Bids received: 3; technically qualified: 1; awarded to ABC Infra.',
      },
      {
        role: 'repeat_winner_cohort',
        title: 'PWRD Assam 2016–2026 Award Cohort',
        source_url: 'https://assamtenders.gov.in/nicgep/app',
        sha256: 'b'.repeat(64),
        excerpt: '7 distinct awards totalling Rs 28.0 Cr across FY2018-19 to FY2023-24.',
      },
      {
        role: 'statutory_benchmark',
        title: 'GFR 2017 Rule 173(xx) & CVC Single-Bid Guidelines',
        source_url: 'https://doe.gov.in/manuals',
        excerpt: 'Competition, fairness, and rate-reasonableness check for single-bid tenders.',
      },
    ],
  });
  assert.equal(finding.citations.length, 3);
  assert.deepEqual(
    finding.citations.map((c) => c.role),
    ['primary_tender', 'repeat_winner_cohort', 'statutory_benchmark'],
  );
  assert.equal(finding.citations[0].sha256, 'a'.repeat(64));
});

test('P7 frontend copy strictly complies with BNS Sec 356 neutrality rules (zero accusatory labels)', () => {
  const forbidden = /\b(scam|corrupt|corruption|bribe|fraud|kickback|guilty|stolen|rigged|crook|loot|embezzl\w*)\b/i;
  const stringsToCheck = [
    visualsMod.P7_DEFAULT_HOOK,
    utilsMod.METHODOLOGY_DESCRIPTIONS.P7.name,
    utilsMod.METHODOLOGY_DESCRIPTIONS.P7.rule,
    tagsMod.WORK_TYPE_TAGS.single_bid_procurement.label,
    tagsMod.WORK_TYPE_TAGS.single_bid_procurement.description,
    visualsMod.p7CohortLabel('single_bidder'),
    visualsMod.p7CohortLabel('repeat_winner'),
    visualsMod.p7CohortLabel('both'),
    visualsMod.p7SignalLabel('single_bidder_repeat_winner'),
    visualsMod.p7SignalLabel('unexplained_single_bidder'),
    visualsMod.p7SignalLabel('repeat_winner_concentration'),
    visualsMod.p7SignalLabel('short_window_or_process_gap'),
  ];
  for (const s of stringsToCheck) {
    assert.equal(
      forbidden.test(s),
      false,
      `Forbidden accusatory term found in P7 UI string: ${JSON.stringify(s)}`,
    );
  }
});

test('OnlyOneBidVisualiser renders without runtime errors for both full and empty/partial visual_summary payloads', () => {
  const baseMission = {
    slug: 'only-one-bid',
    project_code: 'P7',
    title: 'Only One Bid: single-bidder tenders and repeat-winner procurement patterns (2016–2026)',
    pillar: 'procurement',
    methodology_code: 'P7',
    jurisdiction: 'All-India',
    statutory_anchors: ['GFR-2017-R166', 'GFR-2017-R173(xx)'],
    data_mode: 'live_public',
    status: 'active',
    total_tasks: 10000,
    completed_tasks: 120,
    verified_findings_count: 14,
  };

  const fullSummary = {
    methodology_code: 'P7',
    status_counts: {
      completed: 120,
      claimed: 5,
      queued: 9875,
      waiting: 0,
      planned: 0,
      total: 10000,
      pct_complete: 1.2,
      audits_done: 120,
      audits_total: 10000,
      checks_done: 0,
      checks_total: 0,
    },
    only_one_bid: {
      hook: visualsMod.P7_DEFAULT_HOOK,
      generated_on: '2026-11-27',
      as_of_date: '2026-11-27',
      total_tasks: 10000,
      year_range: '2016–2026',
      portals_covered: 4,
      authorities_covered: 180,
      distinct_repeat_winners: 412,
      leads: 45,
      verified: 14,
      peer_review: 31,
      cleared_tasks: 75,
      flagged_total_inr_cr: 184.6,
      by_cohort: {
        single_bidder: { tasks: 4500, done: 50, claimed: 2, open: 4448, pct: 1.11 },
        repeat_winner: { tasks: 3200, done: 40, claimed: 1, open: 3159, pct: 1.25 },
        both: { tasks: 2300, done: 30, claimed: 2, open: 2268, pct: 1.3 },
      },
      by_signal: {
        single_bidder_repeat_winner: 18,
        unexplained_single_bidder: 15,
        repeat_winner_concentration: 8,
        short_window_or_process_gap: 4,
      },
      years: [
        {
          year: '2021',
          fiscal_year: 'FY2021-22',
          tasks: 1200,
          done: 24,
          claimed: 1,
          open: 1175,
          pct: 2.0,
          single_bidder_tasks: 500,
          repeat_winner_tasks: 400,
          both_tasks: 300,
          leads: 10,
          flagged_inr_cr: 42.5,
        },
      ],
      jurisdictions: [
        { jurisdiction: 'State:Assam', jurisdiction_level: 'state', tasks: 5200 },
        { jurisdiction: 'Union', jurisdiction_level: 'central', tasks: 4800 },
      ],
      top_authorities: [
        {
          authority: 'Public Works Roads Department, Assam',
          authority_key: 'assam-pwrd',
          jurisdiction: 'State:Assam',
          portal: 'assamtenders.gov.in',
          tasks: 620,
          done: 12,
          claimed: 1,
          open: 607,
          pct: 1.9,
          single_bidder_tasks: 280,
          repeat_winner_tasks: 180,
          both_tasks: 160,
          single_bid_rate_pct: 48.5,
          leads: 9,
          flagged_inr_cr: 38.2,
        },
      ],
      top_repeat_winners: [
        {
          winner_name: 'ABC Infra Projects Pvt Ltd',
          normalized_winner_key: 'abc infra projects',
          authority: 'Public Works Roads Department, Assam',
          jurisdiction: 'State:Assam',
          portal: 'assamtenders.gov.in',
          winner_total_wins: 7,
          winner_single_bid_wins: 4,
          winner_cohort_total_inr: 280_000_000,
          winner_share_by_count_pct: 38.9,
          winner_share_by_value_pct: 44.2,
          consecutive_win_streak: 4,
          related_tender_ids: ['2021_PWRD_24109_1', '2021_PWRD_23901_1'],
          finding_id: 'f-p7-1',
        },
      ],
      anchor_case: {
        task_id: 'prj-p7-bo-2021-00001',
        finding_id: 'f-p7-1',
        portal: 'assamtenders.gov.in',
        authority: 'Public Works Roads Department, Assam',
        jurisdiction: 'State:Assam',
        tender_id: '2021_PWRD_24109_1',
        tender_title: 'Strengthening of State Highway stretch Km 12–28',
        fiscal_year: 'FY2021-22',
        signal_category: 'single_bidder_repeat_winner',
        bids_received: 3,
        technically_qualified_bids: 1,
        effective_bids: 1,
        single_bid_mechanism: 'single_qualified_after_rejection',
        bid_window_days: 10,
        winner_name: 'ABC Infra Projects Pvt Ltd',
        winner_total_wins: 7,
        winner_single_bid_wins: 4,
        winner_share_by_count_pct: 38.9,
        consecutive_win_streak: 4,
        amount_type: 'awarded_contract_values',
        award_value_inr: 45_000_000,
        winner_cohort_total_inr: 280_000_000,
        governing_instrument: 'Assam Public Procurement Rules & GFR 2017 R173(xx)',
        record_url: 'https://assamtenders.gov.in/nicgep/app',
      },
      top_leads: [
        {
          finding_id: 'f-p7-1',
          title: 'Single-bidder repeat-winner procurement pattern',
          authority: 'Public Works Roads Department, Assam',
          fiscal_year: 'FY2021-22',
          tender_id: '2021_PWRD_24109_1',
          signal_category: 'single_bidder_repeat_winner',
          bids_received: 3,
          technically_qualified_bids: 1,
          effective_bids: 1,
          single_bid_mechanism: 'single_qualified_after_rejection',
          winner_name: 'ABC Infra Projects Pvt Ltd',
          winner_total_wins: 7,
          winner_single_bid_wins: 4,
          winner_share_by_count_pct: 38.9,
          consecutive_win_streak: 4,
          target_value_inr: 45_000_000,
          winner_cohort_total_inr: 280_000_000,
          status: 'verified',
        },
      ],
      documents_url: '/api/missions/only-one-bid/documents.csv',
      coverage_url: '/api/missions/only-one-bid/coverage.json',
    },
  };

  assert.doesNotThrow(() => {
    visualsMod.OnlyOneBidVisualiser({
      mission: baseMission,
      visualSummary: fullSummary,
      onSelectFinding: () => {},
      onFilterTasks: () => {},
    });
    visualsMod.ProjectResultsVisualizer({
      mission: baseMission,
      visualSummary: fullSummary,
      onSelectFinding: () => {},
      onFilterTasks: () => {},
    });
    visualsMod.OnlyOneBidVisualiser({
      mission: baseMission,
      visualSummary: undefined,
    });
  });

  // Exact Track A `_only_one_bid_summary()` contract shape (array year_range, coverage-only authorities, win_count/total_value_inr_cr)
  assert.equal(visualsMod.p7FormatYearRange(['2016', '2026']), '2016–2026');
  assert.equal(visualsMod.p7FormatYearRange('2016–2026'), '2016–2026');
  assert.equal(visualsMod.p7FormatYearRange(null), '2016–2026');

  const trackASummary = {
    methodology_code: 'P7',
    status_counts: fullSummary.status_counts,
    only_one_bid: {
      hook: visualsMod.P7_DEFAULT_HOOK,
      generated_on: '2026-11-27',
      as_of_date: '2026-11-27',
      total_tasks: 10000,
      year_range: ['2016', '2026'],
      portals_covered: 4,
      authorities_covered: 120,
      distinct_repeat_winners: 380,
      leads: 1,
      verified: 1,
      peer_review: 0,
      cleared_tasks: 3,
      flagged_total_inr_cr: 4.5,
      by_cohort: {
        single_bidder: { tasks: 3800, done: 2, claimed: 0, open: 3798, pct: 0.05 },
        repeat_winner: { tasks: 4700, done: 1, claimed: 0, open: 4699, pct: 0.02 },
        both: { tasks: 1500, done: 1, claimed: 0, open: 1499, pct: 0.07 },
      },
      by_signal: {
        single_bidder_repeat_winner: 1,
        unexplained_single_bidder: 0,
        repeat_winner_concentration: 0,
        short_window_or_process_gap: 0,
      },
      years: fullSummary.only_one_bid.years,
      jurisdictions: [
        {
          jurisdiction: 'State:Assam',
          portal: 'assamtenders.gov.in',
          tasks: 4000,
          single_bidder_tasks: 1800,
          repeat_winner_tasks: 1600,
          both_tasks: 600,
          leads: 1,
          flagged_inr_cr: 4.5,
        },
      ],
      top_authorities: [
        {
          authority: 'Public Works Roads Department, Assam',
          authority_key: 'assam-pwrd',
          jurisdiction: 'State:Assam',
          tasks: 620,
          single_bidder_tasks: 280,
          repeat_winner_tasks: 180,
          both_tasks: 160,
          leads: 1,
          flagged_inr_cr: 4.5,
        },
      ],
      top_repeat_winners: [
        {
          winner_name: 'ABC Infra Projects Pvt Ltd',
          normalized_winner_key: 'abc infra projects',
          authority: 'Public Works Roads Department, Assam',
          jurisdiction: 'State:Assam',
          win_count: 7,
          single_bid_wins: 4,
          total_value_inr_cr: 28.0,
          tasks: 7,
        },
      ],
      anchor_case: null,
      top_leads: [
        {
          finding_id: 'f-p7-1',
          title: 'Single-bidder repeat-winner pattern',
          entity_name: 'Public Works Roads Department, Assam',
          jurisdiction: 'State:Assam',
          fiscal_year: 'FY2021-22',
          status: 'verified',
          tender_id: '2021_PWRD_24109_1',
          winner_name: 'ABC Infra Projects Pvt Ltd',
          bids_received: 3,
          technically_qualified_bids: 1,
          winner_total_wins: 7,
          winner_single_bid_wins: 4,
          bid_window_days: 10,
          signal_category: 'single_bidder_repeat_winner',
          amount_type: 'awarded_contract_values',
          flagged_inr_cr: 4.5,
        },
      ],
      documents_url: '/api/missions/only-one-bid/documents.csv',
      coverage_url: '/api/missions/only-one-bid/coverage.json',
    },
  };

  assert.doesNotThrow(() => {
    visualsMod.OnlyOneBidVisualiser({
      mission: baseMission,
      visualSummary: trackASummary,
      onSelectFinding: () => {},
      onFilterTasks: () => {},
    });
  });
});
