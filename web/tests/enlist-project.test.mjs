import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as esbuild from 'esbuild';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(__dirname, '..');
const repoDir = path.resolve(webDir, '..');

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'enlist-test-'));
const utilsOut = path.join(tmpDir, 'utils.mjs');

await esbuild.build({
  entryPoints: [path.join(webDir, 'src/utils.ts')],
  bundle: true,
  format: 'esm',
  platform: 'neutral',
  define: {
    'import.meta.env.DEV': 'false',
  },
  outfile: utilsOut,
});

const utils = await import(pathToFileURL(utilsOut).href);

test.after(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test('buildEnlistPrompt and CANONICAL_ENLIST_PROMPT use /enlist.txt one-line format', () => {
  assert.equal(
    utils.CANONICAL_ENLIST_PROMPT,
    'Fetch and follow https://aiwatchdogs.in/enlist.txt to list my audit project with me',
  );
  assert.equal(
    utils.buildEnlistPrompt('https://aiwatchdogs.in/'),
    'Fetch and follow https://aiwatchdogs.in/enlist.txt to list my audit project with me',
  );
  assert.equal(
    utils.buildEnlistPrompt('http://localhost:5173'),
    'Fetch and follow http://localhost:5173/enlist.txt to list my audit project with me',
  );
});

test('projectCodeFor falls back to PRJ for newly listed projects without project_code or methodology_code', () => {
  assert.equal(utils.projectCodeFor({ slug: 'pune-road-repair-spending' }), 'PRJ');
  assert.equal(utils.projectCodeFor({ slug: 'police-finances' }), 'P1');
  assert.equal(utils.projectCodeFor({ project_code: 'P9', slug: 'pune-road-repair-spending' }), 'P9');
});

test('sortProjectsDescending places latest listed projects first (unnumbered, then P10, P9, P8...P1)', () => {
  const input = [
    { slug: 'police-finances', project_code: 'P1', created_at: '2026-11-20T00:00:00Z' },
    { slug: 'gov-in-public-information', project_code: 'P2', created_at: '2026-11-21T00:00:00Z' },
    { slug: 'only-one-bid', project_code: 'P7', created_at: '2026-11-26T00:00:00Z' },
    { slug: 'sovereign-lies', project_code: 'P8', created_at: '2026-11-27T00:00:00Z' },
    { slug: 'future-ten', project_code: 'P10', created_at: '2026-11-28T00:00:00Z' },
    { slug: 'pune-road-repair-spending', created_at: '2026-11-28T05:00:00Z' },
  ];
  const sorted = utils.sortProjectsDescending(input);
  assert.deepEqual(
    sorted.map((m) => m.slug),
    [
      'pune-road-repair-spending',
      'future-ten',
      'sovereign-lies',
      'only-one-bid',
      'gov-in-public-information',
      'police-finances',
    ],
  );
  assert.equal(
    utils.nextProjectCode([
      { slug: 'police-finances', project_code: 'P1' },
      { slug: 'sovereign-lies', project_code: 'P8' },
    ]),
    'P9',
  );
});

test('ListProjectRow matches project-index-card proportions and sits at the top before projects', () => {
  const listRowSrc = fs.readFileSync(path.join(webDir, 'src/components/ListProjectRow.tsx'), 'utf8');
  assert.match(listRowSrc, /project-index-card/);
  assert.match(listRowSrc, /project-index-card--enlist/);
  assert.match(listRowSrc, /project-index-title/);
  assert.match(listRowSrc, /List your project: copy one line/);
  assert.match(listRowSrc, /Your project here/);
  assert.match(listRowSrc, /buildEnlistPrompt/);
  assert.match(listRowSrc, /JoinOneLineButton/);

  const missionsSrc = fs.readFileSync(path.join(webDir, 'src/pages/MissionsPage.tsx'), 'utf8');
  assert.match(missionsSrc, /sortProjectsDescending/);
  assert.match(missionsSrc, /\/enlist\.txt/);
  assert.match(missionsSrc, /Project guide on GitHub/);
  assert.match(missionsSrc, /Enlisting PR/);
  assert.ok(
    missionsSrc.indexOf('<ListProjectRow') < missionsSrc.indexOf('visible.map('),
    'ListProjectRow must render at the top before the projects list on /projects',
  );

  const homeSrc = fs.readFileSync(path.join(webDir, 'src/pages/HomePage.tsx'), 'utf8');
  assert.match(homeSrc, /sortProjectsDescending/);
  assert.match(homeSrc, /variant="race"/);
  assert.ok(
    homeSrc.indexOf('<ListProjectRow') < homeSrc.indexOf('<ul className="tracks">'),
    'ListProjectRow must render at the top before the project tracks on /',
  );

  const footerSrc = fs.readFileSync(path.join(webDir, 'src/components/Footer.tsx'), 'utf8');
  assert.match(footerSrc, /\/enlist\.txt/);

  const howSrc = fs.readFileSync(path.join(webDir, 'src/pages/HowItWorksPage.tsx'), 'utf8');
  assert.match(howSrc, /\/enlist\.txt/);
  assert.doesNotMatch(howSrc, /Chosen and added by the AI Watchdogs India team/);

  const briefSrc = fs.readFileSync(path.join(webDir, 'src/pages/BriefPage.tsx'), 'utf8');
  assert.match(briefSrc, /\/enlist\.txt/);
  assert.doesNotMatch(briefSrc, /Outside project submissions are not accepted/i);

  const enlistTxt = fs.readFileSync(path.join(webDir, 'public/enlist.txt'), 'utf8');
  assert.match(enlistTxt, /list an audit project/);
  assert.doesNotMatch(enlistTxt, /project listing is closed/i);

  const prTemplate = fs.readFileSync(
    path.join(repoDir, '.github/PULL_REQUEST_TEMPLATE/project.md'),
    'utf8',
  );
  assert.match(prTemplate, /aiwatchdogs project validate/);
});
