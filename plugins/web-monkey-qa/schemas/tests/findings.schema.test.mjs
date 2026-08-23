// findings.schema.json の出力契約テスト (zero-dep / node 組み込みのみ)
//   実行: node --test plugins/web-monkey-qa/schemas/tests/findings.schema.test.mjs
//
// 設計: 完全な JSON Schema バリデータは持ち込まず (local-first / zero-dep)、
//   schema 文書から「実際に効くべき制約」を読み取り、現実的な findings payload に適用する。
//   ハードコードした期待値ではなく schema 由来の制約をテストするため、schema が
//   これらを encode していなければ RED になる (cleanup-queue.schema.test.mjs と同型)。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(here, '..', 'findings.schema.json'), 'utf8'));

const topRequired = schema.required ?? [];
const findingSchema = schema.properties.findings.items;
const findingRequired = findingSchema.required ?? [];
const severityEnum = findingSchema.properties.severity.enum;
const categoryEnum = findingSchema.properties.category.enum;
const viewportEnum = findingSchema.properties.viewport.enum;
const fingerprintPattern = findingSchema.properties.fingerprint.pattern;
const envKindEnum = schema.properties.metadata.properties.environment.properties.kind.enum;
const statsRequired = schema.properties.statistics.required ?? [];

// schema 由来の制約だけを適用する faithful な部分バリデータ。
function validate(doc) {
  const errors = [];
  for (const key of topRequired) if (!(key in doc)) errors.push(`top required '${key}' missing`);
  if (schema.additionalProperties === false) {
    for (const key of Object.keys(doc)) {
      if (!(key in (schema.properties ?? {}))) errors.push(`top extra key '${key}'`);
    }
  }
  for (const f of doc.findings ?? []) {
    for (const key of findingRequired) if (!(key in f)) errors.push(`finding required '${key}' missing`);
    if (findingSchema.additionalProperties === false) {
      for (const key of Object.keys(f)) {
        if (!(key in findingSchema.properties)) errors.push(`finding extra key '${key}'`);
      }
    }
    if ('severity' in f && !severityEnum.includes(f.severity)) errors.push(`severity '${f.severity}' not in enum`);
    if ('category' in f && !categoryEnum.includes(f.category)) errors.push(`category '${f.category}' not in enum`);
    if ('viewport' in f && !viewportEnum.includes(f.viewport)) errors.push(`viewport '${f.viewport}' not in enum`);
    if ('fingerprint' in f && fingerprintPattern && !new RegExp(fingerprintPattern).test(f.fingerprint)) {
      errors.push(`fingerprint '${f.fingerprint}' violates pattern`);
    }
  }
  const env = doc.metadata?.environment;
  if (env && 'kind' in env && !envKindEnum.includes(env.kind)) errors.push(`environment.kind '${env.kind}' not in enum`);
  for (const key of statsRequired) if (!(doc.statistics && key in doc.statistics)) errors.push(`statistics required '${key}' missing`);
  return { ok: errors.length === 0, errors };
}

const mkFinding = (over = {}) => ({
  id: 'mq-001',
  fingerprint: 'a1b2c3d4e5f60718',
  severity: 'High',
  category: 'http-5xx',
  url: 'https://app.example.test/dashboard',
  viewport: 'desktop',
  signal: 'GET /api/v1/items 500',
  repro_steps: ['/ を開く', '「一覧」をクリック'],
  ...over,
});
const mkDoc = (findings = [mkFinding()]) => ({
  metadata: { date: '2026-07-09', environment: { kind: 'local', target: 'local' }, sections_run: ['public'] },
  findings,
  statistics: { high: 1, medium: 0, low: 0 },
});

test('valid findings document passes', () => {
  const r = validate(mkDoc());
  assert.equal(r.ok, true, `valid doc should pass (errors: ${r.errors})`);
});

test('unknown category is rejected', () => {
  assert.equal(validate(mkDoc([mkFinding({ category: 'timeout' })])).ok, false);
});

test('fingerprint must be exactly 16 lowercase hex', () => {
  for (const bad of ['ZZZ', 'A1B2C3D4E5F60718', 'a1b2c3d4e5f6071', 'a1b2c3d4e5f6071899']) {
    assert.equal(validate(mkDoc([mkFinding({ fingerprint: bad })])).ok, false, `bad fingerprint: ${bad}`);
  }
});

test('severity Critical is rejected (monkey is 3-tier High/Medium/Low)', () => {
  assert.equal(validate(mkDoc([mkFinding({ severity: 'Critical' })])).ok, false);
});

test('finding with extra key is rejected (additionalProperties false)', () => {
  assert.equal(validate(mkDoc([mkFinding({ bogus: true })])).ok, false);
});

test('top-level extra key is rejected', () => {
  const doc = mkDoc(); doc.extra = true;
  assert.equal(validate(doc).ok, false);
});

test('missing finding required field is rejected', () => {
  const f = mkFinding(); delete f.signal;
  assert.equal(validate(mkDoc([f])).ok, false);
});

test('unknown viewport is rejected (enum desktop/mobile only)', () => {
  assert.equal(validate(mkDoc([mkFinding({ viewport: 'tablet' })])).ok, false);
  assert.equal(validate(mkDoc([mkFinding({ viewport: 'desktop' })])).ok, true); // 対の positive
});

test('unknown metadata.environment.kind is rejected', () => {
  const doc = mkDoc(); doc.metadata.environment.kind = 'prod';
  assert.equal(validate(doc).ok, false);
});

test('missing statistics required key is rejected', () => {
  const doc = mkDoc(); delete doc.statistics.low;
  assert.equal(validate(doc).ok, false);
});
