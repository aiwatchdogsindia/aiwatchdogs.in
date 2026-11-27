import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as esbuild from 'esbuild';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(__dirname, '..');

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'p8-web-test-'));
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

test('P8 project metadata helpers resolve slug, code, work type, pillar, WebMCP, and official scheme URLs', () => {
  assert.equal(utilsMod.projectCodeFor({ slug: 'sovereign-lies' }), 'P8');
  assert.equal(utilsMod.projectCodeFor({ methodology_code: 'P8' }), 'P8');
  assert.equal(utilsMod.pillarLabel('scheme'), 'Welfare & Social Audits');
  assert.equal(utilsMod.workTypeLabel('anomaly_scan', 'P8'), 'Scheme promises');
  assert.equal(tagsMod.getWorkTypeTag(undefined, 'P8').id, 'sovereign_promises');
  assert.ok(utilsMod.METHODOLOGY_DESCRIPTIONS.P8?.name.includes('Sovereign Lies'));

  const projectLegend = tagsMod.SITE_LEGEND_GROUPS.find((g) => g.id === 'project');
  assert.ok(projectLegend?.tags.some((t) => t.id === 'sovereign_promises'));

  // Official Indian scheme/statutory hosts including RBI, IndiaBudget, IndiaCode, PFRDA, Sansad, PIB
  assert.equal(utilsMod.isOfficialGovUrl('https://www.rbi.org.in/commonperson/English/Scripts/FAQs.aspx?Id=1658'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://rbi.org.in/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://www.indiabudget.gov.in/doc/Finance_Bill.pdf'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://www.indiacode.nic.in/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://www.pfrda.org.in/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://sansad.in/getFile/loksabhaquestions/annex/185/AU123.pdf'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://bucketapi.rajyasabha.digital/RSQ/267/AU123.pdf'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://rajyasabha.digital/'), true);
  assert.equal(utilsMod.isOfficialGovUrl('https://fake-rajyasabha.digital.example.com/q.pdf'), false);
  assert.equal(utilsMod.isOfficialGovUrl('http://bucketapi.rajyasabha.digital/insecure.pdf'), false);
  assert.equal(utilsMod.isOfficialGovUrl('https://fake-rbi.org.in.example.com/sgb'), false);
  assert.equal(utilsMod.isOfficialGovUrl('http://www.rbi.org.in/insecure'), false);

  // WebMCP tool manifest documents sovereign-lies and P8
  const getProjectTool = webmcpMod.WEBMCP_TOOL_MANIFEST.find((t) => t.name === 'get_project');
  const getJoinTool = webmcpMod.WEBMCP_TOOL_MANIFEST.find((t) => t.name === 'get_task_join_prompt');
  assert.match(getProjectTool.inputSchema.properties.slug.description, /sovereign-lies/);
  assert.match(getJoinTool.inputSchema.properties.methodology_code.description, /P8/);
});

test('BriefPage lists P8 Sovereign Lies, FindingsPage supports scheme pillar, and MissionsPage wires P8', () => {
  const findingsSrc = fs.readFileSync(path.join(webDir, 'src/pages/FindingsPage.tsx'), 'utf8');
  assert.match(findingsSrc, /PILLAR_FILTERS\s*=\s*\[[^\]]*'scheme'/);

  const briefSrc = fs.readFileSync(path.join(webDir, 'src/pages/BriefPage.tsx'), 'utf8');
  assert.match(briefSrc, /slug:\s*'sovereign-lies'/);
  assert.match(briefSrc, /code:\s*'P8'/);

  const missionsSrc = fs.readFileSync(path.join(webDir, 'src/pages/MissionsPage.tsx'), 'utf8');
  assert.match(missionsSrc, /SOVEREIGN_LIES_METHOD\s*=\s*'P8'/);
  assert.match(missionsSrc, /sovereign_lies/);
});

test('P8 canonical enum formatters handle all 7 walk-back mechanisms, 8 scheme categories, 6 impact scopes, jurisdictions, and elapsed spans', () => {
  // All 7 canonical walk-back mechanisms + rule-code aliases
  assert.equal(utilsMod.formatWalkbackMechanism('retroactive_tax_reversal').key, 'retroactive_tax_reversal');
  assert.equal(utilsMod.formatWalkbackMechanism('P8-RETROACTIVE-TAX-REVERSAL').key, 'retroactive_tax_reversal');
  assert.equal(utilsMod.formatWalkbackMechanism('midstream_benefit_cut').key, 'midstream_benefit_cut');
  assert.equal(utilsMod.formatWalkbackMechanism('P8-MIDSTREAM-BENEFIT-CUT').key, 'midstream_benefit_cut');
  assert.equal(utilsMod.formatWalkbackMechanism('premature_discontinuation').key, 'premature_discontinuation');
  assert.equal(utilsMod.formatWalkbackMechanism('P8-PREMATURE-DISCONTINUATION').key, 'premature_discontinuation');
  assert.equal(utilsMod.formatWalkbackMechanism('eligibility_narrowing').key, 'eligibility_narrowing');
  assert.equal(utilsMod.formatWalkbackMechanism('P8-ELIGIBILITY-NARROWING').key, 'eligibility_narrowing');
  assert.equal(utilsMod.formatWalkbackMechanism('lockin_or_exit_restriction').key, 'lockin_or_exit_restriction');
  assert.equal(utilsMod.formatWalkbackMechanism('P8-LOCKIN-OR-EXIT-RESTRICTION').key, 'lockin_or_exit_restriction');
  assert.equal(utilsMod.formatWalkbackMechanism('arrears_or_unfunded_promise').key, 'arrears_or_unfunded_promise');
  assert.equal(utilsMod.formatWalkbackMechanism('P8-ARREARS-OR-UNFUNDED-PROMISE').key, 'arrears_or_unfunded_promise');
  const clearedMeta = utilsMod.formatWalkbackMechanism('grandfathered_or_honored');
  assert.equal(clearedMeta.key, 'grandfathered_or_honored');
  assert.equal(clearedMeta.isCleared, true);

  // All 8 canonical scheme categories
  const expectedCats = [
    ['sovereign_bonds_small_savings', 'Sovereign Bonds & Small Savings'],
    ['pensions_retirement', 'Pensions & Retirement'],
    ['tax_exempt_instruments', 'Tax-Exempt Instruments'],
    ['cash_transfers_guarantees', 'Cash Transfers & Guarantees'],
    ['ev_housing_farm_subsidies', 'EV, Housing & Farm Subsidies'],
    ['scholarships_education', 'Scholarships & Education'],
    ['industrial_export_incentives', 'Industrial & Export Incentives'],
    ['welfare_health_public_services', 'Welfare, Health & Public Services'],
  ];
  for (const [key, label] of expectedCats) {
    assert.equal(utilsMod.formatSchemeCategory(key).label, label);
  }

  // All 6 canonical retroactive impacts
  const retro = utilsMod.formatRetroactiveImpact('retroactive_existing_holders');
  assert.equal(retro.isRetroactive, true);
  assert.equal(retro.shortLabel, 'Retroactive');
  assert.equal(utilsMod.formatRetroactiveImpact('mid_stream_active_scheme').isRetroactive, false);
  assert.equal(utilsMod.formatRetroactiveImpact('prospective_discontinuation').key, 'prospective_discontinuation');
  assert.equal(utilsMod.formatRetroactiveImpact('eligibility_narrowing').key, 'eligibility_narrowing');
  assert.equal(utilsMod.formatRetroactiveImpact('unfulfilled_arrears').key, 'unfulfilled_arrears');
  assert.equal(utilsMod.formatRetroactiveImpact('grandfathered_or_honored').key, 'grandfathered_or_honored');

  // Jurisdiction formatting
  assert.equal(utilsMod.formatJurisdictionLabel('Union of India', null, 'union'), 'Union of India');
  assert.equal(utilsMod.formatJurisdictionLabel('State:Maharashtra', 'Maharashtra', 'state'), 'Maharashtra');
  assert.equal(utilsMod.formatJurisdictionLabel('State:Karnataka'), 'Karnataka');
  assert.equal(utilsMod.formatJurisdictionLabel('UT:Delhi', 'Delhi', 'ut'), 'Delhi (UT)');
  assert.equal(utilsMod.formatJurisdictionLabel('UT:Puducherry'), 'Puducherry (UT)');

  // Elapsed span formatting
  assert.equal(utilsMod.formatElapsedPromiseSpan(3718), '3,718 days (~10.2 yrs)');
  assert.equal(utilsMod.formatElapsedPromiseSpan(240), '240 days');
  assert.equal(utilsMod.formatElapsedPromiseSpan(1), '1 day');
  assert.equal(utilsMod.formatElapsedPromiseSpan(null), null);
  assert.equal(utilsMod.formatElapsedPromiseSpan(-5), null);

  // Citation role labels
  assert.equal(utilsMod.formatCitationRoleLabel('original_promise'), 'Original Promise');
  assert.equal(utilsMod.formatCitationRoleLabel('subsequent_walkback'), 'Subsequent Walk-Back');
  assert.equal(utilsMod.formatCitationRoleLabel('statutory_benchmark'), 'Statutory Benchmark');
});

test('extractSovereignLiesMetrics parses full SGB anchor payload and returns null for unrelated metrics', () => {
  const sgbExtracted = utilsMod.extractSovereignLiesMetrics({
    case_key: 'p8-union-sgb-anchor-0001',
    scheme_name: 'Sovereign Gold Bond (SGB) Scheme',
    scheme_slug: 'sovereign-gold-bond-scheme',
    tranche_or_phase: 'All 67 Tranches (2015-16 Series I to 2023-24 Series IV)',
    jurisdiction: 'Union of India',
    jurisdiction_level: 'union',
    state_or_ut: null,
    authority: 'Department of Economic Affairs, Ministry of Finance & RBI',
    authority_key: 'mof-dea-rbi',
    scheme_category: 'sovereign_bonds_small_savings',
    government_era_or_launch_year: '2015',
    launch_year: '2015',
    walkback_year: '2026',
    promise_date: '2015-10-30',
    promise_summary:
      'Original Gazette notification and Finance Act 2016 Sec 47(viic) exempted capital gains tax on redemption of SGBs by any individual holder at maturity.',
    promise_document_ref: 'Finance Act 2016 Sec 47(viic) & GoI Notification F.No.4(19)-W&M/2014',
    promise_excerpt: 'any transfer of Sovereign Gold Bond issued by the Reserve Bank of India by way of redemption, by an assessee being an individual',
    promise_locator: 'Section 47(viic)',
    promise_tokens_matched: ['Sovereign Gold Bond', 'redemption', '47(viic)'],
    walkback_date: '2026-02-01',
    walkback_summary:
      'Finance Bill / Finance Act 2026 Clause 35 restricted capital gains tax exemption on maturity redemption exclusively to original primary subscribers holding continuously until maturity.',
    walkback_document_ref: 'Union Budget 2026-27 Finance Bill Clause 35 / Memorandum',
    walkback_excerpt: 'subscribed to by an individual at the time of original issue and has been held continuously until redemption on maturity',
    walkback_locator: 'Clause 35, p. 22',
    walkback_tokens_matched: ['original issue', 'continuously', 'redemption on maturity'],
    days_from_promise_to_walkback: 3747,
    walkback_mechanism: 'retroactive_tax_reversal',
    retroactive_impact: 'retroactive_existing_holders',
    signal_category: 'retroactive_tax_reversal',
    verdict: 'flagged',
    triggered_rules: ['P8-RETROACTIVE-TAX-REVERSAL'],
    promised_metric: '0% LTCG on redemption for all individual holders',
    revised_metric: 'Exemption restricted to original primary subscribers only',
    metric_unit: 'tax_exemption_scope',
    affected_group: 'Secondary-market retail SGB holders',
    estimated_exposure_inr: null,
    exposure_basis: null,
    statutory_anchor: 'Income-tax Act 1961 Sec 47(viic); Finance Act 2026',
    secondary_role: 'walkback_document',
    is_sgb_anchor: true,
    is_anchor_case: true,
    as_of_date: '2026-11-28',
  });

  assert.ok(sgbExtracted);
  assert.equal(sgbExtracted.isSgbAnchor, true);
  assert.equal(sgbExtracted.daysFromPromiseToWalkback, 3747);
  assert.equal(sgbExtracted.walkbackMechanism, 'retroactive_tax_reversal');
  assert.equal(sgbExtracted.retroactiveImpact, 'retroactive_existing_holders');
  assert.deepEqual(sgbExtracted.promiseTokensMatched, ['Sovereign Gold Bond', 'redemption', '47(viic)']);
  assert.deepEqual(sgbExtracted.triggeredRules, ['P8-RETROACTIVE-TAX-REVERSAL']);
  assert.equal(utilsMod.extractSovereignLiesMetrics({ unrelated_key: 123 }), null);
});

test('P8 FindingCard and FindingDrawer headline metrics avoid misleading ₹0 and handle exposure, deltas, cleared, and quarantined records cleanly', () => {
  const sgbNonMonetary = {
    methodology_code: 'P8',
    monetary_exposure_inr: 0,
    amount_label: 'scheme_promises_and_revisions',
    anomaly_metrics: {
      scheme_name: 'Sovereign Gold Bond (SGB) Scheme',
      walkback_mechanism: 'retroactive_tax_reversal',
      days_from_promise_to_walkback: 3747,
      promised_metric: '0% LTCG all holders',
      revised_metric: 'Primary only',
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(sgbNonMonetary), {
    label: 'Walk-Back Mechanism',
    value: 'Tax reversal · 3,747d',
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(sgbNonMonetary), {
    value: '0% LTCG all holders → Primary only',
    prominent: true,
  });

  // Published exposure takes precedence when > 0
  const publishedExposureP8 = {
    methodology_code: 'P8',
    monetary_exposure_inr: 15_000_000_000,
    anomaly_metrics: {
      scheme_name: 'FAME-II Electric 2-Wheeler Subsidy',
      walkback_mechanism: 'midstream_benefit_cut',
      estimated_exposure_inr: 15_000_000_000,
      days_from_promise_to_walkback: 1522,
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(publishedExposureP8), {
    label: 'Published Scheme Exposure',
    value: utilsMod.formatInr(15_000_000_000),
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(publishedExposureP8), {
    value: utilsMod.formatInr(15_000_000_000),
    prominent: true,
  });

  // Cleared grandfathered/honored scheme
  const clearedP8 = {
    methodology_code: 'P8',
    monetary_exposure_inr: null,
    status: 'cleared',
    anomaly_metrics: {
      scheme_name: 'Sukanya Samriddhi Yojana (Regular Accounts)',
      walkback_mechanism: 'grandfathered_or_honored',
      retroactive_impact: 'grandfathered_or_honored',
    },
  };
  assert.deepEqual(utilsMod.resolveFindingDrawerMetric(clearedP8), {
    label: 'Scheme Status',
    value: 'Honored (Cleared)',
  });
  assert.deepEqual(utilsMod.resolveFindingCardMetric(clearedP8), {
    value: 'Honored (Cleared)',
    prominent: true,
  });

  // Quarantined stays withheld
  assert.deepEqual(
    utilsMod.resolveFindingCardMetric({ ...sgbNonMonetary, quarantined: true }, 'Withheld'),
    { value: 'Withheld', prominent: false },
  );
});

test('SovereignLiesVisualiser helpers normalize tiers, 7 mechanisms, categories, impacts, and multi-axis filters', () => {
  assert.equal(typeof visualsMod.SovereignLiesVisualiser, 'function');
  assert.equal(
    visualsMod.P8_DEFAULT_HOOK,
    'The government made a promise when it launched the scheme. Did it keep it?',
  );

  // Tier normalization (rich objects and numeric shorthand)
  const richTiers = visualsMod.p8NormalizeTiers({
    union: { tasks: 520, done: 104, claimed: 5, open: 411, pct: 20, leads: 48, cleared: 12 },
    state: { tasks: 430, done: 43, claimed: 2, open: 385, pct: 10, leads: 30, cleared: 5 },
    ut: { tasks: 50, done: 10, claimed: 0, open: 40, pct: 20, leads: 6, cleared: 1 },
  });
  assert.equal(richTiers.totalTasks, 1000);
  assert.equal(richTiers.totalDone, 157);
  assert.equal(richTiers.totalLeads, 84);
  assert.equal(richTiers.totalCleared, 18);

  const numericTiers = visualsMod.p8NormalizeTiers({ union: 600, state: 350, ut: 50 });
  assert.equal(numericTiers.totalTasks, 1000);
  assert.equal(numericTiers.ut.open, 50);

  // Mechanism normalization preserves all 7 canonical buckets including grandfathered_or_honored
  const normMechs = visualsMod.p8NormalizeMechanisms(
    {
      retroactive_tax_reversal: { tasks: 150, leads: 20 },
      midstream_benefit_cut: { tasks: 250, leads: 35 },
      premature_discontinuation: { tasks: 180, leads: 15 },
      eligibility_narrowing: { tasks: 170, leads: 12 },
      lockin_or_exit_restriction: { tasks: 100, leads: 8 },
      arrears_or_unfunded_promise: { tasks: 100, leads: 10 },
      grandfathered_or_honored: { tasks: 50, leads: 0 },
    },
    null,
  );
  assert.equal(normMechs.items.length, 7);
  assert.equal(normMechs.totalTasks, 1000);
  assert.equal(normMechs.totalLeads, 100);
  assert.equal(normMechs.items[0].key, 'retroactive_tax_reversal');
  assert.equal(normMechs.items[0].sharePct, 15);
  assert.equal(normMechs.items[6].key, 'grandfathered_or_honored');
  assert.equal(normMechs.items[6].isCleared, true);

  // Category normalization sorts by task count descending
  const normCats = visualsMod.p8NormalizeCategories([
    { category: 'pensions_retirement', tasks: 120, leads: 10 },
    { category: 'sovereign_bonds_small_savings', tasks: 240, leads: 30 },
  ]);
  assert.equal(normCats[0].category, 'sovereign_bonds_small_savings');
  assert.equal(normCats[0].label, 'Sovereign Bonds & Small Savings');

  // Retroactive impact normalization
  const normImpacts = visualsMod.p8NormalizeRetroactiveImpacts({
    retroactive_existing_holders: 200,
    mid_stream_active_scheme: 300,
    prospective_discontinuation: 200,
    eligibility_narrowing: 150,
    unfulfilled_arrears: 100,
    grandfathered_or_honored: 50,
  });
  assert.equal(normImpacts.total, 1000);
  assert.equal(normImpacts.items[0].key, 'retroactive_existing_holders');

  // Filter helpers
  const jurisdictions = [
    { jurisdiction: 'Union of India', jurisdiction_level: 'union', state_or_ut: null, tasks: 520, leads: 40 },
    { jurisdiction: 'State:Maharashtra', jurisdiction_level: 'state', state_or_ut: 'Maharashtra', tasks: 45, leads: 5 },
    { jurisdiction: 'UT:Delhi', jurisdiction_level: 'ut', state_or_ut: 'Delhi', tasks: 20, leads: 3 },
  ];
  assert.equal(visualsMod.p8FilterJurisdictions(jurisdictions, '', 'state').length, 1);
  assert.equal(visualsMod.p8FilterJurisdictions(jurisdictions, 'delhi', 'all').length, 1);

  // Exact jurisdiction prefix token `${jur_short} ·` prevents substring collisions (e.g. Goa vs Goal)
  assert.equal(visualsMod.p8JurisdictionFilterQuery('State:Maharashtra', 'Maharashtra'), 'Maharashtra ·');
  assert.equal(visualsMod.p8JurisdictionFilterQuery('UT:Delhi'), 'Delhi ·');
  assert.equal(visualsMod.p8JurisdictionFilterQuery('State:Goa', 'Goa'), 'Goa ·');
  assert.equal(visualsMod.p8JurisdictionFilterQuery('Union of India', null), 'Union of India ·');
  assert.equal(visualsMod.p8JurisdictionFilterQuery('   ', null), '');

  const sampleP8Titles = [
    'Goa · Dayanand Social Security Scheme: Pension Arrears',
    'Union of India · National Solar Mission Goal 100 GW: Subsidy Revision',
    'Union of India · Goa Shipyard Incentive Notice: Revised Terms',
  ];
  const goaNeedle = visualsMod.p8JurisdictionFilterQuery('State:Goa', 'Goa').toLowerCase();
  const goaMatches = sampleP8Titles.filter((t) => t.toLowerCase().includes(goaNeedle));
  assert.equal(goaMatches.length, 1);
  assert.equal(goaMatches[0], sampleP8Titles[0]);

  // Switching tier reconciles an incompatible active jurisdiction filter back to 'all'
  assert.equal(
    visualsMod.p8ReconcileJurisdictionForTier('State:Maharashtra', 'union', jurisdictions),
    'all',
  );
  assert.equal(
    visualsMod.p8ReconcileJurisdictionForTier('State:Maharashtra', 'ut', jurisdictions),
    'all',
  );
  assert.equal(
    visualsMod.p8ReconcileJurisdictionForTier('State:Maharashtra', 'state', jurisdictions),
    'State:Maharashtra',
  );
  assert.equal(
    visualsMod.p8ReconcileJurisdictionForTier('State:Maharashtra', 'all', jurisdictions),
    'State:Maharashtra',
  );

  // Leads-only fallback in p8NormalizeMechanisms still computes non-zero sharePct
  const leadsOnlyMechs = visualsMod.p8NormalizeMechanisms(null, {
    retroactive_tax_reversal: 3,
    midstream_benefit_cut: 1,
  });
  assert.equal(leadsOnlyMechs.totalTasks, 0);
  assert.equal(leadsOnlyMechs.totalLeads, 4);
  assert.equal(leadsOnlyMechs.items[0].sharePct, 75);
  assert.equal(leadsOnlyMechs.items[1].sharePct, 25);

  // Regression guards for zero-done bar width, leads-only mechanism bar, singular/plural labels, numeric year formatting, and clipped card delta
  const visualsSrcCheck = fs.readFileSync(
    path.join(webDir, 'src/components/ProjectVisuals.tsx'),
    'utf8',
  );
  assert.match(visualsSrcCheck, /donePct > 0 \?/);
  assert.match(visualsSrcCheck, /m\.sharePct > 0 \? Math\.max\(3, m\.sharePct\) : 0/);
  assert.match(visualsSrcCheck, /bucket\.leads === 1 \? 'lead' : 'leads'/);
  assert.match(visualsSrcCheck, /m\.leads === 1 \? 'lead' : 'leads'/);
  assert.match(visualsSrcCheck, /s\.tasks === 1 \? 'task' : 'tasks'/);
  const drawerSrcCheck = fs.readFileSync(
    path.join(webDir, 'src/components/FindingDrawer.tsx'),
    'utf8',
  );
  assert.match(drawerSrcCheck, /k === 'year' \|\| k\.endsWith\('_year'\)/);
  const cardSrcCheck = fs.readFileSync(
    path.join(webDir, 'src/components/FindingCard.tsx'),
    'utf8',
  );
  assert.match(cardSrcCheck, /clipCardText\(p8Record\.promisedMetric/);

  const schemes = [
    {
      scheme_name: 'Sovereign Gold Bond (SGB) Scheme',
      scheme_slug: 'sgb',
      jurisdiction: 'Union of India',
      jurisdiction_level: 'union',
      scheme_category: 'sovereign_bonds_small_savings',
      walkback_mechanism: 'retroactive_tax_reversal',
      retroactive_impact: 'retroactive_existing_holders',
      tasks: 68,
      leads: 12,
      is_sgb_anchor: true,
    },
    {
      scheme_name: 'Mukhyamantri Majhi Ladki Bahin Yojana',
      scheme_slug: 'ladki-bahin',
      jurisdiction: 'State:Maharashtra',
      jurisdiction_level: 'state',
      state_or_ut: 'Maharashtra',
      scheme_category: 'cash_transfers_guarantees',
      walkback_mechanism: 'eligibility_narrowing',
      retroactive_impact: 'eligibility_narrowing',
      tasks: 12,
      leads: 3,
      is_sgb_anchor: false,
    },
  ];
  assert.equal(visualsMod.p8FilterSchemes(schemes, 'gold', 'all', 'all', 'all', 'all').length, 1);
  assert.equal(visualsMod.p8FilterSchemes(schemes, '', 'state', 'all', 'all', 'all').length, 1);
  assert.equal(visualsMod.p8FilterSchemes(schemes, '', 'all', 'cash_transfers_guarantees', 'all', 'all').length, 1);
  assert.equal(visualsMod.p8FilterSchemes(schemes, '', 'all', 'all', 'retroactive_tax_reversal', 'all').length, 1);
  assert.equal(visualsMod.p8FilterSchemes(schemes, '', 'all', 'all', 'all', 'State:Maharashtra').length, 1);
});

test('API client normalizes P8 mission fallback code and 3 structured citation roles + secondary provenance', () => {
  const mission = clientMod.normalizeMission({
    slug: 'sovereign-lies',
    title: 'Sovereign Lies: launch promises vs. subsequent walk-backs across Indian schemes',
    methodology_code: 'P8',
    pillar: 'scheme',
    total_tasks: 1000,
    completed_tasks: 0,
    verified_findings_count: 0,
  });
  assert.equal(mission.project_code, 'P8');
  assert.equal(mission.pillar, 'scheme');

  const finding = clientMod.normalizeFinding({
    id: 'f-p8-sgb-1',
    methodology_code: 'P8',
    pillar: 'scheme',
    title: 'Sovereign Gold Bond (SGB): Sec 47(viic) maturity tax exemption restricted to primary subscribers',
    summary: 'Original launch terms exempted all individual holders; Finance Act 2026 restricted exemption to original primary subscribers.',
    monetary_exposure_inr: null,
    secondary_url: 'https://www.indiabudget.gov.in/doc/Finance_Bill.pdf',
    secondary_sha256: 'b'.repeat(64),
    citations: [
      {
        role: 'original_promise',
        title: 'Finance Act 2016 Sec 47(viic)',
        source_url: 'https://www.indiacode.nic.in/',
        sha256: 'a'.repeat(64),
        excerpt: 'Transfer of Sovereign Gold Bond by way of redemption by an individual.',
      },
      {
        role: 'subsequent_walkback',
        title: 'Finance Bill 2026 Clause 35',
        source_url: 'https://www.indiabudget.gov.in/doc/Finance_Bill.pdf',
        sha256: 'b'.repeat(64),
        excerpt: 'Subscribed to by an individual at the time of original issue and held continuously.',
      },
      {
        role: 'statutory_benchmark',
        title: 'Income-tax Act 1961 Sec 47(viic)',
        source_url: 'https://incometaxindia.gov.in/',
        excerpt: 'Statutory capital-gains redemption rule.',
      },
    ],
  });
  assert.equal(finding.citations.length, 3);
  assert.deepEqual(
    finding.citations.map((c) => c.role),
    ['original_promise', 'subsequent_walkback', 'statutory_benchmark'],
  );
  assert.equal(finding.provenance.secondary_url, 'https://www.indiabudget.gov.in/doc/Finance_Bill.pdf');
  assert.equal(finding.provenance.secondary_sha256, 'b'.repeat(64));
});

test('P8 frontend copy strictly complies with BNS Sec 356 neutrality rules (zero accusatory labels)', () => {
  const forbidden = /\b(scam|corrupt\w*|brib\w*|fraud\w*|kickback\w*|guilty|stolen|rigged|crook\w*|loot\w*|embezzl\w*)\b/i;
  const stringsToCheck = [
    visualsMod.P8_DEFAULT_HOOK,
    utilsMod.METHODOLOGY_DESCRIPTIONS.P8.name,
    utilsMod.METHODOLOGY_DESCRIPTIONS.P8.rule,
    tagsMod.WORK_TYPE_TAGS.sovereign_promises.label,
    tagsMod.WORK_TYPE_TAGS.sovereign_promises.description,
    visualsMod.p8MechanismLabel('retroactive_tax_reversal'),
    visualsMod.p8MechanismLabel('midstream_benefit_cut'),
    visualsMod.p8MechanismLabel('premature_discontinuation'),
    visualsMod.p8MechanismLabel('eligibility_narrowing'),
    visualsMod.p8MechanismLabel('lockin_or_exit_restriction'),
    visualsMod.p8MechanismLabel('arrears_or_unfunded_promise'),
    visualsMod.p8MechanismLabel('grandfathered_or_honored'),
    visualsMod.p8CategoryLabel('sovereign_bonds_small_savings'),
    visualsMod.p8CategoryLabel('pensions_retirement'),
    visualsMod.p8CategoryLabel('tax_exempt_instruments'),
    visualsMod.p8CategoryLabel('cash_transfers_guarantees'),
    visualsMod.p8CategoryLabel('ev_housing_farm_subsidies'),
    visualsMod.p8CategoryLabel('scholarships_education'),
    visualsMod.p8CategoryLabel('industrial_export_incentives'),
    visualsMod.p8CategoryLabel('welfare_health_public_services'),
    visualsMod.p8ImpactLabel('retroactive_existing_holders'),
    visualsMod.p8ImpactLabel('mid_stream_active_scheme'),
    visualsMod.p8ImpactLabel('prospective_discontinuation'),
    visualsMod.p8ImpactLabel('eligibility_narrowing'),
    visualsMod.p8ImpactLabel('unfulfilled_arrears'),
    visualsMod.p8ImpactLabel('grandfathered_or_honored'),
  ];
  for (const s of stringsToCheck) {
    assert.equal(
      forbidden.test(s),
      false,
      `Forbidden accusatory term found in P8 UI string: ${JSON.stringify(s)}`,
    );
  }

  // Also scan P8 section of ProjectVisuals.tsx and BriefPage.tsx
  const visualsSrc = fs.readFileSync(path.join(webDir, 'src/components/ProjectVisuals.tsx'), 'utf8');
  const p8Start = visualsSrc.indexOf('/* ---------- 3H. P8 Sovereign Lies');
  assert.ok(p8Start > 0);
  const p8VisualsSlice = visualsSrc.slice(p8Start);
  assert.equal(forbidden.test(p8VisualsSlice), false, 'Forbidden accusatory term found in P8 visualiser code');

  const briefSrc = fs.readFileSync(path.join(webDir, 'src/pages/BriefPage.tsx'), 'utf8');
  assert.equal(forbidden.test(briefSrc), false, 'Forbidden accusatory term found in BriefPage.tsx');
});

test('SovereignLiesVisualiser renders without runtime errors for full, partial, and empty visual_summary payloads', () => {
  const baseMission = {
    slug: 'sovereign-lies',
    project_code: 'P8',
    title: 'Sovereign Lies: launch promises vs. subsequent walk-backs across Indian schemes',
    pillar: 'scheme',
    methodology_code: 'P8',
    jurisdiction: 'All-India',
    statutory_anchors: ['Income-tax Act Sec 47(viic)', 'Finance Act 2026', 'BNS Sec 356'],
    data_mode: 'live_public',
    status: 'active',
    total_tasks: 1000,
    completed_tasks: 25,
    verified_findings_count: 8,
  };

  const fullSummary = {
    methodology_code: 'P8',
    status_counts: {
      completed: 25,
      claimed: 3,
      queued: 972,
      waiting: 0,
      planned: 0,
      total: 1000,
      pct_complete: 2.5,
      audits_done: 25,
      audits_total: 1000,
      checks_done: 0,
      checks_total: 0,
    },
    sovereign_lies: {
      hook: visualsMod.P8_DEFAULT_HOOK,
      generated_on: '2026-11-28',
      as_of_date: '2026-11-28',
      total_tasks: 1000,
      unique_schemes_covered: 120,
      states_and_uts_covered: 32,
      sgb_tasks_count: 68,
      cleared_control_tasks_embedded: 50,
      leads: 20,
      verified: 8,
      peer_review: 12,
      cleared_tasks: 5,
      flagged_total_inr_cr: 1500.0,
      by_jurisdiction_level: {
        union: { tasks: 520, done: 15, claimed: 2, open: 503, pct: 2.88, leads: 12, cleared: 3 },
        state: { tasks: 430, done: 8, claimed: 1, open: 421, pct: 1.86, leads: 6, cleared: 2 },
        ut: { tasks: 50, done: 2, claimed: 0, open: 48, pct: 4.0, leads: 2, cleared: 0 },
      },
      by_mechanism: {
        retroactive_tax_reversal: { tasks: 150, leads: 6 },
        midstream_benefit_cut: { tasks: 250, leads: 5 },
        premature_discontinuation: { tasks: 180, leads: 4 },
        eligibility_narrowing: { tasks: 170, leads: 2 },
        lockin_or_exit_restriction: { tasks: 100, leads: 2 },
        arrears_or_unfunded_promise: { tasks: 100, leads: 1 },
        grandfathered_or_honored: { tasks: 50, leads: 0 },
      },
      by_signal: {
        retroactive_tax_reversal: 6,
        midstream_benefit_cut: 5,
        premature_discontinuation: 4,
        eligibility_narrowing: 2,
        lockin_or_exit_restriction: 2,
        arrears_or_unfunded_promise: 1,
      },
      by_retroactive_impact: {
        retroactive_existing_holders: 200,
        mid_stream_active_scheme: 300,
        prospective_discontinuation: 200,
        eligibility_narrowing: 150,
        unfulfilled_arrears: 100,
        grandfathered_or_honored: 50,
      },
      by_category: [
        { category: 'sovereign_bonds_small_savings', label: 'Sovereign Bonds & Small Savings', tasks: 200, done: 10, leads: 8, flagged_inr_cr: 0 },
        { category: 'ev_housing_farm_subsidies', label: 'EV, Housing & Farm Subsidies', tasks: 160, done: 5, leads: 4, flagged_inr_cr: 1500 },
      ],
      jurisdictions: [
        { jurisdiction: 'Union of India', jurisdiction_level: 'union', state_or_ut: null, tasks: 520, done: 15, leads: 12, cleared: 3, flagged_inr_cr: 1500 },
        { jurisdiction: 'State:Maharashtra', jurisdiction_level: 'state', state_or_ut: 'Maharashtra', tasks: 45, done: 3, leads: 2, cleared: 1, flagged_inr_cr: 0 },
        { jurisdiction: 'UT:Delhi', jurisdiction_level: 'ut', state_or_ut: 'Delhi', tasks: 20, done: 2, leads: 2, cleared: 0, flagged_inr_cr: 0 },
      ],
      top_schemes: [
        {
          scheme_name: 'Sovereign Gold Bond (SGB) Scheme',
          scheme_slug: 'sovereign-gold-bond-scheme',
          jurisdiction: 'Union of India',
          jurisdiction_level: 'union',
          scheme_category: 'sovereign_bonds_small_savings',
          walkback_mechanism: 'retroactive_tax_reversal',
          retroactive_impact: 'retroactive_existing_holders',
          tasks: 68,
          leads: 6,
          is_sgb_anchor: true,
        },
      ],
      sgb_anchor_case: {
        task_id: 'p8-union-sgb-anchor-0001',
        finding_id: 'f-p8-sgb-1',
        scheme_name: 'Sovereign Gold Bond (SGB) Scheme',
        tranche_or_phase: 'Flagship Anchor (2015–2026)',
        jurisdiction: 'Union of India',
        authority: 'Ministry of Finance & RBI',
        promise_date: '2015-10-30',
        promise_summary: 'Capital gains tax exempted on maturity redemption for all individual holders under Sec 47(viic).',
        promise_document_ref: 'Finance Act 2016 Sec 47(viic)',
        walkback_date: '2026-02-01',
        walkback_summary: 'Finance Act 2026 restricted exemption exclusively to original primary subscribers holding continuously until maturity.',
        walkback_document_ref: 'Finance Bill 2026 Clause 35',
        walkback_mechanism: 'retroactive_tax_reversal',
        retroactive_impact: 'retroactive_existing_holders',
        promised_metric: '0% LTCG for all individual holders',
        revised_metric: 'Primary subscribers only',
        affected_group: 'Secondary-market retail SGB holders',
        days_from_promise_to_walkback: 3747,
        source_url: 'https://www.indiabudget.gov.in/doc/memo.pdf',
        secondary_url: 'https://www.indiabudget.gov.in/doc/Finance_Bill.pdf',
        portal_url: 'https://www.rbi.org.in/',
      },
      top_leads: [
        {
          finding_id: 'f-p8-sgb-1',
          title: 'Sovereign Gold Bond (SGB): maturity tax exemption narrowed to primary subscribers',
          scheme_name: 'Sovereign Gold Bond (SGB) Scheme',
          scheme_slug: 'sovereign-gold-bond-scheme',
          tranche_or_phase: '2015–2026',
          entity_name: 'Department of Economic Affairs, Ministry of Finance',
          jurisdiction: 'Union of India',
          jurisdiction_level: 'union',
          state_or_ut: null,
          scheme_category: 'sovereign_bonds_small_savings',
          walkback_mechanism: 'retroactive_tax_reversal',
          retroactive_impact: 'retroactive_existing_holders',
          signal_category: 'retroactive_tax_reversal',
          promise_date: '2015-10-30',
          walkback_date: '2026-02-01',
          days_from_promise_to_walkback: 3747,
          promised_metric: '0% LTCG all holders',
          revised_metric: 'Primary subscribers only',
          promise_summary: 'Sec 47(viic) exempted individual maturity redemption.',
          walkback_summary: 'Finance Act 2026 restricted exemption to original issue subscribers.',
          affected_group: 'Secondary-market SGB holders',
          is_sgb_anchor: true,
          monetary_exposure_inr: null,
          flagged_inr_cr: 0,
          severity: 'high',
          status: 'verified',
        },
      ],
      documents_url: '/api/missions/sovereign-lies/documents.csv',
      coverage_url: '/api/missions/sovereign-lies/coverage.json',
    },
  };

  assert.doesNotThrow(() => {
    visualsMod.SovereignLiesVisualiser({
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
    visualsMod.SovereignLiesVisualiser({
      mission: baseMission,
      visualSummary: undefined,
    });
    visualsMod.SovereignLiesVisualiser({
      mission: baseMission,
      visualSummary: {
        methodology_code: 'P8',
        status_counts: fullSummary.status_counts,
        sovereign_lies: {
          total_tasks: 1000,
          leads: 0,
          verified: 0,
          peer_review: 0,
          cleared_tasks: 0,
          flagged_total_inr_cr: 0,
          by_jurisdiction_level: { union: 550, state: 400, ut: 50 },
          sgb_anchor_case: null,
          top_leads: [],
        },
      },
    });
  });
});
