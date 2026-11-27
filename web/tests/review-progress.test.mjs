import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as esbuild from 'esbuild';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(__dirname, '..');

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'review-progress-web-test-'));
const progressOut = path.join(tmpDir, 'progress.mjs');
const clientOut = path.join(tmpDir, 'client.mjs');

await Promise.all([
  esbuild.build({
    entryPoints: [path.join(webDir, 'src/lib/progress.ts')],
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    outfile: progressOut,
  }),
  esbuild.build({
    entryPoints: [path.join(webDir, 'src/api/client.ts')],
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    outfile: clientOut,
  }),
]);

const progressMod = await import(pathToFileURL(progressOut).href);
const clientMod = await import(pathToFileURL(clientOut).href);

test.after(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test('3 independent reviews: all tasks done starts at 70% and adds 10% per review', () => {
  const steps = [
    [0, 70, 30],
    [10, 80, 20],
    [20, 90, 10],
    [30, 100, 0],
  ];
  for (const [doneReviews, expectedPct, expectedRemainingPct] of steps) {
    const res = progressMod.computeProjectProgress({
      completed: 100,
      total: 100,
      peerReview: doneReviews < 30 ? 10 : 0,
      reviewsRequired: 30,
      reviewsDone: doneReviews,
      requiredReviews: 3,
    });
    assert.equal(res.perReviewPct, 10);
    assert.equal(res.progressPct, expectedPct);
    assert.equal(res.pctComplete, expectedPct);
    assert.equal(res.reviewRemainingPct, expectedRemainingPct);
    assert.equal(
      progressMod.formatProgressPct(res.progressPct, res.hasIncompleteWork),
      `${expectedPct.toFixed(1)}%`,
    );
  }
});

test('5 independent reviews: all tasks done starts at 70% and adds 6% per review', () => {
  const steps = [
    [0, 70, 30],
    [10, 76, 24],
    [20, 82, 18],
    [30, 88, 12],
    [40, 94, 6],
    [50, 100, 0],
  ];
  for (const [doneReviews, expectedPct, expectedRemainingPct] of steps) {
    const res = progressMod.computeProjectProgress({
      completed: 100,
      total: 100,
      peerReview: doneReviews < 50 ? 10 : 0,
      reviewsRequired: 50,
      reviewsDone: doneReviews,
      requiredReviews: 5,
    });
    assert.equal(res.perReviewPct, 6);
    assert.equal(progressMod.formatPerReviewPct(res.perReviewPct), '6%');
    assert.equal(res.progressPct, expectedPct);
    assert.equal(res.pctComplete, expectedPct);
    assert.equal(res.reviewRemainingPct, expectedRemainingPct);
  }
});

test('never reports 100.0% while any review or task remains', () => {
  const almostDone = progressMod.computeProjectProgress({
    completed: 10000,
    total: 10000,
    peerReview: 1,
    reviewsRequired: 2974 * 3,
    reviewsDone: 2973 * 3 + 2,
    requiredReviews: 3,
  });
  assert.equal(almostDone.hasReviewsRemaining, true);
  assert.equal(almostDone.progressPct, 99.9);
  assert.equal(progressMod.formatProgressPct(almostDone.progressPct, almostDone.hasIncompleteWork), '99.9%');
  assert.equal(almostDone.reviewRemainingPct, 0.1);

  // Minimal fallback with only verified + peerReview counts still stays below 100%
  const fallback = progressMod.computeProjectProgress({
    completed: 100,
    total: 100,
    verified: 30,
    peerReview: 18,
  });
  assert.equal(fallback.hasReviewsRemaining, true);
  assert.ok(fallback.progressPct < 100);
  assert.equal(fallback.progressPct, 88.8);
});

test('projects with zero reviews remaining (e.g. website checks) reach 100.0%', () => {
  const webDone = progressMod.computeProjectProgress({
    completed: 1000,
    total: 1000,
  });
  assert.equal(webDone.progressPct, 100);
  assert.equal(webDone.hasReviewsRemaining, false);
  assert.equal(progressMod.formatProgressPct(webDone.progressPct, webDone.hasIncompleteWork), '100.0%');
});

test('normalizeMission preserves server review-weighted progress fields', () => {
  const m = clientMod.normalizeMission({
    slug: 'police-finances',
    title: 'Police Finances',
    total_tasks: 100,
    completed_tasks: 100,
    progress_pct: 98.3,
    reviews_required: 813,
    reviews_done: 768,
    reviews_remaining: 45,
    required_reviews: 3,
    per_review_pct: 10,
    task_progress_pct: 70,
    review_progress_pct: 28.34,
    review_remaining_pct: 1.66,
    has_reviews_remaining: true,
  });
  assert.equal(m.progress_pct, 98.3);
  assert.equal(m.reviews_required, 813);
  assert.equal(m.reviews_done, 768);
  assert.equal(m.reviews_remaining, 45);
  assert.equal(m.required_reviews, 3);
  assert.equal(m.per_review_pct, 10);
  assert.equal(m.review_remaining_pct, 1.66);
  assert.equal(m.has_reviews_remaining, true);
});
