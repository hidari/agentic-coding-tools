---
name: e2e-scenario-impact-check
description: フロントエンド変更が E2E テスト（特にステージングシナリオテスト）を将来壊す可能性を、PR マージ前に静的検出する。`getByRole` / `getByLabel` / `getByText` の整合性、画面構造変化（ボタン数増減、`<button>` ↔ `<Link>` 変更、モーダル追加など）、role / aria-label / placeholder 変更、ルート変更、フォーム構造変化を agent 経由で判断。「同じ画面を別経路で叩く別テストの取りこぼし」も検出する。dev-workflow:pre-merge-quality-gate Phase 1 から並列呼び出しされる。単独で「E2E 影響チェックして」「a11y リファクタ後の E2E 整合性確認」と頼まれた時にも使う。staging E2E は唯一の自動 E2E 検出網（ローカル E2E は CI で動かない）であるため、staging 爆発前に shift-left で検出する。
---

# E2E Scenario Impact Check

CI の lint と typecheck は E2E テストの **実行時整合性** を検出できない。`getByRole('button', { name: /保存/i })` が画面の構造変化で複数マッチして strict mode violation になるようなパターンは、staging デプロイ後の E2E 実行まで爆発しない。本 skill はこのギャップを **PR マージ前の静的検査** で埋める。

## 目的

- **Shift-left**: フロント変更が E2E を壊す可能性を、staging デプロイまで遅延させずに PR マージ前に検出する
- **取りこぼし防止**: 同じ画面を複数の E2E テストが別経路で叩いている場合、片方だけ修正されたまま他方が取り残されるパターンを検出する
- **構造的代替**: ローカル E2E (`make e2e-test-chrome`) は CI で動かないため人間/Claude のローカル実行依存。本 skill は静的検査でそのギャップを部分的に補う（ただし動的検証の代替ではない）

## いつ使うか

### 必ず使う

- dev-workflow:pre-merge-quality-gate Phase 1 の並列タスクから自動呼び出し（PR 作成前 / マージ前）
- 大きめのフロントエンド変更完了直後（a11y リファクタ完了、フォーム構造変更、ルーティング変更）
- 「E2E に影響あるか確認して」「E2E 整合性チェック」とユーザーから指示されたとき

### 使わない / 早期スキップする

- フロント変更が無い PR（`frontend/` 配下に diff が無い場合は即 skip を返す）
- E2E テストファイル自体だけを修正している PR（`e2e/` のみ変更）
- WIP コミットの push 段階

## ワークフロー

### Phase 0: 入力収集

`Bash` で以下を取得する:

```bash
git diff <base>..HEAD --name-only           # 変更ファイル一覧
git diff <base>..HEAD --stat                # 変更概要
```

`<base>` は通常 `main`。

### Phase 1: 早期スキップ判定

変更ファイルリストに `frontend/` 配下のファイルが **1つも無ければ** 即 skip を返す:

```
## E2E Scenario Impact Check Result

- フロント変更検出: no (skip)
- 影響を受ける可能性のあるテスト: 0 件
- false positive 抑制 OK: yes (フロント変更なしのため能動的に skip)

→ Skip OK
```

E2E テストファイル自体の修正のみ (`e2e/` のみ変更) でも skip。

### Phase 2: agent 起動による静的検査

フロント変更がある場合、`Agent` tool で `general-purpose` を起動する:

- subagent_type: `general-purpose`
- prompt: 以下を渡す
  - フロント変更の概要（`git diff <base>..HEAD --stat` の結果）
  - 変更ファイル一覧
  - 全 diff 取得の指示（agent が `git diff <base>..HEAD` を必要に応じて実行）
  - `e2e/playwright/tests/` 配下のテストファイル一覧
  - 検出パターン hint（下記）
  - 出力フォーマット指定（下記 Phase 3）

#### agent への検出パターン hint

agent には以下の典型パターンを明示的に伝える:

1. **`<button>` → `<Link>` 化**: `getByRole('button', ...)` が爆発する。staging-paid-user / staging-billing-exempt / artwork-crud などで getByRole の取りこぼしが起きやすい
2. **画面構造の変化（ボタン数増減）**: 同じ name/role を持つ要素が増えると `strict mode violation` になる。例: 保存ボタン2個（ヘッダー sticky + フォーム末尾）、削除確認モーダル追加
3. **role / aria-label / placeholder / button text の変更**: テキスト regex で当てている locator (`getByRole('button', { name: /保存/i })`) が新テキストと不一致になる
4. **ルート変更**: `routes.ts` の path 変更や `Link to=...` の URL 変更で、`page.goto('/dashboard/upload')` 系の navigation テストが壊れる
5. **フォーム構造変化**: `form` 属性、`required`、`type` 変更でテストの fill / submit 順序が不整合になる
6. **取りこぼしパターン**: 同じ画面を複数テストが叩いている場合、片方が `.first()` 等で修正済み、他方が未対応のまま残っているケースを検出する。具体的には: フロント変更で「同じ画面のセレクタ修正が必要」と判断したら、その画面を叩く **すべての** E2E テストに同じ修正が入っているかを横断確認する。横断確認の grep 起点戦略: (a) 画面の URL path（例: `/dashboard/artworks/detail/.+/edit`）、(b) 画面固有要素のテキスト（例: `保存`, `編集`）、(c) スクリーン名コメント、の3軸を併用する
7. **既に防御済みの能動判定**: 検出した E2E テストが **既に `.first()` / 個別 locator / 専用 helper 等で防御済み** の場合は、そのまま列挙しつつ「現状: 既に防御済み」を明示する。新規修正不要でも「同じ画面の他テスト全件で防御が揃っているか」の横断確認は必須（取りこぼしの逆パターン: 全部対応済みであることの能動的確認）

#### agent への明示的指示

- staging-*.spec.ts ファイル（`@staging` タグ付き、約10ファイル）は **production 近接** で爆発時のコストが最大なので最優先
- 全 50 ファイル弱のテストファイルを丸読みする必要はない。**フロント変更が触る画面を起点に、関連テストだけ深読み** する
- 確信度を `high / medium / low` で必ず付ける（高すぎる感度は人間の判断負荷を増やす）
- 検出 0 件の場合も、「能動的に 0 件と判定した」ことを明示せよ（沈黙 ≠ 検出 0 件）

### Phase 3: 結果整形

agent の出力を以下のフォーマットに整形して返す:

```
## E2E Scenario Impact Check Result

- フロント変更検出: yes
- 影響を受ける可能性のあるテスト: N 件
- 検出箇所:
  - e2e/playwright/tests/<file>:<line>
    - confidence: high / medium / low
    - 現状: [未対応 / 既に防御済み / 部分的に防御済み]
    - 関連するフロント変更: <ファイル名>
    - 理由: <なぜ壊れそうか>
    - 推奨対応: <未対応なら具体的修正案 (.first() 追記など)、既に防御済みなら「対応不要（既に防御済み）」と明示>
- 取りこぼしチェック:
  - 同じ画面を叩く他テストの整合性: [OK / N 箇所要確認]
  - 詳細: <他テスト名と現状（未対応/既に防御済み）>
- false positive 抑制 OK: <検出 0 件、または検出件数 N でも全件『既に防御済み』のときに「yes、能動的に判定」と明示>

→ [Pass / Findings need review]
```

検出 0 件の場合:

```
## E2E Scenario Impact Check Result

- フロント変更検出: yes
- 影響を受ける可能性のあるテスト: 0 件
- false positive 抑制 OK: yes、能動的に 0 件判定

→ Pass
```

## 落とし穴 (Red flags)

| 思考の罠 | 実態 |
|---|---|
| 「CI lint が通ったから E2E も大丈夫」 | NO。biome / tsc は構文/型しか見ない。E2E の locator / role 整合性は実行時にしか検出されない |
| 「ローカル E2E が CI で走るから安心」 | NO。CI で動く E2E は **staging のみ**。ローカル E2E は人間が手動実行する前提だが、実態として走らされていない場合が多い |
| 「フロント変更の名前空間が小さいから skip して良い」 | NO。1行の `<button>` → `<Link>` 変更で複数 E2E が壊れる。サイズではなく **画面要素の意味変化** で判断する |
| 「E2E テストファイル名が違うから関係ない」 | NO。`artwork-crud.spec.ts` と `staging-paid-user.spec.ts` は別ファイルだが同じ画面（作品編集）を叩く。**画面単位** で関連テストを横断検出する |
| 「agent 任せだと誤検知が多い」 | confidence: low の指摘は人間判断で吸収する。本 skill の価値は「true positive を見逃さないこと」優先で、false positive は許容する |
| 「staging テストはステージング環境がないと検証できない」 | 本 skill は **静的検査のみ**。実行はしない。「壊れる可能性が高い」を伝えれば、Hidari/Claude が修正判断を下せる |
| 「dev-workflow:pre-merge-quality-gate が動くから本 skill 単独呼び出しは不要」 | 大きいフロント変更（a11y 一括リファクタなど）の **完了直後** に単独で呼ぶと、PR を出す前に E2E 取りこぼしを検出できる。dev-workflow:pre-merge-quality-gate より早い shift-left ポイント |

## 関連

- `dev-workflow:pre-merge-quality-gate` (sibling skill): Phase 1 の4つ目並列タスクとして本 skill を呼ぶ
- `playwright-test` (sibling skill): E2E テストの書き方リファレンス。本 skill が修正案を出す際の根拠として参照
- `ast-grep-practice` (sibling skill): 一部のパターン（role 変更、button → Link 化）は ast-grep ルール化候補。本 skill で繰り返し検出される高頻度パターンが固まったら、機械検査に降ろせるか検討する

## 関連 CLAUDE.md ルール

本 skill は、プロジェクトの CLAUDE.md が次のような規定を持つときに噛み合う。

- ローカル E2E を 1 ブラウザに絞り、クロスブラウザは CI のステージングシナリオテストで担保する
- マージ前にローカルマシン上で E2E を全パスさせることを求める
- プロダクションコードの変更時に、ユニット / 統合 / E2E だけでなく E2E シナリオテストへの影響調査まで求める

本 skill はこの「E2E シナリオテストへの影響調査」を **構造化された agent ワークフロー** で実行する。
規定が無いプロジェクトでも動くが、staging E2E が唯一の自動検出網になっている構成でいちばん効く。

## 既知の改善余地（empirical-prompt-tuning Iter 2 で発見、実運用で踏んだら対応）

- 「現状」フィールドの値域 `[未対応 / 既に防御済み / 部分的に防御済み]` に **「無関係（別画面ヒット）」** を追加するか、無関係ヒットは検出箇所自体に含めない方針を明確化する。現状は subagent が裁量で値域外を追加することがある
- 早期 skip 条件を「`frontend/` と `e2e/` 以外しか変更が無い」のように第三ケース（backend / database / docs のみ変更）も明示する。現状は「`frontend/` 配下に diff 無し」「`e2e/` のみ変更」の 2 条件のみ列挙で、subagent が前者を流用解釈している
- `.first()` が **DOM 順依存** である点（ヘッダー sticky と form 末尾の DOM 順が逆転すると意図が壊れる）を hint 1 / 2 で明示する。現状は subagent が補足で気付いて警告している
