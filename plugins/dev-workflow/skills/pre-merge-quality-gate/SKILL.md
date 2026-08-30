---
name: pre-merge-quality-gate
description: PR を main にマージする直前 (gh pr merge を実行する前) または PR を作成する直前 (gh pr create の前) に必ず使う。simplify / feature-dev:code-reviewer / boy-scout-sweep / dev-workflow:e2e-scenario-impact-check を並列で通し、CI が捕捉できない a11y バグ・UX 問題・デッドコード・rot するコメント・タスク参照・E2E シナリオテストの取りこぼしを fix してからマージする。「CI 通り次第マージ」「マージして」と指示された時も必ず通すこと。ボーイスカウトルール (CLAUDE.md MUST) も skill 内で構造化済みで、毎回の追加質問は不要。
---

# Pre-Merge Quality Gate

CI 通過 ≠ マージ可。CI が通ってもこのスキルを通してから `gh pr merge` / `gh pr create` を実行する。

## 目的

- **CI が見逃す品質問題を捕捉する**: a11y バグ (aria-* の重複参照など)、UX 問題 (要素幅・ホバー視認性など)、デッドコード、テストの冗長性、不要コメント
- **後追い修正の摩擦をゼロにする**: マージ後に気づくと follow-up PR で 2 倍の手間 (新ブランチ / 新 PR / 再レビュー / 再マージ)
- **CLAUDE.md MUST ルールを構造的に守る**: 認知負荷で飛ばないようにチェックリスト化する

## いつ使うか

### 必ず使う

- ユーザーが「マージして」「PR 出して」「CI 通り次第マージ」「PR 作成して」と指示したとき
- `gh pr merge` を実行する直前
- `gh pr create` を実行する直前
- 自分の判断で「これでマージ準備 OK」と思ったとき

### 使わない

- WIP コミット (`feat(wip):` 等) を push するだけのとき
- ユーザーが明示的に「simplify と review はスキップして」と指示したとき (理由を確認する)
- リバート / 緊急 hotfix で時間が許さないとき (理由をユーザーに明示してから判断)

## ワークフロー

### Phase 0: コンテキスト収集 (並列前の準備)

後続の Phase が使う材料をここで揃える。Phase 1 の並列レビューに渡す差分情報と、Phase 2 以降の判断に要る事実の両方が対象。`Bash` で:

```bash
git diff <base>..HEAD --name-only   # 変更ファイル一覧
git diff <base>..HEAD --stat        # 変更概要
gh pr view <num> --json title,body  # PR メタ情報
```

`<base>` は通常 `main`、`<num>` は対象 PR 番号。

Issue のクローズをこの PR へ同梱するかの判定に要る事実 (main への直 push を禁じているか) もここで集める。作成経路とマージ経路のどちらから入っても集める。マージ直前でも対象ブランチにはまだコミットを積めるので同梱は選択肢のままで、経路で条件を付けると Phase 2 の判断が事実の無いまま黙って飛ぶ。判定の手順は `dev-workflow:in-repo-issue` の「クローズ経路: feature PR 同梱を優先」節が持つので、写さずにそちらを読む。

### Phase 1: 並列でレビュー実行

simplify / code-reviewer / boy-scout-sweep / dev-workflow:e2e-scenario-impact-check は独立しているので **同じメッセージ内で 4 つ並列に launch する**。

1. **`Skill` tool で `simplify` を起動**
   - args: 変更概要 + 変更ファイルのリスト
   - 変更ファイルは `git diff <base>..HEAD --name-only` で抽出
   - `simplify` 自体が reuse / simplification / efficiency / altitude の 4 並列内部実行を行う

2. **`Agent` tool で `feature-dev:code-reviewer` を起動** (subagent_type=feature-dev:code-reviewer)
   - prompt: 変更概要 + 全 diff の取得方法 (`git diff <base>..HEAD`) + レビュー観点 (CLAUDE.md 由来: バグ / セキュリティ / a11y / 関数型 / DRY / テスト網羅性) + 「confidence-based filtering で本当に修正したい指摘のみ返してほしい」

3. **`Agent` tool で `general-purpose` を起動 (Boy Scout Sweep)**
   - prompt: 「**触ったファイル** (今回 PR で diff のあるファイル) を読み、CLAUDE.md ボーイスカウトルールに該当する箇所のみ抽出。
     - **抽出対象は『触ったファイル』のみ**。 同 directory の隣接ファイル (`<sibling>.tsx` など) は **抽出対象ではなく**、 触ったファイル内の表記揺れ検出のための **コンテキスト参照** として読む (隣接ファイル自体に boy scout 候補があっても、それは別 PR で扱う / 別 issue 提案として返す)。
     - **必ず検出する対象**:
       - タスク参照コメント (`// Issue #N: ...`, `// PR #N`, `// Closes #N` のような GitHub の番号記法に加え、 in-repo Issue の識別子を書いたコメントも同じ) — PR description にあるべき情報、コードでは rot する。 参照先が既に復元できないものは推測で番号や識別子を割り当て直さず、 参照を伴わない記述へ書き換える
       - 履歴説明コメント (`// 旧実装は ...`, `// 元々は ...`, `// 以前 ...`) — git log で追える、 rot する
       - 識別子で表現済みの WHAT 説明コメント (関数名・型名で意図が明らかなのに WHAT を書いてあるもの)
       - 隣接 panel / コンポーネントとの表記揺れで **触ったファイル側に正当な WHY が無い** もの (`<div>` vs `<p>` のような同等用途タグの不統一など)。 触ったファイル側に「機能差を生む WHY コメント」が明示されていれば設計差として温存する。
     - **抽出しない対象**:
       - PR スコープを大きく超える大規模リファクタ (これは follow-up issue 提案にする)
       - WHY を説明する正当なコメント (設計判断、非自明な制約、library 仕様の捕捉、規約逸脱の正当化など)
       - 機能的に異なる兄弟ファイルの設計差 (例: empty state を持つ panel と持たない panel の差は意図的)
     - **出力ルール** (Boy Scout Sweep の自己レポート出力。 skill 全体の最終アグリゲート出力 = Phase 4 の `## Pre-Merge Quality Gate Result` 節は別物で、 そちらは出力フォーマット節に従う):
       - 検出 1 件以上の場合: `<file>:<line> → <削除 or 修正案>` の箇条書き。Confidence 80+ のみ
       - **検出 0 件の場合は『検出なし、 false positive 抑制 OK』と明示** (検出漏れではなく能動的に 0 件と判定したことの証明)」
   - 観点の根拠: CLAUDE.md「WHAT は識別子で示す、タスク参照や rot するコメントは書かない」/「ボーイスカウトルール: 触ったファイルや関連箇所の小さな改善は今回 PR で行う」

4. **`Skill` tool で `dev-workflow:e2e-scenario-impact-check` を起動**
   - args: 変更概要 + base branch (通常 `main`)
   - skill 内部でフロント変更検出 → 無ければ早期 skip、有れば agent 経由で E2E への影響を静的検査
   - 検出対象: `getByRole` / `getByLabel` 整合性、画面構造変化（保存ボタン2個など strict mode violation 系）、`<button>` ↔ `<Link>` 化、role / aria-label 変更、ルート変更、同じ画面を別経路で叩く別テストの取りこぼし
   - staging E2E は唯一の自動 E2E 検出網（ローカル E2E は CI で動かない）であるため、shift-left で爆発前に検出する価値が高い

5. 4 つの結果を **アグリゲート** する

並列で投げると 30〜90 秒で 4 つの結果が揃う。逐次にしない。

### Phase 2: 指摘事項の判断

Phase 1 の指摘と gate 自身の判断を、次の軸で分類する:

| 分類 | 対応 |
|---|---|
| **Critical** (a11y / security / data loss / 機能バグ) | 必ず修正 |
| **Important** (UX / 明確なバグ / 仕様逸脱) | 修正 |
| **Boy Scout** (rot コメント / タスク参照 / 履歴説明 / 識別子で表現済み WHAT / 隣接ファイルとの軽微な表記揺れ) | 触ったファイル内なら今回 PR で修正、 PR スコープを大きく超えるなら別 issue 起票。CLAUDE.md MUST ルール |
| **Nice-to-have** (簡素化 / dead code 除去 / テスト fixture) | スコープに余裕があれば修正、なければ follow-up issue を起票 |
| **False positive / 過剰抽象化** | スキップ。短い理由を明記 |
| **クローズ同梱** (この PR が Issue のタスクを全消化するか) | 同梱するなら Phase 3 で `dev-workflow:in-repo-issue` の「クローズ経路: feature PR 同梱を優先」節が規定する手順を適用する。判定は Phase 0 で集めた事実に基づく |

CLAUDE.md「3 lines vs premature abstraction」原則を守る: 3 箇所程度の重複は定数化を強制しない。

#### 観点どうしが正面衝突したときの裁定

並列レビューは観点ごとに独立して走るので、同じ箇所に正反対の結論が返ることがある。これは並列化の産物であって失敗ではない。片方だけを読んで従うと単一レビュアーと同じ盲点に戻るため、必ず両方の根拠を読んでから裁定し、採らなかった側は理由を記録する。

裁定は印象や confidence の大小ではなく **その変更で検証力が増えるか減るか** で決める。既定は「pin が 1 本減るなら採らない」。

実例: `simplification` が「全行が同じ値のタプルは平坦化しろ (YAGNI)」、`altitude` が「将来の分岐余地として残せ」と正反対を返した。どちらも confidence 80 台で印象では決められなかったが、平坦化すると分類表と実装の食い違いを突合していたテストがその能力を失う (pin を 1 本失う) と分かって決着した。

`simplify` の指摘には挙動を変える提案が混ざる。マージ直前のブランチでは、挙動を変える提案は既定で別 issue へ回す。`simplify` は品質の改善であって仕様変更の場ではない。

### Phase 3: 修正反映 + 再検証

1. 修正を適用 (Edit / Write)
2. 同梱すると判断したなら `dev-workflow:in-repo-issue` の「クローズ経路: feature PR 同梱を優先」節が規定する手順を feature ブランチで適用する
   - Phase D を直接名指ししないのは、同梱経路の射程が Phase D 全体とは一致せず、狭める側の canonical が同梱節だから。ここで Phase D を名指しすると gate の宣言だけが canonical より広くなる
   - 「コミットへ含める」ではなく「適用する」と書くのは、同梱節の射程がコミットを作る操作だけではないため。次の Phase を起動する手順のようにコミットへ入る変更を作らないものが混ざるので、動詞をコミットに縛ると gate の宣言だけが canonical より狭くなる (広くする側と同じだけ危険で、狭い側は「やらなくてよい」に読める)
   - Phase 4 (執行) ではなくここへ置くのは、同梱が issue.md の編集と `git mv` と相対リンクの補正というファイル変更を伴うため。Phase 4 に置くと、この Phase の再検証を通らない変更が PR へ入る
   - マージ経路から入ったときは、同梱で作ったコミットをリモートの対象ブランチへ push してから Phase 4 へ進む。`gh pr merge` がマージするのはリモート側の内容なので、手元に積んだだけだと同梱が黙って落ちる
   - その push は必須チェックを pending へ戻す。待たずに Phase 4 へ進むと `gh pr merge` が拒否されるので、`gh pr checks <num> --watch` で緑を見てから進む。同梱をマージ経路で行うとこの再走のぶん CI が余計に走る (作成経路には無い費用で、同梱節が書く費用の比較は作成経路の話)
3. `make format` (該当エリアのみ)
4. `make ci-<area>` で全テスト pass を確認
   - area 名はプロジェクトの `Makefile` に依存する。`ci-frontend` / `ci-backend` のようにデプロイ単位で切られていることが多い
   - 不明なら `make help` か `Makefile` を grep して該当 target を探す
5. UI 変更なら **chrome-devtools-mcp で実ブラウザ動作確認** (CLAUDE.md MUST: フロントエンド変更は実ブラウザで確認)
   - 主要ツール: `mcp__plugin_chrome-devtools-mcp_chrome-devtools__navigate_page`, `take_snapshot`, `take_screenshot`, `list_console_messages`, `click`, `hover`, `fill`
   - フロントエンド再起動後は `isolatedContext` パラメータを必ず使う (CLAUDE.md MUST)
6. 必要なら追加コミット (同じブランチに積むか、既にマージ済みなら follow-up ブランチ)

### Phase 4: マージ / PR 作成実行

ここで初めて `gh pr merge <num> --squash --delete-branch` または `gh pr create`。

`gh pr create` する場合、PR 本文の書き方と渡し方は `dev-workflow:commit-and-pr-message` の Phase C に従う (従わないと Tirith にブロックされコマンドごと失敗する)。

### Phase 5: Issue クローズ処理 (gh pr merge 実行時のみ)

`gh pr merge` 成功後、 `dev-workflow:in-repo-issue` skill の Phase C (自動クローズ) を起動する。 起動可否を gate 側で判定しないこと。 対象識別子の抽出も「抽出 0 件なら close 対象なしで終了」の判定も C.1 が持っており、 同じ条件を gate 側へ写すと識別子の記法が変わったときに写した側だけが取り残されて起動が静かに止まる。 起動時は呼び出し元が gate であることを明示し、 dev-workflow:in-repo-issue 側 C.2 のフォールバック CI 確認をスキップさせる。 詳細手順は dev-workflow:in-repo-issue SKILL.md 参照。

## 落とし穴 (Red flags)

| 思考の罠 | 実態 |
|---|---|
| 「ユーザーが『CI 通り次第マージ』と言った = simplify はスキップ可」 | NO。CI は code review の代替ではない。「CI 通り次第マージ」は CI を確認してマージする手順の指示であり、品質ゲートの省略許可ではない。指示の文字通りの解釈で MUST ルールを飛ばさない |
| 「小さな変更だから simplify は不要」 | 小さい変更ほど a11y や境界条件のバグが隠れやすい。例: 1 行の `aria-describedby` 追加でスクリーンリーダー読み上げが二重化 |
| 「マージ後に simplify すれば良い」 | follow-up PR の摩擦は大きい (新ブランチ / 新 PR / 再レビュー / 再マージ + Reviewer の手間) |
| 「CLAUDE.md に書いてあるから覚えてる」 | タスク完了モードでは MUST ルールが認知から落ちやすい。skill 化で構造的に防ぐ |
| 「simplify は表面的な改善だけ」 | simplify は 4 並列で reuse / simplification / efficiency / altitude を見る。dead code、leaky abstraction、hot path bloat、redundant state に加え、特殊ケースの積み重ねや集合の二重管理といった構造の問題も検出される |
| 「テストが pass してるから OK」 | テスト pass は仕様通り動くことの証明だが、a11y バグ・UX 問題はテストで検出されにくい |
| 「ボーイスカウト確認はユーザーに毎回聞けば良い」 | NG。CLAUDE.md MUST ルール「ボーイスカウト」を skill 内で構造化するためにこの skill がある。Phase 1 の Boy Scout Sweep を毎回必ず動かす |
| 「Boy Scout Sweep は触ったファイルだけで充分」 | 触ったファイルの **同 directory 隣接ファイル** も対象。empty state パターンの不統一など、隣接ファイルとの一貫性は touched files だけ見ても気付けない |

## 出力フォーマット

タスク完了の前に、自分への確認として以下を出力する:

```
## Pre-Merge Quality Gate Result

- simplify: [completed / skipped (reason)]
- feature-dev:code-reviewer: [completed / skipped (reason)]
- boy-scout-sweep: [completed / skipped (reason)]
- dev-workflow:e2e-scenario-impact-check: [completed / skipped (reason: e.g., no frontend changes)]
- Findings applied: [N items (incl. M boy scout, K e2e impact) / no findings]
- Re-verification: [make ci-* pass / manual QA done / N/A]

→ Merge OK
```

「Merge OK」を出力していない状態で `gh pr merge` を呼ばない。
ユーザーに「ボーイスカウト適用余地は？」と追加で聞かれた場合は **本 skill が既にカバーしているはず** なので、漏れた候補があれば skill の Phase 1 (Boy Scout Sweep) のプロンプト改善対象として記録する。

## 関連

- `dev-workflow:commit-and-pr-message` (sibling skill): コミット本文と PR 本文の書き方と渡し方。Phase 4 で `gh pr create` する直前に使う
- `dev-workflow:in-repo-issue` (sibling skill): Issue のライフサイクル。同梱の判定 (Phase 0 で事実収集、Phase 2 で判断、Phase 3 で適用) と、マージ後の自動クローズ (Phase 5) がここを参照する
- `simplify` (built-in skill): 4 並列レビューと修正適用
- `feature-dev:code-reviewer` (subagent): バグ・logic・security の confidence-based filtering
- `dev-workflow:e2e-scenario-impact-check` (sibling skill): フロント変更が E2E シナリオテストを壊す可能性を静的検出。Phase 1 の 4 つ目並列タスク
- `verification-before-completion` (superpowers): 完了主張前の検証
- `requesting-code-review` (superpowers): レビュー依頼の作法

## 関連 CLAUDE.md ルール

- `~/.claude/CLAUDE.md`: 「Simplify、レビュー、ボーイスカウトルール全てを適用後の Push」
- 各プロジェクトの `CLAUDE.md` に「PRのマージ前には必ず /simplify と feature-dev:code-reviewer」がある場合はそれが優先
