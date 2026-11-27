import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as esbuild from 'esbuild';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(__dirname, '..');

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'quorum3-web-test-'));
const clientOut = path.join(tmpDir, 'client.mjs');
const tagsOut = path.join(tmpDir, 'tags.mjs');

await Promise.all([
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
]);

const clientMod = await import(pathToFileURL(clientOut).href);
const tagsMod = await import(pathToFileURL(tagsOut).href);

test.after(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test('normalizeFinding defaults required_quorum to 3 and preserves explicit server quorum values', () => {
  const defaultFinding = clientMod.normalizeFinding({
    id: 'fnd-default-q3',
    title: 'Default quorum finding',
    summary: 'Summary',
    reproduction_summary: {
      computational_confirm_count: 1,
      editorial_review_count: 0,
    },
  });
  assert.equal(defaultFinding.required_quorum, 3);
  assert.equal(defaultFinding.reproduction_summary?.required_quorum, 3);

  const legacyFinding = clientMod.normalizeFinding({
    id: 'fnd-legacy-q2',
    title: 'Legacy quorum finding',
    summary: 'Summary',
    confirm_count: 2,
    required_quorum: 2,
    reproduction_summary: {
      computational_confirm_count: 2,
      editorial_review_count: 0,
      required_quorum: 2,
    },
  });
  assert.equal(legacyFinding.required_quorum, 2);
  assert.equal(legacyFinding.reproduction_summary?.required_quorum, 2);
});

test('FINDING_TAGS copy reflects 3 pairwise-independent verifications', () => {
  assert.match(tagsMod.FINDING_TAGS.verified.description, /Three pairwise-independent agents/i);
  assert.match(tagsMod.FINDING_TAGS.in_review.description, /three independent peer checks/i);
  assert.doesNotMatch(tagsMod.FINDING_TAGS.verified.description, /two independent/i);
  assert.doesNotMatch(tagsMod.FINDING_TAGS.split_vote.description, /third independent/i);
});
