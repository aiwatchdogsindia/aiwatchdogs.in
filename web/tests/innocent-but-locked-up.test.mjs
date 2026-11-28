import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as esbuild from 'esbuild';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(__dirname, '..');

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'p10-web-test-'));
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

test('P10 project metadata helpers resolve slug, code, work type, justice pillar, WebMCP, and official court/prison URLs', () => {
  assert.equal(utilsMod.projectCodeFor({ slug: 'innocent-but-locked-up' }), 'P10');
  assert.equal(utilsMod.projectCodeFor({ methodology_code: 'P10' }), 'P10');
  assert.equal(utilsMod.pillarLabel('justice'), 'Courts & Custody');
  assert.equal(utilsMod.pillarTint('justice').label, 'Courts & Custody');
  assert.equal(utilsMod.workTypeLabel('anomaly_scan', 'P10'), 'Custody & release audit');
  assert.equal(tagsMod.getWorkTypeTag(undefined, 'P10').id, 'custody_overrun');
  assert.ok(utilsMod.METHODOLOGY_DESCRIPTIONS.P10?.name.includes('Innocent But Locked Up'));

  const projectLegend = tagsMod.SITE_LEGEND_GROUPS.find((g) => g.id === 'project');
  assert.ok(projectLegend?.tags.some((t) => t.id === 'custody_overrun'));

  // Official court, prison, NHRC, NALSA, Parliament & ministry URLs
  assert.equal(utilsMod.isOfficialGovUrl('https://www.sci.gov.in/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://api.sci.gov.in/supremecourt/2024/order.pdf'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://eprisons.nic.in/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://nhrc.nic.in/press-release'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://nalsa.gov.in/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://www.allahabadhighcourt.in/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://dslsa.org/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://jhalsa.org/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://pshrc.net/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://delhihighcourt.nic.in/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://sansad.in/getFile/loksabhaquestions/annex/18/AU1.pdf'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://indian-supreme-court-judgments.s3.amazonaws.com/data/pdf/year=2025/a.pdf'), false);
  assert.equal(utilsMod.isOfficialGovUrl('https://fake-allahabadhighcourt.in.example.com/order.pdf'), false);
  assert.equal(utilsMod.isOfficialGovUrl('https://fake-dslsa.org.example.com/order.pdf'), false);
  assert.equal(utilsMod.isOfficialGovUrl('http://www.sci.gov.in/insecure.pdf'), false);

  // WebMCP tool manifest documents innocent-but-locked-up and P10
  const getProjectTool = webmcpMod.WEBMCP_TOOL_MANIFEST.find((t) => t.name === 'get_project');
  const getJoinTool = webmcpMod.WEBMCP_TOOL_MANIFEST.find((t) => t.name === 'get_task_join_prompt');
  assert.match(getProjectTool.inputSchema.properties.slug.description, /innocent-but-locked-up/);
  assert.match(getJoinTool.inputSchema.properties.methodology_code.description, /P10/);
});

test('BriefPage lists P10, FindingsPage supports justice pillar, and MissionsPage wires P10', () => {
  const findingsSrc = fs.readFileSync(path.join(webDir, 'src/pages/FindingsPage.tsx'), 'utf8');
  assert.match(findingsSrc, /TOPICS[^=]*=\s*\[[\s\S]*code:\s*'P10'/);

  const briefSrc = fs.readFileSync(path.join(webDir, 'src/pages/BriefPage.tsx'), 'utf8');
  assert.match(briefSrc, /slug:\s*'innocent-but-locked-up'/);
  assert.match(briefSrc, /code:\s*'P10'/);

  const missionsSrc = fs.readFileSync(path.join(webDir, 'src/pages/MissionsPage.tsx'), 'utf8');
  assert.match(missionsSrc, /INNOCENT_BUT_LOCKED_UP_METHOD\s*=\s*'P10'/);
  assert.match(missionsSrc, /innocent_but_locked_up/);
});

test('P10 canonical enum formatters handle all 6 mechanisms, 8 portals, 4 court tiers, 4 chain statuses, order types, and overrun days', () => {
  // All 6 canonical custody overrun mechanisms + rule-code aliases
  assert.equal(
    utilsMod.formatCustodyOverrunMechanism('post_acquittal_contempt_overrun').key,
    'post_acquittal_contempt_overrun',
  );
  assert.equal(
    utilsMod.formatCustodyOverrunMechanism('P10-POST-ACQUITTAL-CONTEMPT-OVERRUN').key,
    'post_acquittal_contempt_overrun',
  );
  assert.equal(
    utilsMod.formatCustodyOverrunMechanism('bail_or_release_order_contempt').key,
    'bail_or_release_order_contempt',
  );
  assert.equal(
    utilsMod.formatCustodyOverrunMechanism('P10-RELEASE-ORDER-CONTEMPT-OVERRUN').key,
    'bail_or_release_order_contempt',
  );
  assert.equal(
    utilsMod.formatCustodyOverrunMechanism('warrant_or_faster_transmission_delay').key,
    'warrant_or_faster_transmission_delay',
  );
  assert.equal(
    utilsMod.formatCustodyOverrunMechanism('P10-WARRANT-TRANSMISSION-DELAY').key,
    'warrant_or_faster_transmission_delay',
  );
  assert.equal(
    utilsMod.formatCustodyOverrunMechanism('statutory_commission_detention_finding').key,
    'statutory_commission_detention_finding',
  );
  assert.equal(
    utilsMod.formatCustodyOverrunMechanism('P10-STATUTORY-UNLAWFUL-DETENTION').key,
    'statutory_commission_detention_finding',
  );
  assert.equal(
    utilsMod.formatCustodyOverrunMechanism('institutional_compliance_gap').key,
    'institutional_compliance_gap',
  );
  assert.equal(
    utilsMod.formatCustodyOverrunMechanism('P10-INSTITUTIONAL-COMPLIANCE-GAP').key,
    'institutional_compliance_gap',
  );
  const compliantMeta = utilsMod.formatCustodyOverrunMechanism('timely_release_or_compliant');
  assert.equal(compliantMeta.key, 'timely_release_or_compliant');
  assert.equal(compliantMeta.isCleared, true);

  // All 8 canonical portal categories
  const expectedPortals = [
    ['court_portal', 'Court Portal'],
    ['prison_portal', 'Prison Portal'],
    ['police_portal', 'Police Portal'],
    ['nhrc_shrc', 'NHRC / SHRC'],
    ['nalsa_slsa', 'NALSA / SLSA'],
    ['parliament', 'Parliament (Digital Sansad)'],
    ['govt_ministry', 'Govt Ministry (MHA / DoJ / PIB)'],
    ['cag_audit', 'CAG Audit'],
  ];
  for (const [key, label] of expectedPortals) {
    assert.equal(utilsMod.formatPortalCategory(key).label, label);
  }

  // All 4 court tiers
  assert.equal(utilsMod.formatCourtTier('supreme_court').shortLabel, 'Supreme Court');
  assert.equal(utilsMod.formatCourtTier('high_court').shortLabel, 'High Court');
  assert.equal(utilsMod.formatCourtTier('district_court').shortLabel, 'District Court');
  assert.equal(utilsMod.formatCourtTier('statutory_body').shortLabel, 'Statutory Body');

  // All 4 chain statuses
  assert.equal(utilsMod.formatChainStatus('full_4_stage_chain').shortLabel, '4/4 stages');
  assert.equal(utilsMod.formatChainStatus('partial_3_stage', 3).shortLabel, '3/4 stages');
  assert.equal(utilsMod.formatChainStatus('institutional_record').shortLabel, 'Institutional');
  assert.equal(utilsMod.formatChainStatus('compliant_control').shortLabel, 'Compliant');

  // Acquittal order types
  assert.equal(utilsMod.formatAcquittalOrderType('acquittal'), 'Acquittal Order');
  assert.equal(utilsMod.formatAcquittalOrderType('discharge'), 'Discharge Order');
  assert.equal(utilsMod.formatAcquittalOrderType('detention_quashed'), 'Detention Quashed');
  assert.equal(utilsMod.formatAcquittalOrderType('bail_release_order'), 'Bail / Release Order');
  assert.equal(utilsMod.formatAcquittalOrderType('conviction_set_aside'), 'Conviction Set Aside');

  // Overrun days formatting
  assert.equal(utilsMod.formatOverrunDays(1), '1 day');
  assert.equal(utilsMod.formatOverrunDays(18), '18 days');
  assert.equal(utilsMod.formatOverrunDays(420), '420 days (~1.1 yrs)');
  assert.equal(utilsMod.formatOverrunDays(0), '0 days');
  assert.equal(utilsMod.formatOverrunDays(null), null);
  assert.equal(utilsMod.formatOverrunDays(-3), null);

  // Citation role labels
  assert.equal(utilsMod.formatCitationRoleLabel('acquittal_and_custody_order'), 'Acquittal & Custody Order');
  assert.equal(utilsMod.formatCitationRoleLabel('contempt_and_release_outcome'), 'Contempt & Release Outcome');
  assert.equal(utilsMod.formatCitationRoleLabel('statutory_benchmark'), 'Statutory Benchmark');
});

test('extractInnocentButLockedUpMetrics parses full 4-stage anchor payload and returns null for unrelated metrics', () => {
  const extracted = utilsMod.extractInnocentButLockedUpMetrics({
    case_key: 'p10-court-2025-anchor-0001',
    case_title: 'Post-Acquittal Detention Contempt Review — District Jail Compliance',
    portal_category: 'court_portal',
    court_tier: 'high_court',
    state_ut: 'Uttar Pradesh',
    jurisdiction: 'State:Uttar Pradesh',
    year: 2025,
    document_date: '2025-06-18',
    document_ref: 'Contempt Application (Civil) No. 1234/2025',
    overrun_mechanism: 'post_acquittal_contempt_overrun',
    signal_category: 'post_acquittal_contempt_overrun',
    verdict: 'flagged_custody_overrun',
    triggered_rules: ['P10-POST-ACQUITTAL-CONTEMPT-OVERRUN'],
    stages_verified_count: 4,
    chain_status: 'full_4_stage_chain',
    stage_1_acquittal_verified: true,
    acquittal_order_date: '2025-05-01',
    acquittal_court: 'High Court of Judicature at Allahabad',
    acquittal_case_number: 'Criminal Appeal No. 410/2023',
    acquittal_order_type: 'acquittal',
    acquittal_quote: 'The appellant is acquitted of all charges and directed to be released forthwith.',
    stage_2_continued_detention_verified: true,
    prison_name: 'District Jail, Ghaziabad',
    custody_authority: 'Superintendent, District Jail',
    non_release_reason: 'Delay in electronic warrant verification despite FASTER transmission.',
    overrun_days: 28,
    overrun_window: '2025-05-01 to 2025-05-29',
    stage_3_contempt_verified: true,
    contempt_case_number: 'Contempt Application (Civil) No. 1234/2025',
    contempt_court: 'High Court of Judicature at Allahabad',
    contempt_filing_date: '2025-05-20',
    contempt_order_date: '2025-06-18',
    contempt_quote: 'Personal appearance of the Jail Superintendent directed; contempt notice issued.',
    stage_4_release_verified: true,
    release_date: '2025-05-29',
    release_verified: true,
    compensation_inr: 500000,
    judicial_direction_summary: 'Ordered Rs 5 lakh compensation and statewide FASTER release audit.',
    custody_order_excerpt: 'Acquitted of all charges; release warrant transmitted electronically.',
    custody_order_locator: 'Page 4, Para 12',
    custody_order_tokens_matched: ['acquitted', 'release forthwith', 'FASTER'],
    contempt_or_outcome_excerpt: 'Detainee released on 29.05.2025 following contempt notice; compensation awarded.',
    contempt_or_outcome_locator: 'Page 7, Para 19',
    contempt_or_outcome_tokens_matched: ['contempt', 'released', 'compensation'],
    statutory_anchor: 'Constitution of India Art. 21; Contempt of Courts Act 1971',
    is_anchor_case: true,
    as_of_date: '2026-11-28',
  });

  assert.ok(extracted);
  assert.equal(extracted.isAnchorCase, true);
  assert.equal(extracted.stagesVerifiedCount, 4);
  assert.equal(extracted.chainStatus, 'full_4_stage_chain');
  assert.equal(extracted.overrunDays, 28);
  assert.equal(extracted.compensationInr, 500000);
  assert.equal(extracted.releaseVerified, true);
  assert.deepEqual(extracted.triggeredRules, ['P10-POST-ACQUITTAL-CONTEMPT-OVERRUN']);
  assert.equal(utilsMod.extractInnocentButLockedUpMetrics({ unrelated_key: 42 }), null);
});

test('P10 FindingCard and FindingDrawer headline metrics prioritize overrun days & compensation and avoid misleading ₹0', () => {
  const overrunWithComp = {
    methodology_code: 'P10',
    monetary_exposure_inr: 500000,
    amount_label: 'custody_overrun_and_contempt',
    anomaly_metrics: {
      overrun_mechanism: 'post_acquittal_contempt_overrun',
      overrun_days: 28,
      compensation_inr: 500000,
      chain_status: 'full_4_stage_chain',
      stages_verified_count: 4,
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(overrunWithComp), {
    label: 'Custody Overrun',
    value: `+28 days · ${utilsMod.formatInr(500000)}`,
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(overrunWithComp), {
    value: `+28d overrun · ${utilsMod.formatInr(500000)}`,
    prominent: true,
  });

  const overrunNoComp = {
    methodology_code: 'P10',
    monetary_exposure_inr: 0,
    anomaly_metrics: {
      overrun_mechanism: 'bail_or_release_order_contempt',
      overrun_days: 1,
      compensation_inr: null,
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(overrunNoComp), {
    label: 'Custody Overrun',
    value: '+1 day',
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(overrunNoComp), {
    value: '+1d overrun',
    prominent: true,
  });

  const clearedBenchmark = {
    methodology_code: 'P10',
    monetary_exposure_inr: 0,
    status: 'cleared',
    anomaly_metrics: {
      overrun_mechanism: 'timely_release_or_compliant',
      overrun_days: 0,
      chain_status: 'compliant_control',
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(clearedBenchmark), {
    label: 'Release Compliance',
    value: 'Compliant (Cleared)',
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(clearedBenchmark), {
    value: 'Compliant (Cleared)',
    prominent: true,
  });

  const institutionalGap = {
    methodology_code: 'P10',
    monetary_exposure_inr: 0,
    anomaly_metrics: {
      overrun_mechanism: 'warrant_or_faster_transmission_delay',
      overrun_days: null,
      chain_status: 'institutional_record',
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(institutionalGap), {
    label: 'Custody Audit Pattern',
    value: 'FASTER / warrant delay · Institutional',
  });

  assert.deepEqual(
    utilsMod.resolveFindingCardMetric({ ...overrunWithComp, quarantined: true }, 'Withheld'),
    { value: 'Withheld', prominent: false },
  );
});

test('InnocentButLockedUpVisualiser helpers normalize portals, years, tiers, mechanisms, chain statuses, 4-stage chains, and 6-axis filters', () => {
  assert.equal(typeof visualsMod.InnocentButLockedUpVisualiser, 'function');
  assert.equal(
    visualsMod.P10_DEFAULT_HOOK,
    'The court declared them acquitted and ordered release. Why did the prison keep them locked up until contempt was filed?',
  );

  // Portals array + dict shorthand normalization
  const normPortals = visualsMod.p10NormalizePortals([
    { portal_category: 'court_portal', tasks: 620, done: 120, leads: 90, cleared: 30 },
    { portal_category: 'prison_portal', tasks: 110, done: 20, leads: 12, cleared: 8 },
    { portal_category: 'nhrc_shrc', tasks: 80, done: 15, leads: 14, cleared: 1 },
    { portal_category: 'parliament', tasks: 90, done: 10, leads: 8, cleared: 2 },
    { portal_category: 'police_portal', tasks: 40, done: 5, leads: 4, cleared: 1 },
    { portal_category: 'nalsa_slsa', tasks: 30, done: 4, leads: 3, cleared: 1 },
    { portal_category: 'govt_ministry', tasks: 20, done: 2, leads: 1, cleared: 1 },
    { portal_category: 'cag_audit', tasks: 10, done: 1, leads: 1, cleared: 0 },
  ]);
  assert.equal(normPortals.totalTasks, 1000);
  assert.equal(normPortals.items.length, 8);
  assert.equal(normPortals.items[0].portal_category, 'court_portal');
  assert.equal(normPortals.items[0].sharePct, 62);

  const dictPortals = visualsMod.p10NormalizePortals({ court_portal: 700, nhrc_shrc: 300 });
  assert.equal(dictPortals.totalTasks, 1000);
  assert.equal(dictPortals.items[0].portal_category, 'court_portal');

  // Years sorted chronologically
  const normYears = visualsMod.p10NormalizeYears([
    { year: 2025, tasks: 220, full_4_stage: 45, overrun_days: 920 },
    { year: 2021, tasks: 110, full_4_stage: 12, overrun_days: 340 },
    { year: 2026, tasks: 170, full_4_stage: 38, overrun_days: 610 },
  ]);
  assert.deepEqual(
    normYears.map((y) => y.year),
    [2021, 2025, 2026],
  );

  // Mechanisms normalization preserves all 6 canonical buckets + leads-only fallback
  const normMechs = visualsMod.p10NormalizeMechanisms(
    {
      post_acquittal_contempt_overrun: { tasks: 300, leads: 80 },
      bail_or_release_order_contempt: { tasks: 250, leads: 60 },
      warrant_or_faster_transmission_delay: { tasks: 150, leads: 30 },
      statutory_commission_detention_finding: { tasks: 120, leads: 25 },
      institutional_compliance_gap: { tasks: 80, leads: 15 },
      timely_release_or_compliant: { tasks: 100, leads: 0 },
    },
    null,
  );
  assert.equal(normMechs.items.length, 6);
  assert.equal(normMechs.totalTasks, 1000);
  assert.equal(normMechs.totalLeads, 210);
  assert.equal(normMechs.items[5].isCleared, true);

  const leadsOnlyMechs = visualsMod.p10NormalizeMechanisms(null, {
    post_acquittal_contempt_overrun: 3,
    statutory_commission_detention_finding: 1,
  });
  assert.equal(leadsOnlyMechs.totalTasks, 0);
  assert.equal(leadsOnlyMechs.totalLeads, 4);
  assert.equal(leadsOnlyMechs.items[0].sharePct, 75);

  // Chain status normalization
  const normChains = visualsMod.p10NormalizeChainStatuses({
    full_4_stage_chain: 420,
    partial_3_stage: 230,
    institutional_record: 250,
    compliant_control: 100,
  });
  assert.equal(normChains.total, 1000);
  assert.equal(normChains.items[0].key, 'full_4_stage_chain');

  // 4-stage timeline builder
  const stages = visualsMod.p10BuildFourStageChain({
    acquittal_order_date: '2025-05-01',
    acquittal_court: 'Allahabad High Court',
    acquittal_case_number: 'Crl. Appeal 410/2023',
    acquittal_order_type: 'acquittal',
    acquittal_quote: 'Acquitted of all charges.',
    prison_name: 'District Jail Ghaziabad',
    overrun_days: 28,
    contempt_case_number: 'Contempt (Civil) 1234/2025',
    contempt_court: 'Allahabad High Court',
    contempt_filing_date: '2025-05-20',
    release_date: '2025-05-29',
    release_verified: true,
    compensation_inr: 500000,
    judicial_direction_summary: 'Directed Rs 5 lakh compensation and FASTER compliance.',
  });
  assert.equal(stages.length, 4);
  assert.equal(stages.every((s) => s.verified), true);
  assert.equal(stages[1].badge, '+28 days overrun');
  assert.match(stages[3].badge, /5 L/);

  // 6-axis lead filtering
  const sampleLeads = [
    {
      finding_id: 'f-p10-1',
      title: 'Lead 1',
      case_title: 'Allahabad HC Contempt Case',
      portal_category: 'court_portal',
      court_tier: 'high_court',
      state_ut: 'Uttar Pradesh',
      year: 2025,
      overrun_mechanism: 'post_acquittal_contempt_overrun',
      signal_category: 'post_acquittal_contempt_overrun',
      chain_status: 'full_4_stage_chain',
      stages_verified_count: 4,
      status: 'verified',
    },
    {
      finding_id: 'f-p10-2',
      title: 'Lead 2',
      case_title: 'NHRC Unlawful Detention Notice',
      portal_category: 'nhrc_shrc',
      court_tier: 'statutory_body',
      state_ut: 'Bihar',
      year: 2023,
      overrun_mechanism: 'statutory_commission_detention_finding',
      signal_category: 'statutory_commission_detention_finding',
      chain_status: 'institutional_record',
      stages_verified_count: 2,
      status: 'peer_review',
    },
  ];
  assert.equal(visualsMod.p10FilterLeads(sampleLeads, 2025).length, 1);
  assert.equal(visualsMod.p10FilterLeads(sampleLeads, 'all', 'Bihar').length, 1);
  assert.equal(visualsMod.p10FilterLeads(sampleLeads, 'all', 'all', 'nhrc_shrc').length, 1);
  assert.equal(visualsMod.p10FilterLeads(sampleLeads, 'all', 'all', 'all', 'full_4_stage_chain').length, 1);
  assert.equal(
    visualsMod.p10FilterLeads(sampleLeads, 'all', 'all', 'all', 'all', 'post_acquittal_contempt_overrun').length,
    1,
  );
  assert.equal(
    visualsMod.p10FilterLeads(sampleLeads, 'all', 'all', 'all', 'all', 'all', 'statutory_body').length,
    1,
  );

  // State & court filters (including Union of India with state_ut: null from backend desk.py)
  const states = [
    { state_ut: null, jurisdiction: 'Union of India', tasks: 120, leads: 20 },
    { state_ut: 'Uttar Pradesh', jurisdiction: 'State:Uttar Pradesh', tasks: 180, leads: 40 },
    { state_ut: 'Delhi', jurisdiction: 'UT:Delhi', tasks: 65, leads: 12 },
  ];
  assert.equal(visualsMod.p10FilterStates(states, 'delhi').length, 1);
  assert.equal(visualsMod.p10FilterStates(states, 'union').length, 1);

  const courts = [
    { court_name: 'Supreme Court of India', court_tier: 'supreme_court', state_ut: null, tasks: 90, leads: 20 },
    { court_name: 'High Court of Judicature at Allahabad', court_tier: 'high_court', state_ut: 'Uttar Pradesh', tasks: 150, leads: 35 },
  ];
  assert.equal(visualsMod.p10FilterCourts(courts, '', 'supreme_court', 'all').length, 1);
  assert.equal(visualsMod.p10FilterCourts(courts, '', 'all', 'Union of India').length, 1);
  assert.equal(visualsMod.p10FilterCourts(courts, 'allahabad', 'all', 'Uttar Pradesh').length, 1);

  // 4-stage timeline builder — respects explicit stage flags and does NOT false-verify Stage 2 on compliant controls
  const compliantStages = visualsMod.p10BuildFourStageChain({
    overrun_mechanism: 'timely_release_or_compliant',
    chain_status: 'compliant_control',
    acquittal_order_date: '2025-05-01',
    acquittal_court: 'Delhi High Court',
    prison_name: 'Central Jail Tihar',
    custody_authority: 'Superintendent, Central Jail Tihar',
    non_release_reason: 'None — timely same-day release via FASTER.',
    overrun_days: 0,
    release_date: '2025-05-01',
    release_verified: true,
  });
  assert.equal(compliantStages[0].verified, true);
  assert.equal(compliantStages[1].verified, false);
  assert.equal(compliantStages[2].verified, false);
  assert.equal(compliantStages[3].verified, true);

  // Institutional record with required non-empty court/prison fields but explicit stage_1/2: false
  const instExtracted = utilsMod.extractInnocentButLockedUpMetrics({
    overrun_mechanism: 'institutional_compliance_gap',
    portal_category: 'parliament',
    acquittal_court: 'Parliament of India (Lok Sabha)',
    prison_name: 'All-India Prisons',
    stage_1_acquittal_verified: false,
    stage_2_continued_detention_verified: false,
    stage_3_contempt_verified: false,
    stage_4_release_verified: false,
    overrun_days: null,
  });
  assert.ok(instExtracted);
  assert.equal(instExtracted.stage1AcquittalVerified, false);
  assert.equal(instExtracted.stage2ContinuedDetentionVerified, false);
  assert.equal(instExtracted.stage3ContemptVerified, false);
  assert.equal(instExtracted.stage4ReleaseVerified, false);

  // Union of India filter must reject a lead with state_ut: null and jurisdiction: 'State:Bihar'
  const biharNullStateLead = {
    ...sampleLeads[1],
    finding_id: 'f-p10-bihar-null-state',
    state_ut: null,
    jurisdiction: 'State:Bihar',
  };
  const unionLead = {
    ...sampleLeads[0],
    finding_id: 'f-p10-union',
    state_ut: null,
    jurisdiction: 'Union of India',
    court_tier: 'supreme_court',
  };
  assert.equal(
    visualsMod.p10FilterLeads([...sampleLeads, biharNullStateLead, unionLead], 'all', 'Union of India').length,
    1,
  );
  assert.equal(
    visualsMod.p10FilterLeads([...sampleLeads, biharNullStateLead, unionLead], 'all', 'Union of India')[0].finding_id,
    'f-p10-union',
  );

  // Regression guards for stage verification, Dossiers timeline joining (no leading dot / +0d), Union location, and pluralization
  const cardSrcCheck = fs.readFileSync(path.join(webDir, 'src/components/FindingCard.tsx'), 'utf8');
  assert.doesNotMatch(cardSrcCheck, /stage2ContinuedDetentionVerified\s*\|\|\s*p10Record\.prisonName/);
  const drawerSrcCheck = fs.readFileSync(path.join(webDir, 'src/components/FindingDrawer.tsx'), 'utf8');
  assert.doesNotMatch(drawerSrcCheck, /stage2ContinuedDetentionVerified\s*\|\|\s*p10Record\.prisonName/);
  const visualsSrcCheck = fs.readFileSync(path.join(webDir, 'src/components/ProjectVisuals.tsx'), 'utf8');
  assert.match(visualsSrcCheck, /uniqueDocs === 1 \? 'doc' : 'docs'/);
  assert.match(visualsSrcCheck, /prisonsNamed === 1 \? 'prison' : 'prisons'/);
  assert.match(visualsSrcCheck, /clearedEmbedded === 1 \? 'control' : 'controls'/);
  assert.match(visualsSrcCheck, /activePortalsCount === 1 \? 'category' : 'categories'/);
  assert.match(visualsSrcCheck, /lead\.overrun_days != null && lead\.overrun_days > 0/);
});

test('API client normalizes P10 mission fallback code and 3 structured citation roles + secondary provenance', () => {
  const mission = clientMod.normalizeMission({
    slug: 'innocent-but-locked-up',
    title: 'Innocent But Locked Up: Post-Acquittal Custody Overruns & Contempt Compliance (2021–2026)',
    methodology_code: 'P10',
    pillar: 'justice',
    total_tasks: 1000,
    completed_tasks: 0,
    verified_findings_count: 0,
  });
  assert.equal(mission.project_code, 'P10');
  assert.equal(mission.pillar, 'justice');

  const finding = clientMod.normalizeFinding({
    id: 'f-p10-anchor-1',
    methodology_code: 'P10',
    pillar: 'justice',
    title: 'Post-Acquittal Custody Overrun Audited via High Court Contempt Record',
    summary: 'Primary court order documents acquittal, continued jail detention, contempt notice, and subsequent release.',
    monetary_exposure_inr: 500000,
    secondary_url: 'https://www.allahabadhighcourt.in/contempt_order.pdf',
    secondary_sha256: 'd'.repeat(64),
    citations: [
      {
        role: 'acquittal_and_custody_order',
        title: 'Criminal Appeal Acquittal Judgment',
        source_url: 'https://www.allahabadhighcourt.in/acquittal_order.pdf',
        sha256: 'c'.repeat(64),
        excerpt: 'The appellant is acquitted of all charges and directed to be released forthwith.',
      },
      {
        role: 'contempt_and_release_outcome',
        title: 'Contempt Petition Compliance Order',
        source_url: 'https://www.allahabadhighcourt.in/contempt_order.pdf',
        sha256: 'd'.repeat(64),
        excerpt: 'Detainee released following contempt proceedings; compensation directed.',
      },
      {
        role: 'statutory_benchmark',
        title: 'Constitution of India Article 21 & Contempt of Courts Act 1971',
        source_url: 'https://www.indiacode.nic.in/',
        excerpt: 'Personal liberty and compliance with judicial release orders.',
      },
    ],
  });
  assert.equal(finding.citations.length, 3);
  assert.deepEqual(
    finding.citations.map((c) => c.role),
    ['acquittal_and_custody_order', 'contempt_and_release_outcome', 'statutory_benchmark'],
  );
  assert.equal(finding.provenance.secondary_url, 'https://www.allahabadhighcourt.in/contempt_order.pdf');
  assert.equal(finding.provenance.secondary_sha256, 'd'.repeat(64));
});

test('P10 frontend copy strictly complies with BNS Sec 356 neutrality rules (zero accusatory labels including guilty)', () => {
  const forbidden = /\b(scam|corrupt\w*|brib\w*|fraud\w*|kickback\w*|guilty|stolen|rigged|crook\w*|loot\w*|embezzl\w*)\b/i;
  const stringsToCheck = [
    visualsMod.P10_DEFAULT_HOOK,
    utilsMod.METHODOLOGY_DESCRIPTIONS.P10.name,
    utilsMod.METHODOLOGY_DESCRIPTIONS.P10.rule,
    tagsMod.WORK_TYPE_TAGS.custody_overrun.label,
    tagsMod.WORK_TYPE_TAGS.custody_overrun.description,
    visualsMod.p10MechanismLabel('post_acquittal_contempt_overrun'),
    visualsMod.p10MechanismLabel('bail_or_release_order_contempt'),
    visualsMod.p10MechanismLabel('warrant_or_faster_transmission_delay'),
    visualsMod.p10MechanismLabel('statutory_commission_detention_finding'),
    visualsMod.p10MechanismLabel('institutional_compliance_gap'),
    visualsMod.p10MechanismLabel('timely_release_or_compliant'),
    visualsMod.p10PortalLabel('court_portal'),
    visualsMod.p10PortalLabel('prison_portal'),
    visualsMod.p10PortalLabel('police_portal'),
    visualsMod.p10PortalLabel('nhrc_shrc'),
    visualsMod.p10PortalLabel('nalsa_slsa'),
    visualsMod.p10PortalLabel('parliament'),
    visualsMod.p10PortalLabel('govt_ministry'),
    visualsMod.p10PortalLabel('cag_audit'),
    visualsMod.p10CourtTierLabel('supreme_court'),
    visualsMod.p10CourtTierLabel('high_court'),
    visualsMod.p10CourtTierLabel('district_court'),
    visualsMod.p10CourtTierLabel('statutory_body'),
    visualsMod.p10ChainStatusLabel('full_4_stage_chain'),
    visualsMod.p10ChainStatusLabel('partial_3_stage'),
    visualsMod.p10ChainStatusLabel('institutional_record'),
    visualsMod.p10ChainStatusLabel('compliant_control'),
  ];
  for (const s of stringsToCheck) {
    assert.equal(
      forbidden.test(s),
      false,
      `Forbidden accusatory term found in P10 UI string: ${JSON.stringify(s)}`,
    );
  }

  // Scan P10 slices of ProjectVisuals.tsx, FindingCard.tsx, FindingDrawer.tsx, and BriefPage.tsx
  const visualsSrc = fs.readFileSync(path.join(webDir, 'src/components/ProjectVisuals.tsx'), 'utf8');
  const p10VisualStart = visualsSrc.indexOf('/* ---------- 3I. P10 Innocent But Locked Up');
  assert.ok(p10VisualStart > 0);
  assert.equal(
    forbidden.test(visualsSrc.slice(p10VisualStart)),
    false,
    'Forbidden accusatory term found in P10 visualiser code',
  );

  const cardSrc = fs.readFileSync(path.join(webDir, 'src/components/FindingCard.tsx'), 'utf8');
  assert.equal(forbidden.test(cardSrc), false, 'Forbidden accusatory term found in FindingCard.tsx');

  const drawerSrc = fs.readFileSync(path.join(webDir, 'src/components/FindingDrawer.tsx'), 'utf8');
  const p10DrawerStart = drawerSrc.indexOf('{/* P10 Structured 4-Stage');
  assert.ok(p10DrawerStart > 0);
  const p10DrawerEnd = drawerSrc.indexOf('{/* Deterministic Metrics Table', p10DrawerStart);
  assert.ok(p10DrawerEnd > p10DrawerStart);
  assert.equal(
    forbidden.test(drawerSrc.slice(p10DrawerStart, p10DrawerEnd)),
    false,
    'Forbidden accusatory term found in P10 FindingDrawer dossier code',
  );

  const briefSrc = fs.readFileSync(path.join(webDir, 'src/pages/BriefPage.tsx'), 'utf8');
  assert.equal(forbidden.test(briefSrc), false, 'Forbidden accusatory term found in BriefPage.tsx');
});

test('InnocentButLockedUpVisualiser renders without runtime errors for full, partial, and empty visual_summary payloads', () => {
  const baseMission = {
    slug: 'innocent-but-locked-up',
    project_code: 'P10',
    title: 'Innocent But Locked Up',
    pillar: 'justice',
    methodology_code: 'P10',
    jurisdiction: 'All-India',
    statutory_anchors: ['Constitution of India Art. 21', 'Contempt of Courts Act 1971', 'BNS Sec 356'],
    data_mode: 'live_public',
    status: 'active',
    total_tasks: 1000,
    completed_tasks: 40,
    verified_findings_count: 14,
  };

  const fullSummary = {
    methodology_code: 'P10',
    status_counts: {
      completed: 40,
      claimed: 5,
      queued: 955,
      waiting: 0,
      planned: 0,
      total: 1000,
      pct_complete: 4.0,
      audits_done: 40,
      audits_total: 1000,
      checks_done: 0,
      checks_total: 0,
    },
    innocent_but_locked_up: {
      hook: visualsMod.P10_DEFAULT_HOOK,
      generated_on: '2026-11-28',
      as_of_date: '2026-11-28',
      year_range: [2021, 2026],
      total_tasks: 1000,
      unique_documents_covered: 480,
      states_and_uts_covered: 29,
      courts_covered: 26,
      prisons_named: 84,
      full_4_stage_tasks: 420,
      contempt_tasks: 510,
      verified_release_tasks: 490,
      cleared_control_tasks_embedded: 100,
      total_overrun_days_documented: 6420,
      median_overrun_days: 14,
      max_overrun_days: 380,
      total_compensation_ordered_inr: 12500000,
      leads: 32,
      verified: 14,
      peer_review: 18,
      cleared_tasks: 8,
      flagged_compensation_inr_cr: 1.25,
      by_portal_category: [
        { portal_category: 'court_portal', label: 'Court Portal', tasks: 620, done: 25, leads: 20, cleared: 5 },
        { portal_category: 'prison_portal', label: 'Prison Portal', tasks: 110, done: 5, leads: 4, cleared: 1 },
        { portal_category: 'nhrc_shrc', label: 'NHRC / SHRC', tasks: 100, done: 4, leads: 4, cleared: 0 },
        { portal_category: 'parliament', label: 'Parliament', tasks: 90, done: 4, leads: 2, cleared: 2 },
        { portal_category: 'nalsa_slsa', label: 'NALSA / SLSA', tasks: 80, done: 2, leads: 2, cleared: 0 },
      ],
      by_year: [
        { year: 2021, tasks: 110, done: 5, full_4_stage: 40, overrun_days: 610 },
        { year: 2022, tasks: 150, done: 6, full_4_stage: 62, overrun_days: 920 },
        { year: 2023, tasks: 190, done: 8, full_4_stage: 85, overrun_days: 1340 },
        { year: 2024, tasks: 210, done: 9, full_4_stage: 95, overrun_days: 1580 },
        { year: 2025, tasks: 220, done: 8, full_4_stage: 92, overrun_days: 1350 },
        { year: 2026, tasks: 120, done: 4, full_4_stage: 46, overrun_days: 620 },
      ],
      by_court_tier: {
        supreme_court: { tasks: 90, done: 8, leads: 6, cleared: 2 },
        high_court: { tasks: 650, done: 24, leads: 20, cleared: 4 },
        district_court: { tasks: 60, done: 2, leads: 2, cleared: 0 },
        statutory_body: { tasks: 200, done: 6, leads: 4, cleared: 2 },
      },
      by_mechanism: {
        post_acquittal_contempt_overrun: { tasks: 310, leads: 14 },
        bail_or_release_order_contempt: { tasks: 240, leads: 8 },
        warrant_or_faster_transmission_delay: { tasks: 150, leads: 5 },
        statutory_commission_detention_finding: { tasks: 120, leads: 3 },
        institutional_compliance_gap: { tasks: 80, leads: 2 },
        timely_release_or_compliant: { tasks: 100, leads: 0 },
      },
      by_signal: {
        post_acquittal_contempt_overrun: 14,
        bail_or_release_order_contempt: 8,
        warrant_or_faster_transmission_delay: 5,
        statutory_commission_detention_finding: 3,
        institutional_compliance_gap: 2,
      },
      by_chain_status: {
        full_4_stage_chain: 420,
        partial_3_stage: 230,
        institutional_record: 250,
        compliant_control: 100,
      },
      states: [
        {
          state_ut: 'Uttar Pradesh',
          jurisdiction: 'State:Uttar Pradesh',
          tasks: 185,
          done: 12,
          leads: 9,
          cleared: 3,
          full_4_stage: 88,
          total_overrun_days: 1420,
          compensation_inr: 4500000,
        },
        {
          state_ut: 'Delhi',
          jurisdiction: 'UT:Delhi',
          tasks: 75,
          done: 6,
          leads: 5,
          cleared: 1,
          full_4_stage: 34,
          total_overrun_days: 410,
          compensation_inr: 1500000,
        },
      ],
      top_courts: [
        {
          court_name: 'Supreme Court of India',
          court_tier: 'supreme_court',
          state_ut: null,
          tasks: 90,
          leads: 6,
          full_4_stage: 38,
        },
        {
          court_name: 'High Court of Judicature at Allahabad',
          court_tier: 'high_court',
          state_ut: 'Uttar Pradesh',
          tasks: 160,
          leads: 9,
          full_4_stage: 82,
        },
      ],
      top_prisons: [
        {
          prison_name: 'District Jail, Ghaziabad',
          state_ut: 'Uttar Pradesh',
          tasks: 14,
          leads: 4,
          total_overrun_days: 162,
        },
      ],
      anchor_case: {
        task_id: 'p10-court-2025-anchor-0001',
        finding_id: 'f-p10-anchor-1',
        case_title: 'Post-Acquittal Release Overrun & Contempt Direction — Allahabad HC',
        portal_category: 'court_portal',
        court_tier: 'high_court',
        state_ut: 'Uttar Pradesh',
        jurisdiction: 'State:Uttar Pradesh',
        year: 2025,
        overrun_mechanism: 'post_acquittal_contempt_overrun',
        stages_verified_count: 4,
        chain_status: 'full_4_stage_chain',
        acquittal_order_date: '2025-05-01',
        acquittal_court: 'High Court of Judicature at Allahabad',
        acquittal_case_number: 'Criminal Appeal No. 410/2023',
        acquittal_quote: 'The appellant is acquitted of all charges and directed to be released forthwith.',
        prison_name: 'District Jail, Ghaziabad',
        custody_authority: 'Superintendent, District Jail',
        non_release_reason: 'Warrant transmission delay notwithstanding FASTER upload.',
        overrun_days: 28,
        overrun_window: '2025-05-01 to 2025-05-29',
        contempt_case_number: 'Contempt Application (Civil) No. 1234/2025',
        contempt_court: 'High Court of Judicature at Allahabad',
        contempt_filing_date: '2025-05-20',
        contempt_order_date: '2025-06-18',
        contempt_quote: 'Contempt notice issued against the Superintendent; explanation sought.',
        release_date: '2025-05-29',
        release_verified: true,
        compensation_inr: 500000,
        judicial_direction_summary: 'Ordered Rs 5 lakh compensation and strict FASTER release audit.',
        source_url: 'https://www.allahabadhighcourt.in/acquittal_order.pdf',
        secondary_url: 'https://www.allahabadhighcourt.in/contempt_order.pdf',
        portal_url: 'https://www.allahabadhighcourt.in/',
        source_sha256: 'c'.repeat(64),
      },
      top_leads: [
        {
          finding_id: 'f-p10-anchor-1',
          title: 'Post-Acquittal Custody Overrun Audited via High Court Contempt Record',
          case_key: 'p10-court-2025-anchor-0001',
          case_title: 'Post-Acquittal Release Overrun & Contempt Direction — Allahabad HC',
          entity_name: 'Prison Administration, Uttar Pradesh',
          portal_category: 'court_portal',
          court_tier: 'high_court',
          state_ut: 'Uttar Pradesh',
          jurisdiction: 'State:Uttar Pradesh',
          year: 2025,
          overrun_mechanism: 'post_acquittal_contempt_overrun',
          signal_category: 'post_acquittal_contempt_overrun',
          stages_verified_count: 4,
          chain_status: 'full_4_stage_chain',
          acquittal_order_date: '2025-05-01',
          acquittal_court: 'High Court of Judicature at Allahabad',
          prison_name: 'District Jail, Ghaziabad',
          overrun_days: 28,
          contempt_case_number: 'Contempt Application (Civil) No. 1234/2025',
          release_date: '2025-05-29',
          release_verified: true,
          compensation_inr: 500000,
          judicial_direction_summary: 'Ordered Rs 5 lakh compensation and FASTER compliance audit.',
          is_anchor_case: true,
          severity: 'high',
          status: 'verified',
        },
      ],
      documents_url: '/api/missions/innocent-but-locked-up/documents.csv',
      coverage_url: '/api/missions/innocent-but-locked-up/coverage.json',
    },
  };

  assert.doesNotThrow(() => {
    visualsMod.InnocentButLockedUpVisualiser({
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
    visualsMod.InnocentButLockedUpVisualiser({
      mission: baseMission,
      visualSummary: undefined,
    });
    visualsMod.InnocentButLockedUpVisualiser({
      mission: baseMission,
      visualSummary: {
        methodology_code: 'P10',
        status_counts: fullSummary.status_counts,
        innocent_but_locked_up: {
          total_tasks: 1000,
          leads: 0,
          verified: 0,
          peer_review: 0,
          cleared_tasks: 0,
          anchor_case: null,
          top_leads: [],
        },
      },
    });
  });
});
