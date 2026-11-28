import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as esbuild from 'esbuild';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(__dirname, '..');

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'p9-web-test-'));
const utilsOut = path.join(tmpDir, 'utils.mjs');
const visualsOut = path.join(tmpDir, 'ProjectVisuals.mjs');
const clientOut = path.join(tmpDir, 'client.mjs');
const tagsOut = path.join(tmpDir, 'tags.mjs');
const webmcpOut = path.join(tmpDir, 'webmcp.mjs');
const cardOut = path.join(tmpDir, 'FindingCard.mjs');
const drawerOut = path.join(tmpDir, 'FindingDrawer.mjs');

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
        export const jsx = (type, props) => ({ type, props });
        export const jsxs = (type, props) => ({ type, props });
        export const Fragment = 'Fragment';
        export const createContext = (def) => ({ Provider: noop, _default: def });
        export const useContext = () => noop;
        export const useEffect = noop;
        export const useCallback = (fn) => fn;
        export const useMemo = (fn) => fn();
        export const useRef = (v) => ({ current: v });
        export const useState = (v) => [v, noop];
        export const AlertTriangle = noop;
        export const BarChart3 = noop;
        export const Check = noop;
        export const CheckCircle2 = noop;
        export const CircleDashed = noop;
        export const Copy = noop;
        export const Download = noop;
        export const ExternalLink = noop;
        export const FileText = noop;
        export const Globe = noop;
        export const History = noop;
        export const Layers = noop;
        export const Lock = noop;
        export const MinusCircle = noop;
        export const RotateCcw = noop;
        export const Scale = noop;
        export const Search = noop;
        export const ShieldAlert = noop;
        export const ShieldCheck = noop;
        export const X = noop;
        export const XCircle = noop;
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
  esbuild.build({
    entryPoints: [path.join(webDir, 'src/components/FindingCard.tsx')],
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    jsx: 'automatic',
    outfile: cardOut,
    plugins: [uiStubPlugin],
  }),
  esbuild.build({
    entryPoints: [path.join(webDir, 'src/components/FindingDrawer.tsx')],
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    jsx: 'automatic',
    outfile: drawerOut,
    plugins: [uiStubPlugin],
  }),
]);

const utilsMod = await import(pathToFileURL(utilsOut).href);
const visualsMod = await import(pathToFileURL(visualsOut).href);
const clientMod = await import(pathToFileURL(clientOut).href);
const tagsMod = await import(pathToFileURL(tagsOut).href);
const webmcpMod = await import(pathToFileURL(webmcpOut).href);
const cardMod = await import(pathToFileURL(cardOut).href);
const drawerMod = await import(pathToFileURL(drawerOut).href);

test.after(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test('P9 project metadata helpers resolve slug, code, work type, pillar, WebMCP, and official Indian exam agency URLs', () => {
  assert.equal(utilsMod.projectCodeFor({ slug: 'auditing-government-exams' }), 'P9');
  assert.equal(utilsMod.projectCodeFor({ methodology_code: 'P9' }), 'P9');
  assert.equal(utilsMod.pillarLabel('audit'), 'CAG & PAC Audits');
  assert.equal(utilsMod.workTypeLabel('anomaly_scan', 'P9'), 'Exam conduct');
  assert.equal(tagsMod.getWorkTypeTag(undefined, 'P9').id, 'exam_conduct');
  assert.ok(utilsMod.METHODOLOGY_DESCRIPTIONS.P9?.name.includes('Auditing Government Exams'));

  const projectLegend = tagsMod.SITE_LEGEND_GROUPS.find((g) => g.id === 'project');
  assert.ok(projectLegend?.tags.some((t) => t.id === 'exam_conduct'));

  // Official Indian exam conducting bodies & parliamentary portals
  const officialUrls = [
    'https://nta.ac.in/Download/Notice/Notice_20240619225242.pdf',
    'https://exams.nta.ac.in/UGC-NET/images/public-notice.pdf',
    'https://upsc.gov.in/sites/default/files/ExamCal-2024.pdf',
    'https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/notice.pdf',
    'https://ssc.nic.in/Portal/Notices',
    'https://natboard.edu.in/viewNotice.php?Nid=123',
    'https://nbe.edu.in/',
    'https://www.aiimsexams.ac.in/',
    'https://www.ibps.in/',
    'https://sbi.co.in/web/careers',
    'https://afcat.cdac.in/AFCAT/',
    'https://www.ugc.ac.in/',
    'https://csirhrdg.res.in/',
    'https://sansad.in/getFile/loksabhaquestions/annex/185/AU999.pdf',
    'https://bucketapi.rajyasabha.digital/RSQ/267/AU456.pdf',
    'https://static.pib.gov.in/WriteReadData/specificdocs/documents/2024/jun/doc.pdf',
    'https://uppsc.up.nic.in/',
    'https://bpsc.bih.nic.in/',
  ];
  for (const url of officialUrls) {
    assert.equal(utilsMod.isOfficialGovUrl(url), true, `Expected official host: ${url}`);
  }

  // Non-official / insecure / spoofed hosts must return false
  const nonOfficialUrls = [
    'http://nta.ac.in/insecure.pdf',
    'https://fake-nta.ac.in.example.com/notice.pdf',
    'https://fake-natboard.edu.in.attacker.org/doc.pdf',
    'https://prsindia.org/billtrack/public-examinations-bill-2024',
    'https://news.example.com/exam-update',
  ];
  for (const url of nonOfficialUrls) {
    assert.equal(utilsMod.isOfficialGovUrl(url), false, `Expected non-official host: ${url}`);
  }

  // WebMCP tool manifest documents auditing-government-exams and P9
  const getProjectTool = webmcpMod.WEBMCP_TOOL_MANIFEST.find((t) => t.name === 'get_project');
  const getJoinTool = webmcpMod.WEBMCP_TOOL_MANIFEST.find((t) => t.name === 'get_task_join_prompt');
  assert.match(getProjectTool.inputSchema.properties.slug.description, /auditing-government-exams/);
  assert.match(getJoinTool.inputSchema.properties.methodology_code.description, /P9/);
});

test('BriefPage lists P9 Auditing Government Exams, FindingsPage supports audit pillar, and MissionsPage wires P9', () => {
  const findingsSrc = fs.readFileSync(path.join(webDir, 'src/pages/FindingsPage.tsx'), 'utf8');
  assert.match(findingsSrc, /TOPICS[^=]*=\s*\[[\s\S]*code:\s*'P9'/);

  const briefSrc = fs.readFileSync(path.join(webDir, 'src/pages/BriefPage.tsx'), 'utf8');
  assert.match(briefSrc, /slug:\s*'auditing-government-exams'/);
  assert.match(briefSrc, /code:\s*'P9'/);
  assert.match(briefSrc, /LIVE_PROJECTS\.slice\(\)\.reverse\(\)\.map/);

  // P9 sorts newest-first ahead of P8..P1 and advances nextProjectCode to P10
  const sorted = utilsMod.sortProjectsDescending([
    { slug: 'police-finances', project_code: 'P1' },
    { slug: 'sovereign-lies', project_code: 'P8' },
    { slug: 'auditing-government-exams' },
  ]);
  assert.deepEqual(
    sorted.map((m) => m.slug),
    ['auditing-government-exams', 'sovereign-lies', 'police-finances'],
  );
  assert.equal(
    utilsMod.nextProjectCode([
      { slug: 'police-finances', project_code: 'P1' },
      { slug: 'sovereign-lies', project_code: 'P8' },
      { slug: 'auditing-government-exams' },
    ]),
    'P10',
  );

  const missionsSrc = fs.readFileSync(path.join(webDir, 'src/pages/MissionsPage.tsx'), 'utf8');
  assert.match(missionsSrc, /AUDITING_EXAMS_METHOD\s*=\s*'P9'/);
  assert.match(missionsSrc, /auditing_government_exams/);
});

test('P9 canonical enum formatters cover all 7 disruption types, 8 authority types, 8 exam categories, 7 reasons, delay spans, and candidate counts', () => {
  // All 7 canonical disruption types + rule-code aliases
  assert.equal(utilsMod.formatExamDisruption('cancelled_after_conduct').key, 'cancelled_after_conduct');
  assert.equal(utilsMod.formatExamDisruption('P9-CANCELLED-AFTER-CONDUCT').key, 'cancelled_after_conduct');
  assert.equal(utilsMod.formatExamDisruption('cancelled_after_conduct').label, 'Took Exam, Then Cancelled');
  assert.equal(utilsMod.formatExamDisruption('postponed').key, 'postponed');
  assert.equal(utilsMod.formatExamDisruption('P9-POSTPONED').key, 'postponed');
  assert.equal(utilsMod.formatExamDisruption('rescheduled').key, 'rescheduled');
  assert.equal(utilsMod.formatExamDisruption('P9-RESCHEDULED').key, 'rescheduled');
  assert.equal(utilsMod.formatExamDisruption('delayed').key, 'delayed');
  assert.equal(utilsMod.formatExamDisruption('P9-DELAYED').key, 'delayed');
  assert.equal(utilsMod.formatExamDisruption('cancelled_before_conduct').key, 'cancelled_before_conduct');
  assert.equal(utilsMod.formatExamDisruption('P9-CANCELLED-BEFORE-CONDUCT').key, 'cancelled_before_conduct');
  assert.equal(utilsMod.formatExamDisruption('partial_reexam_or_glitch').key, 'partial_reexam_or_glitch');
  assert.equal(utilsMod.formatExamDisruption('P9-PARTIAL-REEXAM-OR-GLITCH').key, 'partial_reexam_or_glitch');

  const flawless = utilsMod.formatExamDisruption('flawless_conduct');
  assert.equal(flawless.key, 'flawless_conduct');
  assert.equal(flawless.isCleared, true);
  assert.equal(flawless.label, 'Conducted Flawlessly (Cleared)');

  // All 8 canonical authority types
  const authorityTypes = [
    'central_testing_agency',
    'union_psc_or_recruitment',
    'railway_banking_psu',
    'defence_medical_technical',
    'state_psc',
    'state_police_or_subordinate',
    'state_teacher_or_entrance',
    'ut_recruitment_or_board',
  ];
  for (const at of authorityTypes) {
    assert.equal(utilsMod.formatExamAuthorityType(at).key, at);
    assert.ok(utilsMod.formatExamAuthorityType(at).label.length > 4);
  }

  // All 8 canonical exam categories
  const examCategories = [
    'civil_services_and_psc',
    'police_and_defence',
    'teaching_and_faculty',
    'medical_and_health',
    'engineering_and_higher_ed',
    'banking_railways_ssc',
    'clerical_and_subordinate',
    'board_and_other_public',
  ];
  for (const cat of examCategories) {
    assert.equal(utilsMod.formatExamCategory(cat).key, cat);
    assert.ok(utilsMod.formatExamCategory(cat).label.length > 4);
  }

  // All 7 canonical disruption reason categories
  const reasons = [
    'paper_integrity_compromise',
    'administrative_or_logistical',
    'technical_or_cbt_glitch',
    'election_or_calendar_clash',
    'weather_or_disaster',
    'judicial_or_policy_review',
    'none_flawless',
  ];
  for (const r of reasons) {
    assert.equal(utilsMod.formatExamDisruptionReason(r).key, r);
  }

  // Delay spans & compact candidate counts
  assert.equal(utilsMod.formatExamDelaySpan(0), '0 days');
  assert.equal(utilsMod.formatExamDelaySpan(1), '1 day');
  assert.equal(utilsMod.formatExamDelaySpan(49), '49 days');
  assert.equal(utilsMod.formatExamDelaySpan(400), '400 days (~1.1 yrs)');
  assert.equal(utilsMod.formatExamDelaySpan(null), null);
  assert.equal(utilsMod.formatExamDelaySpan(-3), null);

  assert.equal(utilsMod.formatCandidateCountCompact(908580), '9.09 L');
  assert.equal(utilsMod.formatCandidateCountCompact(14200000), '1.42 Cr');
  assert.equal(utilsMod.formatCandidateCountCompact(45000), '45,000');
  assert.equal(utilsMod.formatCandidateCountCompact(null), null);
  assert.equal(utilsMod.formatCandidateCountCompact(0), null);

  // Citation role labels for P9
  assert.equal(utilsMod.formatCitationRoleLabel('original_schedule'), 'Original Exam Schedule');
  assert.equal(utilsMod.formatCitationRoleLabel('conduct_outcome'), 'Conduct / Disruption Notice');
  assert.equal(utilsMod.formatCitationRoleLabel('statutory_benchmark'), 'Statutory Benchmark');
});

test('extractAuditingGovernmentExamsMetrics parses full P9 UGC-NET June 2024 payload and returns null for unrelated metrics', () => {
  const extracted = utilsMod.extractAuditingGovernmentExamsMetrics({
    case_key: 'p9-union-ugc-net-june-2024-0001',
    verdict: 'flagged_disruption',
    signal_category: 'cancelled_after_conduct',
    triggered_rules: ['P9-CANCELLED-AFTER-CONDUCT'],
    exam_name: 'UGC-NET June 2024',
    exam_slug: 'ugc-net-june-2024',
    cycle_or_stage: 'June 2024 Cycle (Pen-and-Paper OMR)',
    exam_year: 2024,
    jurisdiction: 'Union of India',
    jurisdiction_level: 'union',
    state_or_ut: null,
    authority: 'National Testing Agency (NTA)',
    authority_key: 'nta',
    authority_type: 'central_testing_agency',
    exam_category: 'teaching_and_faculty',
    scheduled_date: '2024-06-18',
    actual_or_event_date: '2024-06-19',
    delay_days: 64,
    disruption_type: 'cancelled_after_conduct',
    disruption_reason_category: 'paper_integrity_compromise',
    students_took_exam_before_cancellation: true,
    schedule_summary: 'NTA conducted UGC-NET June 2024 across 317 cities on 18 June 2024 in OMR mode.',
    schedule_document_ref: 'NTA Public Notice dated 18.06.2024',
    schedule_excerpt: 'National Testing Agency conducted the UGC-NET June 2024 Examination on 18 June 2024.',
    schedule_locator: 'Page 1, Para 1',
    schedule_tokens_matched: ['UGC-NET June 2024', '18 June 2024'],
    outcome_summary: 'Ministry of Education cancelled the examination on 19 June 2024 following I4C integrity inputs and ordered a fresh exam.',
    outcome_document_ref: 'MoE / PIB Press Release dated 19.06.2024',
    outcome_excerpt: 'Government has decided to cancel the UGC-NET June 2024 Examination and conduct a fresh examination.',
    outcome_locator: 'Release ID 2026752',
    outcome_tokens_matched: ['cancel', 'UGC-NET June 2024', 'fresh examination'],
    affected_candidates_count: 908580,
    affected_candidates_basis: 'PIB Press Release (9.08 lakh candidates appeared on 18 June 2024)',
    statutory_anchor: 'Public Examinations (Prevention of Unfair Means) Act, 2024',
    secondary_role: 'outcome_document',
    is_anchor_case: true,
    as_of_date: '2026-11-28',
    source_locator: 'Page 1',
  });

  assert.ok(extracted);
  assert.equal(extracted.examName, 'UGC-NET June 2024');
  assert.equal(extracted.examYear, 2024);
  assert.equal(extracted.disruptionType, 'cancelled_after_conduct');
  assert.equal(extracted.studentsTookExamBeforeCancellation, true);
  assert.equal(extracted.delayDays, 64);
  assert.equal(extracted.affectedCandidatesCount, 908580);
  assert.equal(extracted.isAnchorCase, true);
  assert.deepEqual(extracted.scheduleTokensMatched, ['UGC-NET June 2024', '18 June 2024']);
  assert.deepEqual(extracted.outcomeTokensMatched, ['cancel', 'UGC-NET June 2024', 'fresh examination']);
  assert.deepEqual(extracted.triggeredRules, ['P9-CANCELLED-AFTER-CONDUCT']);

  assert.equal(utilsMod.extractAuditingGovernmentExamsMetrics({ unrelated: true }), null);
});

test('P9 FindingCard and FindingDrawer headline metrics avoid misleading ₹0 and handle disrupted, cleared, and quarantined records cleanly', () => {
  const ugcNetFinding = {
    id: 'f-p9-ugcnet-1',
    methodology_code: 'P9',
    pillar: 'audit',
    title: 'UGC-NET June 2024 cancelled one day after conduct across 317 cities',
    summary: 'Students sat for UGC-NET June 2024 on 18 June 2024; Ministry of Education cancelled the exam on 19 June 2024.',
    entity_name: 'National Testing Agency (NTA)',
    jurisdiction: 'Union of India',
    fiscal_year: '2024',
    monetary_exposure_inr: 0,
    amount_label: 'public_examination_conduct',
    severity: 'high',
    status: 'verified',
    rule_triggered: 'P9-CANCELLED-AFTER-CONDUCT',
    citations: [],
    benign_explanations: ['Preventive integrity safeguard under Public Examinations (Prevention of Unfair Means) Act, 2024.'],
    anomaly_metrics: {
      exam_name: 'UGC-NET June 2024',
      cycle_or_stage: 'June 2024 Cycle',
      exam_year: 2024,
      jurisdiction: 'Union of India',
      jurisdiction_level: 'union',
      authority: 'National Testing Agency (NTA)',
      authority_type: 'central_testing_agency',
      exam_category: 'teaching_and_faculty',
      scheduled_date: '2024-06-18',
      actual_or_event_date: '2024-06-19',
      delay_days: 64,
      disruption_type: 'cancelled_after_conduct',
      disruption_reason_category: 'paper_integrity_compromise',
      students_took_exam_before_cancellation: true,
      schedule_summary: 'Conducted on 18 June 2024 in OMR mode.',
      outcome_summary: 'Cancelled on 19 June 2024; re-conducted in August–September 2024.',
      affected_candidates_count: 908580,
      affected_candidates_basis: 'PIB Press Release',
      is_anchor_case: true,
    },
  };

  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(ugcNetFinding), {
    label: 'Conduct Outcome',
    value: 'Cancelled after exam · +64d',
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(ugcNetFinding), {
    value: 'Cancelled after exam · +64d',
    prominent: true,
  });

  // Cleared flawless conduct
  const clearedExam = {
    methodology_code: 'P9',
    monetary_exposure_inr: null,
    status: 'cleared',
    anomaly_metrics: {
      exam_name: 'UPSC Civil Services Preliminary Exam 2025',
      disruption_type: 'flawless_conduct',
      delay_days: 0,
      students_took_exam_before_cancellation: false,
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(clearedExam), {
    label: 'Exam Conduct',
    value: 'Flawless (Cleared)',
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(clearedExam), {
    value: 'Flawless (Cleared)',
    prominent: true,
  });

  // Quarantined stays withheld
  assert.deepEqual(
    utilsMod.resolveFindingCardMetric({ ...ugcNetFinding, quarantined: true }, 'Withheld'),
    { value: 'Withheld', prominent: false },
  );

  // FindingCard and FindingDrawer render without runtime error for P9
  assert.doesNotThrow(() => {
    cardMod.FindingCard({ finding: ugcNetFinding, onSelect: () => {} });
    cardMod.FindingCard({ finding: ugcNetFinding, onSelect: () => {}, compact: true });
    drawerMod.FindingDrawer({ findingId: null, onClose: () => {} });
  });
});

test('AuditingGovernmentExamsVisualiser helpers normalize headline, 7 disruptions, tiers, years, sorting, pluralization, corpus fallback status, and multi-axis filters', () => {
  assert.equal(typeof visualsMod.AuditingGovernmentExamsVisualiser, 'function');
  assert.ok(visualsMod.P9_DEFAULT_HOOK.includes('1,000'));

  // Headline normalization from explicit headline_counts
  const headline = visualsMod.p9NormalizeHeadline({
    total_tasks: 1000,
    verified: 0,
    peer_review: 0,
    cleared_tasks: 0,
    headline_counts: {
      total_audited: 1000,
      not_flawless_count: 850,
      flawless_count: 150,
      not_flawless_rate_pct: 85.0,
      flawless_rate_pct: 15.0,
      cancelled_after_conduct: 120,
      postponed: 310,
      rescheduled: 210,
      delayed: 110,
      cancelled_before_conduct: 45,
      partial_reexam_or_glitch: 55,
      documented_affected_candidates_total: 18500000,
    },
  });
  assert.equal(headline.totalAudited, 1000);
  assert.equal(headline.notFlawlessCount, 850);
  assert.equal(headline.flawlessCount, 150);
  assert.equal(headline.notFlawlessRatePct, 85);
  assert.equal(headline.cancelledAfterConduct, 120);
  assert.equal(headline.postponed, 310);
  assert.equal(headline.rescheduled, 210);
  assert.equal(headline.delayed, 110);
  assert.equal(headline.cancelledBeforeConduct, 45);
  assert.equal(headline.partialReexamOrGlitch, 55);
  assert.equal(headline.documentedAffectedCandidatesTotal, 18500000);

  // Headline fallback when headline_counts is absent (computes from by_disruption_type)
  const derivedHeadline = visualsMod.p9NormalizeHeadline({
    total_tasks: 100,
    verified: 0,
    peer_review: 0,
    cleared_tasks: 0,
    by_disruption_type: {
      cancelled_after_conduct: { disruption_type: 'cancelled_after_conduct', tasks: 10, leads: 2 },
      postponed: { disruption_type: 'postponed', tasks: 30, leads: 5 },
      rescheduled: { disruption_type: 'rescheduled', tasks: 20, leads: 3 },
      delayed: { disruption_type: 'delayed', tasks: 15, leads: 1 },
      cancelled_before_conduct: { disruption_type: 'cancelled_before_conduct', tasks: 5, leads: 0 },
      partial_reexam_or_glitch: { disruption_type: 'partial_reexam_or_glitch', tasks: 5, leads: 0 },
      flawless_conduct: { disruption_type: 'flawless_conduct', tasks: 15, leads: 0, cleared: 15 },
    },
  });
  assert.equal(derivedHeadline.notFlawlessCount, 85);
  assert.equal(derivedHeadline.flawlessCount, 15);
  assert.equal(derivedHeadline.notFlawlessRatePct, 85);

  // All 7 disruptions preserved in canonical order, including flawless_conduct
  const normDisruptions = visualsMod.p9NormalizeDisruptions({
    cancelled_after_conduct: { disruption_type: 'cancelled_after_conduct', tasks: 120, leads: 12, pct: 12.0 },
    postponed: { disruption_type: 'postponed', tasks: 310, leads: 31, pct: 31.0 },
    rescheduled: { disruption_type: 'rescheduled', tasks: 210, leads: 21, pct: 21.0 },
    delayed: { disruption_type: 'delayed', tasks: 110, leads: 11, pct: 11.0 },
    cancelled_before_conduct: { disruption_type: 'cancelled_before_conduct', tasks: 45, leads: 4, pct: 4.5 },
    partial_reexam_or_glitch: { disruption_type: 'partial_reexam_or_glitch', tasks: 55, leads: 5, pct: 5.5 },
    flawless_conduct: { disruption_type: 'flawless_conduct', tasks: 150, leads: 0, cleared: 150, pct: 15.0 },
  });
  assert.equal(normDisruptions.items.length, 7);
  assert.equal(normDisruptions.items[0].key, 'cancelled_after_conduct');
  assert.equal(normDisruptions.items[6].key, 'flawless_conduct');
  assert.equal(normDisruptions.items[6].isCleared, true);
  assert.equal(normDisruptions.totalTasks, 1000);
  assert.equal(normDisruptions.notFlawlessTasks, 850);
  assert.equal(normDisruptions.flawlessTasks, 150);

  // Tier normalization (rich objects & numeric shorthand)
  const richTiers = visualsMod.p9NormalizeTiers({
    union: { tasks: 420, done: 42, claimed: 3, open: 375, pct: 10, leads: 35, cleared: 7, not_flawless: 350, flawless: 70, cancelled_after_conduct: 40 },
    state: { tasks: 510, done: 51, claimed: 2, open: 457, pct: 10, leads: 44, cleared: 7, not_flawless: 440, flawless: 70, cancelled_after_conduct: 72 },
    ut: { tasks: 70, done: 7, claimed: 0, open: 63, pct: 10, leads: 6, cleared: 1, not_flawless: 60, flawless: 10, cancelled_after_conduct: 8 },
  });
  assert.equal(richTiers.totalTasks, 1000);
  assert.equal(richTiers.totalNotFlawless, 850);
  assert.equal(richTiers.totalFlawless, 150);
  assert.equal(richTiers.union.cancelledAfterConduct, 40);

  // Year normalization sorts ascending (2023..2026)
  const years = visualsMod.p9NormalizeYears([
    { year: 2026, tasks: 200, not_flawless: 160, flawless: 40, cancelled_after_conduct: 15, postponed: 60, rescheduled: 45, delayed: 20, cancelled_before_conduct: 10, partial_reexam_or_glitch: 10 },
    { year: 2023, tasks: 50, not_flawless: 40, flawless: 10, cancelled_after_conduct: 5, postponed: 15, rescheduled: 10, delayed: 5, cancelled_before_conduct: 3, partial_reexam_or_glitch: 2 },
    { year: 2024, tasks: 450, not_flawless: 400, flawless: 50, cancelled_after_conduct: 75, postponed: 140, rescheduled: 95, delayed: 45, cancelled_before_conduct: 20, partial_reexam_or_glitch: 25 },
    { year: 2025, tasks: 300, not_flawless: 250, flawless: 50, cancelled_after_conduct: 25, postponed: 95, rescheduled: 60, delayed: 40, cancelled_before_conduct: 12, partial_reexam_or_glitch: 18 },
  ]);
  assert.deepEqual(
    years.map((y) => y.year),
    ['2023', '2024', '2025', '2026'],
  );
  assert.equal(years[0].notFlawlessPct, 80);

  // Jurisdiction filter, sort, exact prefix query, and tier reconciliation
  const jurisdictions = [
    { jurisdiction: 'Union of India', jurisdiction_level: 'union', state_or_ut: null, tasks: 420, leads: 0, not_flawless: 350, flawless: 70, cancelled_after_conduct: 40, postponed: 130, rescheduled: 90, delayed: 50, cancelled_before_conduct: 20, partial_reexam_or_glitch: 20, not_flawless_rate_pct: 83.3 },
    { jurisdiction: 'State:Uttar Pradesh', jurisdiction_level: 'state', state_or_ut: 'Uttar Pradesh', tasks: 65, leads: 0, not_flawless: 60, flawless: 5, cancelled_after_conduct: 22, postponed: 18, rescheduled: 10, delayed: 5, cancelled_before_conduct: 2, partial_reexam_or_glitch: 3, not_flawless_rate_pct: 92.3 },
    { jurisdiction: 'State:Goa', jurisdiction_level: 'state', state_or_ut: 'Goa', tasks: 8, leads: 0, not_flawless: 6, flawless: 2, cancelled_after_conduct: 0, postponed: 3, rescheduled: 2, delayed: 1, cancelled_before_conduct: 0, partial_reexam_or_glitch: 0, not_flawless_rate_pct: 75.0 },
    { jurisdiction: 'UT:Delhi', jurisdiction_level: 'ut', state_or_ut: 'Delhi', tasks: 20, leads: 0, not_flawless: 18, flawless: 2, cancelled_after_conduct: 2, postponed: 8, rescheduled: 5, delayed: 2, cancelled_before_conduct: 0, partial_reexam_or_glitch: 1, not_flawless_rate_pct: 90.0 },
  ];
  assert.equal(visualsMod.p9FilterAndSortJurisdictions(jurisdictions, '', 'state', 'not_flawless').length, 2);
  assert.equal(visualsMod.p9FilterAndSortJurisdictions(jurisdictions, '', 'all', 'rate')[0].jurisdiction, 'State:Uttar Pradesh');
  assert.equal(visualsMod.p9FilterAndSortJurisdictions(jurisdictions, '', 'all', 'name')[0].jurisdiction, 'UT:Delhi');
  assert.equal(visualsMod.p9JurisdictionFilterQuery('State:Goa', 'Goa'), 'Goa ·');
  assert.equal(visualsMod.p9JurisdictionFilterQuery('UT:Delhi', 'Delhi'), 'Delhi ·');
  assert.equal(visualsMod.p9JurisdictionFilterQuery('Union of India', null), 'Union of India ·');
  assert.equal(visualsMod.p9ReconcileJurisdictionForTier('State:Goa', 'union', jurisdictions), 'all');
  assert.equal(visualsMod.p9ReconcileJurisdictionForTier('State:Goa', 'state', jurisdictions), 'State:Goa');

  // Corpus fallback status badge distinguishes queued corpus records from live peer review
  assert.equal(visualsMod.p9LeadStatusMeta('corpus_queued').label, 'Queued in corpus');
  assert.equal(visualsMod.p9LeadStatusMeta('verified').label, 'Verified');
  assert.equal(visualsMod.p9LeadStatusMeta('cleared').label, 'Cleared');

  // Exact pluralization helper
  assert.equal(visualsMod.p9Plural(1, 'exam'), '1 exam');
  assert.equal(visualsMod.p9Plural(0, 'exam'), '0 exams');
  assert.equal(visualsMod.p9Plural(1000, 'exam task'), '1,000 exam tasks');
  assert.equal(visualsMod.p9Plural(1, 'authority', 'authorities'), '1 authority');
  assert.equal(visualsMod.p9Plural(68, 'authority', 'authorities'), '68 authorities');

  // Leads-only fallback in p9NormalizeDisruptions still computes non-zero sharePct
  const leadsOnly = visualsMod.p9NormalizeDisruptions({
    cancelled_after_conduct: { disruption_type: 'cancelled_after_conduct', tasks: 0, leads: 3 },
    postponed: { disruption_type: 'postponed', tasks: 0, leads: 1 },
  });
  assert.equal(leadsOnly.totalTasks, 0);
  assert.equal(leadsOnly.totalLeads, 4);
  assert.equal(leadsOnly.items[0].sharePct, 75);
  assert.equal(leadsOnly.items[1].sharePct, 25);
  assert.equal(leadsOnly.items[2].sharePct, 0);

  // Regression guards for zero-width bar safety, jurisdiction pre-cancel breakdown, active authority filter, hero pluralization, and numeric year formatting
  const visualsSrcCheck = fs.readFileSync(
    path.join(webDir, 'src/components/ProjectVisuals.tsx'),
    'utf8',
  );
  assert.match(visualsSrcCheck, /d\.sharePct > 0 \? Math\.max\(3, d\.sharePct\) : 0/);
  assert.match(visualsSrcCheck, /p9Plural\(headline\.totalAudited, 'exam'\)/);
  assert.match(visualsSrcCheck, /activeAuthorityTypes = authorityTypes\.filter\(\(at\) => at\.tasks > 0\)/);
  assert.match(visualsSrcCheck, /j\.cancelled_before_conduct > 0/);
  assert.match(visualsSrcCheck, /No disruptions/);
  const drawerSrcCheck = fs.readFileSync(
    path.join(webDir, 'src/components/FindingDrawer.tsx'),
    'utf8',
  );
  assert.match(drawerSrcCheck, /k === 'year' \|\| k\.endsWith\('_year'\)/);

  // Multi-axis ledger filtering (disruption, not_flawless, tier, jurisdiction, authority_type, year, text)
  const sampleLeads = [
    {
      finding_id: null,
      task_id: 'p9-union-ugc-net-2024-0001',
      case_key: 'p9-union-ugc-net-2024-0001',
      title: 'UGC-NET June 2024 cancelled after conduct',
      exam_name: 'UGC-NET June 2024',
      exam_year: 2024,
      jurisdiction: 'Union of India',
      jurisdiction_level: 'union',
      state_or_ut: null,
      authority: 'National Testing Agency (NTA)',
      authority_type: 'central_testing_agency',
      disruption_type: 'cancelled_after_conduct',
      disruption_reason_category: 'paper_integrity_compromise',
      students_took_exam_before_cancellation: true,
      status: 'corpus_queued',
    },
    {
      finding_id: null,
      task_id: 'p9-state-uppbpb-constable-2024-0002',
      case_key: 'p9-state-uppbpb-constable-2024-0002',
      title: 'UP Police Constable 2024 cancelled after conduct',
      exam_name: 'UP Police Constable Recruitment 2024',
      exam_year: 2024,
      jurisdiction: 'State:Uttar Pradesh',
      jurisdiction_level: 'state',
      state_or_ut: 'Uttar Pradesh',
      authority: 'UP Police Recruitment and Promotion Board (UPPRPB)',
      authority_type: 'state_police_or_subordinate',
      disruption_type: 'cancelled_after_conduct',
      disruption_reason_category: 'paper_integrity_compromise',
      students_took_exam_before_cancellation: true,
      status: 'corpus_queued',
    },
    {
      finding_id: 'f-p9-upsc-2025',
      task_id: 'p9-union-upsc-cse-2025-0003',
      case_key: 'p9-union-upsc-cse-2025-0003',
      title: 'UPSC CSE Prelims 2025 conducted on schedule',
      exam_name: 'UPSC Civil Services Preliminary Exam 2025',
      exam_year: 2025,
      jurisdiction: 'Union of India',
      jurisdiction_level: 'union',
      state_or_ut: null,
      authority: 'Union Public Service Commission (UPSC)',
      authority_type: 'union_psc_or_recruitment',
      disruption_type: 'flawless_conduct',
      disruption_reason_category: 'none_flawless',
      students_took_exam_before_cancellation: false,
      status: 'cleared',
    },
  ];

  assert.equal(visualsMod.p9FilterLeads(sampleLeads, '', 'not_flawless').length, 2);
  assert.equal(visualsMod.p9FilterLeads(sampleLeads, '', 'flawless_conduct').length, 1);
  assert.equal(visualsMod.p9FilterLeads(sampleLeads, '', 'cancelled_after_conduct', 'state').length, 1);
  assert.equal(visualsMod.p9FilterLeads(sampleLeads, '', 'all', 'all', 'all', 'union_psc_or_recruitment').length, 1);
  assert.equal(visualsMod.p9FilterLeads(sampleLeads, '', 'all', 'all', 'all', 'all', '2025').length, 1);
  assert.equal(visualsMod.p9FilterLeads(sampleLeads, 'upprpb', 'all').length, 1);
  assert.equal(visualsMod.p9FilterLeads(sampleLeads, 'nonexistent-exam-xyz', 'all').length, 0);
});

test('API client normalizes P9 mission fallback code and 3 structured citation roles + secondary provenance', () => {
  const mission = clientMod.normalizeMission({
    slug: 'auditing-government-exams',
    title: 'Auditing Government Exams: 3-year schedule vs. conduct audit across India',
    methodology_code: 'P9',
    pillar: 'audit',
    total_tasks: 1000,
    completed_tasks: 0,
    verified_findings_count: 0,
  });
  assert.equal(mission.project_code, 'P9');
  assert.equal(mission.pillar, 'audit');
  assert.equal(typeof clientMod.api.getAuditingGovernmentExamsSummary, 'function');

  const finding = clientMod.normalizeFinding({
    id: 'f-p9-ugc-net-1',
    methodology_code: 'P9',
    pillar: 'audit',
    title: 'UGC-NET June 2024 cancelled after conduct',
    summary: 'Conducted on 18 June 2024 and cancelled on 19 June 2024 following integrity inputs.',
    monetary_exposure_inr: null,
    secondary_url: 'https://static.pib.gov.in/WriteReadData/specificdocs/documents/2024/jun/doc.pdf',
    secondary_sha256: 'c'.repeat(64),
    citations: [
      {
        role: 'original_schedule',
        title: 'NTA UGC-NET June 2024 Schedule Notice',
        source_url: 'https://nta.ac.in/Download/Notice/Notice_20240618.pdf',
        sha256: 'a'.repeat(64),
        excerpt: 'UGC-NET June 2024 Examination conducted on 18 June 2024.',
      },
      {
        role: 'conduct_outcome',
        title: 'MoE / PIB Cancellation Notice 19 June 2024',
        source_url: 'https://static.pib.gov.in/WriteReadData/specificdocs/documents/2024/jun/doc.pdf',
        sha256: 'c'.repeat(64),
        excerpt: 'Government has decided to cancel the UGC-NET June 2024 Examination.',
      },
      {
        role: 'statutory_benchmark',
        title: 'Public Examinations (Prevention of Unfair Means) Act, 2024',
        source_url: 'https://www.indiacode.nic.in/',
        excerpt: 'Statutory safeguard framework for public examinations.',
      },
    ],
  });
  assert.equal(finding.citations.length, 3);
  assert.deepEqual(
    finding.citations.map((c) => c.role),
    ['original_schedule', 'conduct_outcome', 'statutory_benchmark'],
  );
  assert.equal(
    finding.provenance.secondary_url,
    'https://static.pib.gov.in/WriteReadData/specificdocs/documents/2024/jun/doc.pdf',
  );
  assert.equal(finding.provenance.secondary_sha256, 'c'.repeat(64));
});

test('P9 frontend copy strictly complies with BNS Sec 356 neutrality rules (zero accusatory labels)', () => {
  const forbidden = /\b(scam|corrupt\w*|brib\w*|fraud\w*|kickback\w*|guilty|stolen|rigged|crook\w*|loot\w*|embezzl\w*)\b/i;
  const stringsToCheck = [
    visualsMod.P9_DEFAULT_HOOK,
    utilsMod.METHODOLOGY_DESCRIPTIONS.P9.name,
    utilsMod.METHODOLOGY_DESCRIPTIONS.P9.rule,
    tagsMod.WORK_TYPE_TAGS.exam_conduct.label,
    tagsMod.WORK_TYPE_TAGS.exam_conduct.description,
    visualsMod.p9DisruptionLabel('cancelled_after_conduct'),
    visualsMod.p9DisruptionLabel('postponed'),
    visualsMod.p9DisruptionLabel('rescheduled'),
    visualsMod.p9DisruptionLabel('delayed'),
    visualsMod.p9DisruptionLabel('cancelled_before_conduct'),
    visualsMod.p9DisruptionLabel('partial_reexam_or_glitch'),
    visualsMod.p9DisruptionLabel('flawless_conduct'),
    visualsMod.p9ReasonLabel('paper_integrity_compromise'),
    visualsMod.p9ReasonLabel('administrative_or_logistical'),
    visualsMod.p9ReasonLabel('technical_or_cbt_glitch'),
    visualsMod.p9ReasonLabel('election_or_calendar_clash'),
    visualsMod.p9ReasonLabel('weather_or_disaster'),
    visualsMod.p9ReasonLabel('judicial_or_policy_review'),
    visualsMod.p9ReasonLabel('none_flawless'),
  ];
  for (const s of stringsToCheck) {
    assert.equal(
      forbidden.test(s),
      false,
      `Forbidden accusatory term found in P9 UI string: ${JSON.stringify(s)}`,
    );
  }

  const visualsSrc = fs.readFileSync(path.join(webDir, 'src/components/ProjectVisuals.tsx'), 'utf8');
  const p9Start = visualsSrc.indexOf('/* ---------- 3I. P9 Auditing Government Exams');
  assert.ok(p9Start > 0);
  const p9VisualsSlice = visualsSrc.slice(p9Start);
  assert.equal(forbidden.test(p9VisualsSlice), false, 'Forbidden accusatory term found in P9 visualiser code');

  const drawerSrc = fs.readFileSync(path.join(webDir, 'src/components/FindingDrawer.tsx'), 'utf8');
  const p9DrawerStart = drawerSrc.indexOf('/* P9 Structured Exam Audit Dossier');
  assert.ok(p9DrawerStart > 0);
  assert.equal(
    forbidden.test(drawerSrc.slice(p9DrawerStart, p9DrawerStart + 12000)),
    false,
    'Forbidden accusatory term found in P9 drawer dossier code',
  );

  const briefSrc = fs.readFileSync(path.join(webDir, 'src/pages/BriefPage.tsx'), 'utf8');
  assert.equal(forbidden.test(briefSrc), false, 'Forbidden accusatory term found in BriefPage.tsx');
});

test('AuditingGovernmentExamsVisualiser renders without runtime errors for full, corpus-fallback, and empty visual_summary payloads', () => {
  const baseMission = {
    slug: 'auditing-government-exams',
    project_code: 'P9',
    title: 'Auditing Government Exams: 3-year schedule vs. conduct audit across India',
    pillar: 'audit',
    methodology_code: 'P9',
    jurisdiction: 'All-India',
    statutory_anchors: ['Public Examinations (Prevention of Unfair Means) Act, 2024', 'BNS Sec 356'],
    data_mode: 'live_public',
    status: 'active',
    total_tasks: 1000,
    completed_tasks: 0,
    verified_findings_count: 0,
  };

  const fullSummary = {
    methodology_code: 'P9',
    status_counts: {
      completed: 0,
      claimed: 0,
      queued: 1000,
      waiting: 0,
      planned: 0,
      total: 1000,
      pct_complete: 0,
      audits_done: 0,
      audits_total: 1000,
      checks_done: 0,
      checks_total: 0,
    },
    auditing_government_exams: {
      project_slug: 'auditing-government-exams',
      project_code: 'P9',
      methodology_code: 'P9',
      hook: visualsMod.P9_DEFAULT_HOOK,
      generated_on: '2026-11-28',
      as_of_date: '2026-11-28',
      audit_window: {
        start_date: '2023-11-28',
        end_date: '2026-11-28',
        years: [2023, 2024, 2025, 2026],
        label: 'Nov 2023 – Nov 2026 (3 years)',
      },
      total_tasks: 1000,
      completed_tasks: 0,
      published_leads: 0,
      verified: 0,
      peer_review: 0,
      cleared_tasks: 0,
      unevaluable_tasks: 0,
      unique_exams_covered: 410,
      unique_authorities_covered: 68,
      unique_documents_count: 245,
      states_and_uts_covered: 36,
      leads_source: 'corpus_fallback',
      documents_url: '/api/missions/auditing-government-exams/documents.csv',
      coverage_url: '/api/missions/auditing-government-exams/coverage.json',
      headline_counts: {
        total_audited: 1000,
        not_flawless_count: 850,
        flawless_count: 150,
        not_flawless_rate_pct: 85.0,
        flawless_rate_pct: 15.0,
        cancelled_after_conduct: 120,
        postponed: 310,
        rescheduled: 210,
        delayed: 110,
        cancelled_before_conduct: 45,
        partial_reexam_or_glitch: 55,
        documented_affected_candidates_total: 18500000,
      },
      by_disruption_type: {
        cancelled_after_conduct: { disruption_type: 'cancelled_after_conduct', label: 'Took Exam, Then Cancelled', rule: 'P9-CANCELLED-AFTER-CONDUCT', tasks: 120, done: 0, leads: 0, cleared: 0, pct: 12.0, documented_affected_candidates: 8500000 },
        postponed: { disruption_type: 'postponed', label: 'Postponed', rule: 'P9-POSTPONED', tasks: 310, done: 0, leads: 0, cleared: 0, pct: 31.0, documented_affected_candidates: 4200000 },
        rescheduled: { disruption_type: 'rescheduled', label: 'Rescheduled', rule: 'P9-RESCHEDULED', tasks: 210, done: 0, leads: 0, cleared: 0, pct: 21.0, documented_affected_candidates: 2800000 },
        delayed: { disruption_type: 'delayed', label: 'Delayed', rule: 'P9-DELAYED', tasks: 110, done: 0, leads: 0, cleared: 0, pct: 11.0, documented_affected_candidates: 1500000 },
        cancelled_before_conduct: { disruption_type: 'cancelled_before_conduct', label: 'Cancelled Before Conduct', rule: 'P9-CANCELLED-BEFORE-CONDUCT', tasks: 45, done: 0, leads: 0, cleared: 0, pct: 4.5, documented_affected_candidates: 500000 },
        partial_reexam_or_glitch: { disruption_type: 'partial_reexam_or_glitch', label: 'Technical Glitch / Partial Re-Exam', rule: 'P9-PARTIAL-REEXAM-OR-GLITCH', tasks: 55, done: 0, leads: 0, cleared: 0, pct: 5.5, documented_affected_candidates: 1000000 },
        flawless_conduct: { disruption_type: 'flawless_conduct', label: 'Conducted Flawlessly (Cleared)', rule: null, tasks: 150, done: 0, leads: 0, cleared: 0, pct: 15.0, documented_affected_candidates: 0 },
      },
      by_jurisdiction_level: {
        union: { tasks: 420, done: 0, claimed: 0, open: 420, pct: 0, leads: 0, cleared: 0, not_flawless: 350, flawless: 70, cancelled_after_conduct: 40 },
        state: { tasks: 510, done: 0, claimed: 0, open: 510, pct: 0, leads: 0, cleared: 0, not_flawless: 440, flawless: 70, cancelled_after_conduct: 72 },
        ut: { tasks: 70, done: 0, claimed: 0, open: 70, pct: 0, leads: 0, cleared: 0, not_flawless: 60, flawless: 10, cancelled_after_conduct: 8 },
      },
      by_authority_type: [
        { authority_type: 'central_testing_agency', label: 'Central Testing Agency', tasks: 180, done: 0, leads: 0, not_flawless: 155, flawless: 25, cancelled_after_conduct: 22 },
      ],
      by_exam_category: [
        { exam_category: 'teaching_and_faculty', label: 'Teaching & Faculty', tasks: 160, done: 0, leads: 0, not_flawless: 140, flawless: 20, cancelled_after_conduct: 35 },
      ],
      by_year: [
        { year: 2023, tasks: 40, done: 0, leads: 0, not_flawless: 32, flawless: 8, cancelled_after_conduct: 4, postponed: 12, rescheduled: 8, delayed: 4, cancelled_before_conduct: 2, partial_reexam_or_glitch: 2 },
        { year: 2024, tasks: 460, done: 0, leads: 0, not_flawless: 400, flawless: 60, cancelled_after_conduct: 70, postponed: 148, rescheduled: 92, delayed: 46, cancelled_before_conduct: 20, partial_reexam_or_glitch: 24 },
        { year: 2025, tasks: 310, done: 0, leads: 0, not_flawless: 260, flawless: 50, cancelled_after_conduct: 30, postponed: 95, rescheduled: 68, delayed: 38, cancelled_before_conduct: 13, partial_reexam_or_glitch: 16 },
        { year: 2026, tasks: 190, done: 0, leads: 0, not_flawless: 158, flawless: 32, cancelled_after_conduct: 16, postponed: 55, rescheduled: 42, delayed: 22, cancelled_before_conduct: 10, partial_reexam_or_glitch: 13 },
      ],
      by_reason_category: [
        { reason_category: 'paper_integrity_compromise', label: 'Paper Integrity Safeguard', tasks: 180, leads: 0 },
        { reason_category: 'administrative_or_logistical', label: 'Administrative / Logistical', tasks: 290, leads: 0 },
      ],
      jurisdictions: [
        { jurisdiction: 'Union of India', jurisdiction_level: 'union', state_or_ut: null, tasks: 420, done: 0, leads: 0, cleared: 0, not_flawless: 350, flawless: 70, cancelled_after_conduct: 40, postponed: 130, rescheduled: 90, delayed: 50, cancelled_before_conduct: 20, partial_reexam_or_glitch: 20, not_flawless_rate_pct: 83.3, documented_affected_candidates: 9500000 },
        { jurisdiction: 'State:Uttar Pradesh', jurisdiction_level: 'state', state_or_ut: 'Uttar Pradesh', tasks: 65, done: 0, leads: 0, cleared: 0, not_flawless: 60, flawless: 5, cancelled_after_conduct: 22, postponed: 18, rescheduled: 10, delayed: 5, cancelled_before_conduct: 2, partial_reexam_or_glitch: 3, not_flawless_rate_pct: 92.3, documented_affected_candidates: 6000000 },
      ],
      top_authorities: [
        { authority: 'National Testing Agency (NTA)', authority_key: 'nta', authority_type: 'central_testing_agency', jurisdiction: 'Union of India', jurisdiction_level: 'union', tasks: 140, done: 0, leads: 0, not_flawless: 120, flawless: 20, cancelled_after_conduct: 18, postponed: 45, rescheduled: 32, delayed: 10, cancelled_before_conduct: 5, partial_reexam_or_glitch: 10, not_flawless_rate_pct: 85.7, documented_affected_candidates: 6800000 },
      ],
      anchor_cases: [
        {
          task_id: 'p9-union-ugc-net-june-2024-0001',
          case_key: 'p9-union-ugc-net-june-2024-0001',
          finding_id: null,
          exam_name: 'UGC-NET June 2024',
          exam_slug: 'ugc-net-june-2024',
          cycle_or_stage: 'June 2024 Cycle',
          exam_year: 2024,
          jurisdiction: 'Union of India',
          jurisdiction_level: 'union',
          state_or_ut: null,
          authority: 'National Testing Agency (NTA)',
          authority_key: 'nta',
          authority_type: 'central_testing_agency',
          exam_category: 'teaching_and_faculty',
          scheduled_date: '2024-06-18',
          actual_or_event_date: '2024-06-19',
          delay_days: 64,
          disruption_type: 'cancelled_after_conduct',
          disruption_reason_category: 'paper_integrity_compromise',
          students_took_exam_before_cancellation: true,
          schedule_summary: 'Conducted on 18 June 2024 across 317 cities.',
          schedule_document_ref: 'NTA Notice 18.06.2024',
          outcome_summary: 'Cancelled on 19 June 2024 following I4C integrity inputs.',
          outcome_document_ref: 'PIB Press Release 19.06.2024',
          affected_candidates_count: 908580,
          affected_candidates_basis: 'PIB Press Release',
          statutory_anchor: 'Public Examinations (Prevention of Unfair Means) Act, 2024',
          source_url: 'https://nta.ac.in/Download/Notice/Notice_20240618.pdf',
          secondary_url: 'https://static.pib.gov.in/WriteReadData/specificdocs/documents/2024/jun/doc.pdf',
          portal_url: 'https://nta.ac.in/',
          severity: 'high',
        },
      ],
      leads: [
        {
          finding_id: null,
          task_id: 'p9-union-ugc-net-june-2024-0001',
          case_key: 'p9-union-ugc-net-june-2024-0001',
          title: 'UGC-NET June 2024 cancelled after conduct',
          exam_name: 'UGC-NET June 2024',
          exam_slug: 'ugc-net-june-2024',
          cycle_or_stage: 'June 2024 Cycle',
          exam_year: 2024,
          jurisdiction: 'Union of India',
          jurisdiction_level: 'union',
          state_or_ut: null,
          authority: 'National Testing Agency (NTA)',
          authority_key: 'nta',
          authority_type: 'central_testing_agency',
          exam_category: 'teaching_and_faculty',
          scheduled_date: '2024-06-18',
          actual_or_event_date: '2024-06-19',
          delay_days: 64,
          disruption_type: 'cancelled_after_conduct',
          disruption_reason_category: 'paper_integrity_compromise',
          signal_category: 'cancelled_after_conduct',
          rule_triggered: 'P9-CANCELLED-AFTER-CONDUCT',
          students_took_exam_before_cancellation: true,
          schedule_summary: 'Conducted on 18 June 2024 across 317 cities.',
          outcome_summary: 'Cancelled on 19 June 2024 following integrity inputs.',
          affected_candidates_count: 908580,
          affected_candidates_basis: 'PIB Press Release',
          source_url: 'https://nta.ac.in/Download/Notice/Notice_20240618.pdf',
          is_anchor_case: true,
          severity: 'high',
          status: 'corpus_queued',
        },
      ],
      dry_run_summary: {
        status: 'passed',
        evaluated_tasks: 1000,
        flagged_leads: 850,
        cleared_flawless: 150,
        unevaluable: 0,
        verified_documents: 245,
        as_of_date: '2026-11-28',
      },
    },
  };

  assert.doesNotThrow(() => {
    visualsMod.AuditingGovernmentExamsVisualiser({
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
    visualsMod.AuditingGovernmentExamsVisualiser({
      mission: baseMission,
      visualSummary: undefined,
    });
    visualsMod.AuditingGovernmentExamsVisualiser({
      mission: baseMission,
      visualSummary: {
        methodology_code: 'P9',
        status_counts: fullSummary.status_counts,
        auditing_government_exams: {
          total_tasks: 1000,
          verified: 0,
          peer_review: 0,
          cleared_tasks: 0,
          leads_source: 'corpus_fallback',
          anchor_cases: [],
          leads: [],
        },
      },
    });
  });
});
