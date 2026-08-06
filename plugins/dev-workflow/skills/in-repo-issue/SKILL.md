---
name: in-repo-issue
description: リポジトリ内 Markdown (`docs/issues/<NNN>_<title>/issue.md`) で Issue を起票・更新・自動クローズ・reopen する。「Issue を立てて」「起票して」「作って」「閉じて」と指示された時、 PR マージ後 `Closes #NNN` / `Fixes #NNN` を検出した時 (dev-workflow:pre-merge-quality-gate Phase 5 から自動呼び出し)、 親 Issue の子全 closed を検出した時に起動。 frontmatter は status のみ、 完了判定は本文「## タスク」チェックリスト全 [x] で行う。
---

# In-Repo Issue Management

## 用語と構造

| 用語 | 実体 |
|---|---|
| Issue | `docs/issues/<NNN>_<title>/issue.md` |
| 補助資料 | 同ディレクトリ内の任意ファイル。 命名はプロジェクト規約に従う (`dev-workflow:issue-scoped-artifacts` 採用時は `<NNN>-spec.md` / `<NNN>-plan.md`) |
| 番号 NNN | プロジェクト内連番。 max+1 (closed/ 配下も含む) |
| クローズ済み | `docs/issues/closed/` (`status: closed` の保管庫。 通常は編集しない。 例外: Phase F reopen) |
| テンプレ | `docs/issues/templates/issue.md` (コピー元) |

## 初期化 (新規プロジェクト)

`docs/issues/` が無いプロジェクトでは:

```bash
mkdir -p docs/issues/templates
cp ${CLAUDE_SKILL_DIR}/templates/issue.md docs/issues/templates/
```

採番は 1 から開始。 templates のセットアップは「初期化」として起票とは別コミットに切る (例: `chore(issues): in-repo-issue テンプレートを追加`)。

## いつ使うか

### 必ず使う

- 新規 Issue 起票指示 (「Issue を立てて」「起票して」「作って」)
- 既存 Issue の status 更新・クローズ・PR リンク追加
- 親 Issue を子 Issue に分割するとき
- dev-workflow:pre-merge-quality-gate Phase 5 から自動呼び出し (PR マージ後 `Closes #NNN` / `Fixes #NNN` を検出した時)
- ユーザーから「マージしたよ」「閉じて」と指示された時、 また次セッション開始時に main の CI 成功 + `Closes #NNN` を検出した時 (フォールバック)

### 使わない

- typo 修正のような軽微変更
- 1-2 ステップの即時修正で進捗追跡不要なもの
- 調査・質問応答のみのセッション

## frontmatter スキーマ

必須 (1 項目):

```yaml
status: open  # open / in_progress / closed
```

必要時のみ追記 (任意):

```yaml
parent: 5         # 子 Issue のときのみ
children: [6, 7]  # 親 Issue のときのみ
```

これ以外は持たない。

## ライフサイクル

### Phase A: 起票

A.1 採番 (10 進数強制で 0 prefix の 8 進解釈エラーを防ぐ。 未マージブランチの起票済み番号も拾うため全ブランチ横断で最大値を取る):

```bash
MAX=$( { find docs/issues -maxdepth 2 -type d -regex '.*/[0-9]+_.*' -not -path '*/templates/*'
         for b in $(git for-each-ref --format='%(refname:short)' refs/heads refs/remotes); do
           git ls-tree -d --name-only "$b" docs/issues/ 2>/dev/null
         done
       } | sed -E 's|.*/([0-9]+)_.*|\1|' | grep -E '^[0-9]+$' | sort -n | tail -1)
NEXT=$((10#${MAX:-0} + 1))
```

`find` だけでは現在チェックアウト中のツリーしか見えず、 未マージブランチで起票済みの番号と衝突する (実例: main で採番して #13 を得たが、 #13 は未マージの PR ブランチに既に存在した)。 `git ls-tree` の出力も `docs/issues/<NNN>_<title>` 形式なので find の結果とそのまま混ぜられる。 `grep -E '^[0-9]+$'` は sed が変換しなかった行 (`docs/issues` 自身など) を落とすためのもの。

A.2 ディレクトリ名 `<NNN>_<title>` を作る。 タイトルから FS-safe 文字列を作るサニタイズ規則 (3 項目):

- 削除する文字: `/` `\` `:` `?` `*` `<` `>` `|` `"` (FS-unsafe な 9 文字のみ)
- 長さ制限: Unicode コードポイント 50 文字以内
- 末尾の半角/全角空白・`_`・`-`・`.` を除去

サニタイズ規則の境界 (規則を読む前に必ず確認):

- **サニタイズ対象はディレクトリ名タイトル部のみ**。 本文 H1 は元タイトル (`:` を含む conventional commits prefix と記号類) を**そのまま保持**する
- **conventional commits prefix の除外境界**: `feat:` 等の prefix とその**直後の半角空白 1 つ**を丸ごと除外してからサニタイズ規則を適用 (例: `feat: タイトル` → ディレクトリ名タイトル部 `タイトル`)
- **シェル特殊文字を含むディレクトリ名**: `#` `!` `$` `` ` `` や半角空白を含む場合、 シェルコマンドでは**常にダブルクォート**で囲う (例: `git add "docs/issues/6_Issue #5 を子に分割 browser 側/issue.md"`)。 Markdown リンクで `#` を含むパスを書くときは URL エンコードせず素のパスで OK (git / 多くの renderer が解決する)

それ以外 (日本語、 英数字、 半角空白、 全角空白、 括弧、 句読点、 ハイフン等) は全部そのまま保持。 結果が空文字列なら `untitled`。

```bash
DIR="docs/issues/${NEXT}_<sanitized-title>"
mkdir -p "${DIR}"
cp docs/issues/templates/issue.md "${DIR}/issue.md"
```

例: 入力 `feat: 作品詳細ページに SNS シェアボタンを追加` → ディレクトリ `1_作品詳細ページに SNS シェアボタンを追加/`、 本文 H1 `# feat: 作品詳細ページに SNS シェアボタンを追加`

A.3 issue.md を Edit:

- H1 の `<タイトル>` を実タイトル (prefix 含む) に置換
- `## タスク` セクションにチェックボックスを書き込む

A.4 コミット: `docs(issues): Issue #NNN <title> を起票` (`<title>` は本文 H1 と同じ元タイトル = conventional commits prefix を含むフルタイトル。 サニタイズ後のディレクトリ名タイトル部ではない)

A.5 分割 (親 → 子 Issue):「Plan が複数必要」「PR が PR-A, PR-B に分かれる」場合、 子 Issue を順次起票して子 frontmatter に `parent: 5`、 親 frontmatter に `children: [6, 7]` を追記。 親 issue.md の `## タスク` を `- [ ] [Issue #6: <子タイトル>](../6_<子タイトル>/issue.md)` 形式に置き換えて子の俯瞰用にする (Phase E の close 判定はディレクトリ位置のみで、 親 `## タスク` のチェック状態は判定に使わない)。 コミット: `docs(issues): Issue #NNN を子 Issue (#NN1, #NN2) に分割`

### Phase B: 更新

- `open → in_progress` 遷移時のみ frontmatter `status` を書き換え
- タスク進捗は本文 `- [ ]` → `- [x]`
- 親子化したら `parent: N` / `children: [N, M]` を必要時に追記
- コミット: `docs(issues): Issue #NNN を <変更内容> に更新`

### クローズ経路: feature PR 同梱を優先 (main 直 push を避ける)

Phase C/D は既定で post-merge に main へ直接 commit して close する。 だが **main への直 push を禁じるプロジェクト** (default branch への push を PR で迂回する方針、 CI-on-PR や auto-mode classifier のガードがある環境) では、 この直 push が方針に反する。

その場合は feature PR にクローズを同梱する:

- feature ブランチ内で Phase D.1〜D.3 と同じ操作 (issue.md を `status: closed` 化 + `git mv` で `closed/` へ移動 + 相対リンクの補正) をコミットに含める。
- PR マージで issue が closed のまま main に入る。 post-merge の別コミット/push は不要で、 CI 追加 run も出ない (別 docs PR だと ci が PR+マージで 2 run 走りコスト増)。
- マージ後に Phase C が走っても、 対象が `closed/` 配下にあるため C.3 の既存分岐で「既に closed」→ no-op になり破綻しない。
- 親 Issue の Phase E 伝播 close も、 親を閉じる PR に同梱するか、 直 push が許されない環境では別 PR で行う。

プロジェクトが main 直 push を許すなら従来どおり post-merge 直 push (Phase C→D) でよい。

### Phase C: 自動クローズ

C.1 マージ PR 本文から `Closes` / `Fixes` の対象 Issue 番号を抽出 (本 skill 規定の `Closes [Issue #NNN](...)` 形式と GitHub 素形式 `Closes #NNN` の両方に対応):

```bash
gh pr view <PR> --json body --jq '.body' \
  | grep -oE '(Closes|Fixes) \[?(Issue )?#[0-9]+' \
  | grep -oE '[0-9]+'
```

抽出 0 件なら Phase C 終了 (close 対象なし)。 正規表現は `Closes`/`Fixes` 直後の `[Issue #` 角括弧形式 (`## PR / コミット規約` 節が規定する正式形式) と素の `#` 形式の双方を拾う。 `(Closes|Fixes) #[0-9]+` に戻すと規定形式 (`Closes [Issue #NNN](...)`) を取りこぼし、 gate Phase 5 の自動クローズがサイレントに 0 件化するので注意。

C.2 起動契機 2 (gate 未経由フォールバック) の場合のみ、 main の最新 CI 成功を確認:

```bash
CI=$(gh run list --branch main --limit 1 --json status,conclusion --jq '.[0]')
echo "$CI" | jq -e '.status == "completed" and .conclusion == "success"' >/dev/null
```

CI 未成功なら Phase C 終了 (Phase D 実行を保留)。 gate Phase 5 経由の場合は gate が既に検証済みなのでこのチェックをスキップ。

C.3 該当 Issue の `## タスク` チェックリスト判定:

```bash
ISSUE_PATH=$(ls docs/issues/${NNN}_*/issue.md docs/issues/closed/${NNN}_*/issue.md 2>/dev/null | head -1)
[ -z "$ISSUE_PATH" ] && { echo "Issue #${NNN} が見つからない"; exit 0; }
case "$ISSUE_PATH" in docs/issues/closed/*) echo "既に closed"; exit 0 ;; esac
has_task_section=$(grep -c '^## タスク' "$ISSUE_PATH")
unchecked=$(grep -c '^- \[ \]' "$ISSUE_PATH")
```

分岐:

- `has_task_section == 0`: 自動 close 対象外。 「Issue #NNN にチェックリスト未定義、 手動 close 推奨」とログ出力のみ
- `unchecked > 0`: status を `in_progress` に更新するだけ、 close しない。 「Issue #NNN にまだ未完タスクがある」とログ
- `unchecked == 0`: Phase D を実行

### Phase D: クローズ実行

順序は **D.1 (Edit) → D.2 (git mv) → D.3 (リンク補正) → D.4 (add + commit)** で固定 (理由は D.4 参照)。

D.1 frontmatter `status` を `closed` に書き換え (Edit ツールで `status: open` を `status: closed` に置換)。

D.2 `git mv` で `closed/` 配下に移動 (ディレクトリ名は維持):

```bash
mkdir -p docs/issues/closed
git mv "docs/issues/${NNN}_<title>" "docs/issues/closed/${NNN}_<title>"
```

D.3 移動でパスの深さが 1 段変わるので、 壊れる相対リンクを補正する。 方向は 3 つあり、 3 つとも見ること:

| 方向 | 移動前 | 移動後 |
|---|---|---|
| 外部 → 移動対象 | `../<NNN>_<title>/issue.md` | `../closed/<NNN>_<title>/issue.md` |
| 移動対象 → 外部 | `../<MMM>_<title>/issue.md` | `../../<MMM>_<title>/issue.md` |
| 移動対象 → 移動先の兄弟 | `../closed/<MMM>_<title>/issue.md` | `../<MMM>_<title>/issue.md` |

```bash
grep -rn "${NNN}_" --include='*.md' docs/                    # 外部から移動対象への参照
grep -n '](\.\./' "docs/issues/closed/${NNN}_<title>"/*.md   # 移動対象から出る参照
```

3 つ目は移動元を見ても出てこない。 移動先の階層にだけ依存する変化なので、 `closed/` へ入った後の位置で数え直すこと。 相対リンク検査を持つプロジェクトはコミット時に検出するが、 持たないプロジェクトでは沈黙したまま壊れる。

D.4 frontmatter 編集と git mv の rename とリンク補正を 1 コミットにまとめる。 `git mv` が新パスへ stage するのは **HEAD の内容** なので、 D.1 の Edit は unstaged のまま残る (`git status` が `RM` を返す)。 明示 stage を省くと `status: open` のまま `closed/` 配下に入り、 検索手順が頼る `^status: open$` の grep が壊れる。 他の変更を巻き込まないためにも **新パスの明示 stage** を行う:

```bash
git add "docs/issues/closed/${NNN}_<title>/issue.md"
git commit -F .cache/commit-close.txt
```

`(PR #M)` の `M` はマージされた PR 番号 (コミット文言の経路別形式は `## PR / コミット規約` 節参照)。メッセージ本文の渡し方は `dev-workflow:commit-and-pr-message` の Phase A に従う。`<title>` に日本語が入る経路ではインラインの `-m` がブロックされうる。

D.5 Phase D 完了後、 close した Issue の `parent` を確認して Phase E に進む。

### Phase E: 親伝播 (子全 closed なら親 close を提案)

E.1 / E.2 close した Issue の `parent`、 親の `children`、 各子の所在を順に読む (ダブルクォート内のグロブは展開されないため一度パスを変数化する):

```bash
CLOSED_PATH=$(ls docs/issues/closed/${NNN}_*/issue.md 2>/dev/null | head -1)
PARENT=$(grep -E '^parent:' "$CLOSED_PATH" | sed -E 's/^parent: *//')
[ -z "$PARENT" ] && exit 0
PARENT_PATH=$(ls docs/issues/${PARENT}_*/issue.md 2>/dev/null | head -1)
CHILDREN=$(grep -E '^children:' "$PARENT_PATH" | sed -E 's/.*\[(.*)\].*/\1/; s/,/ /g')
for child in $CHILDREN; do ls docs/issues/${child}_*/issue.md 2>/dev/null && echo "child ${child} active"; done
```

active が 1 件でもあれば Phase E 終了。 全件 closed なら親も close 可能。 ディレクトリ位置と frontmatter の不整合は Phase D の手順違反なので、 E ではディレクトリ位置だけで判定する。

E.3 AskUserQuestion で「Issue #N の子 Issue が全て closed です。 親 Issue #N も close しますか?」と提案。

E.4 承認 → 親に対して Phase D を実行 → 親に祖父母がいれば E を再帰実行。 拒否 → 何もしない。 親伝播 close のコミットメッセージは `## PR / コミット規約` 節参照。

### Phase F: Reopen

クローズ後の再オープン。 `closed → in_progress` への巻き戻しはこの経路でのみ許可される。

F.1 frontmatter `status: closed → in_progress`。

F.2 `git mv` で `closed/` から戻す:

```bash
git mv "docs/issues/closed/${NNN}_<title>" "docs/issues/${NNN}_<title>"
```

F.3 1 コミット: `docs(issues): Issue #NNN を reopen`。 リンク補正は D.3 の 3 方向をそのまま逆向きに適用する。 stage 規約は D.4 と同じ (明示パスで他差分を巻き込まない)。

## 検索手順

```bash
# 全 open Issue (closed/ と templates/ を除外)
grep -lr '^status: open$' docs/issues/[0-9]*/issue.md

# 親 Issue 一覧 (children を持つ)
grep -lr '^children:' docs/issues/[0-9]*/issue.md

# 番号で開く (active / closed 両方)
ls docs/issues/5_*/issue.md docs/issues/closed/5_*/issue.md 2>/dev/null | head -1

# closed 一覧
ls docs/issues/closed/
```

## PR / コミット規約

PR タイトル: `<prefix>(<scope>): <subject> (Issue #NNN)`

PR 本文に Issue 本体への相対リンクと `Closes #NNN` を必ず書く (`Closes` キーワードが Phase C.1 のトリガーになる):

```markdown
Closes [Issue #NNN](../../docs/issues/NNN_title/issue.md)
```

これがない PR は close されない。 GitHub Issues 併用時は `Closes #NNN` が GitHub の自動クローズ構文と衝突する (番号が別系列だと意図しないクローズ)。 採番衝突回避ルールをプロジェクト CLAUDE.md に明示すること。

コミットメッセージ形式 (Phase 別):

| Phase | 形式 |
|---|---|
| A 起票 | `docs(issues): Issue #NNN <title> を起票` |
| B 更新 | `docs(issues): Issue #NNN を <変更内容> に更新` |
| A.5 分割 | `docs(issues): Issue #NNN を子 Issue (#NN1, #NN2) に分割` |
| D PR マージ起点 close | `docs(issues): Issue #NNN をクローズ (PR #M)` |
| E 親伝播起点 close | `docs(issues): Issue #NNN をクローズ (子 Issue #NN1, #NN2 完了に伴う伝播)` |
| F Reopen | `docs(issues): Issue #NNN を reopen` |

## Red flags

| 思考の罠 | 実態 |
|---|---|
| 「frontmatter は適当でいい」 | NG。 `^status: open$` の grep が生命線。 クォート/コロンのフォーマット崩壊で grep が壊れる |
| 「クローズ時 status だけ更新」 | NG。 `status: closed` への遷移と Phase D.2 の git mv (`closed/` 配下へ) は常にセット |
| 「`## タスク` 不在でも自動 close したい」 | NG。 チェックリスト不在 Issue は手動 close。 自動 close はタスク全消化を判定する仕組みで、 起点が無いと暴走する |
| 「reopen で番号を採り直す」 | NG。 reopen は元番号を維持したまま `closed/` から戻す。 過去コミット内 `Issue #NNN` 参照がブレる |
| 「採番に bash 算術で `$((007 + 1))`」 | NG。 0 prefix は 8 進数解釈で `value too great for base` エラー。 `$((10#${MAX} + 1))` で 10 進強制 |
| 「採番は `find docs/issues` だけで足りる」 | NG。 現在チェックアウト中のツリーしか見ず、 未マージブランチで起票済みの番号と衝突する。 A.1 のとおり `git for-each-ref` + `git ls-tree` を併用して全ブランチ横断で採る |

## 関連

- `dev-workflow:git-branch-switcher`: Issue 起票後、 必ずブランチ作成
- `dev-workflow:pre-merge-quality-gate`: Phase 5 から本 skill の Phase C を呼ぶ
- プロジェクト `CLAUDE.md`: `docs/issues/` 配置のオーバーライドが必要な場合のみ
