import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import esbuild from 'esbuild';

const webDir = path.resolve(import.meta.dirname, '..');
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'awd-intake-test-'));
const utilsOut = path.join(tmpDir, 'utils.mjs');
const tagsOut = path.join(tmpDir, 'tags.mjs');

await esbuild.build({
  entryPoints: [path.join(webDir, 'src/utils.ts')],
  bundle: true,
  format: 'esm',
  platform: 'neutral',
  define: { 'import.meta.env.DEV': 'false' },
  outfile: utilsOut,
});

await esbuild.build({
  entryPoints: [path.join(webDir, 'src/lib/tags.ts')],
  bundle: true,
  format: 'esm',
  platform: 'neutral',
  define: { 'import.meta.env.DEV': 'false' },
  outfile: tagsOut,
});

const utils = await import(pathToFileURL(utilsOut).href);
const tags = await import(pathToFileURL(tagsOut).href);

test.after(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test('pillarLabel formats infra cleanly and never shows raw lowercase infra', () => {
  assert.equal(utils.pillarLabel('infra'), 'Infrastructure & Works');
  assert.equal(utils.pillarLabel('infrastructure'), 'Infrastructure & Works');
  assert.equal(utils.pillarLabel('budget'), 'Union & State Budgets');
  assert.equal(utils.pillarLabel('scheme'), 'Welfare & Social Audits');
});

test('getWorkTypeTag returns Proposal instead of Budget check for dataset-less proposals', () => {
  assert.equal(tags.getWorkTypeTag('proposal').label, 'Proposal');
  assert.equal(tags.getWorkTypeTag('needs_data', '').label, 'Proposal');
});

test('splitActiveAndProposalProjects keeps needs_data 0-task proposals separate and below active audits', () => {
  const mixed = [
    { slug: 'police-finances', project_code: 'P1', status: 'active', total_tasks: 100 },
    { slug: 'innocent-but-locked-up', project_code: 'P10', status: 'active', total_tasks: 1000 },
    { slug: 'cg-road-proposal', project_code: 'P11', status: 'needs_data', total_tasks: 0 },
    { slug: 'cg-icds-proposal', status: 'needs_data', total_tasks: 0 },
  ];

  const { active, proposals } = utils.splitActiveAndProposalProjects(mixed);
  assert.deepEqual(active.map((m) => m.slug), ['innocent-but-locked-up', 'police-finances']);
  assert.deepEqual(proposals.map((m) => m.slug), ['cg-icds-proposal', 'cg-road-proposal']);

  // Even plain sortProjectsDescending puts needs_data proposals after active projects.
  const sorted = utils.sortProjectsDescending(mixed);
  assert.deepEqual(sorted.map((m) => m.slug), [
    'innocent-but-locked-up',
    'police-finances',
    'cg-icds-proposal',
    'cg-road-proposal',
  ]);
});

test('HomePage filters out needs_data proposals and MissionsPage renders separate intake lane without bogus Budget check badge', () => {
  const homeSrc = fs.readFileSync(path.join(webDir, 'src/pages/HomePage.tsx'), 'utf8');
  assert.match(homeSrc, /isProposalMission/);
  assert.match(homeSrc, /\/projects#proposals/);

  const missionsSrc = fs.readFileSync(path.join(webDir, 'src/pages/MissionsPage.tsx'), 'utf8');
  assert.match(missionsSrc, /splitActiveAndProposalProjects/);
  assert.match(missionsSrc, /id="proposals"/);
  assert.match(missionsSrc, /Community proposals · Needs datasets/);
  assert.match(missionsSrc, /\(m\.methodology_code \|\| m\.status !== 'needs_data'\)/);
});
