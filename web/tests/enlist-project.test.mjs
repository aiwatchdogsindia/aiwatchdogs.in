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

test('ListProjectRow is wired into MissionsPage, HomePage, and discovery surfaces', () => {
  const listRowSrc = fs.readFileSync(path.join(webDir, 'src/components/ListProjectRow.tsx'), 'utf8');
  assert.match(listRowSrc, /List your project: copy one line/);
  assert.match(listRowSrc, /Your project here/);
  assert.match(listRowSrc, /buildEnlistPrompt/);
  assert.match(listRowSrc, /JoinOneLineButton/);

  const missionsSrc = fs.readFileSync(path.join(webDir, 'src/pages/MissionsPage.tsx'), 'utf8');
  assert.match(missionsSrc, /<ListProjectRow\b/);
  assert.match(missionsSrc, /\/enlist\.txt/);
  assert.match(missionsSrc, /Project guide on GitHub/);
  assert.match(missionsSrc, /Enlisting PR/);

  const homeSrc = fs.readFileSync(path.join(webDir, 'src/pages/HomePage.tsx'), 'utf8');
  assert.match(homeSrc, /<ListProjectRow\b/);
  assert.match(homeSrc, /variant="race"/);

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
