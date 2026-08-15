---
name: in-repo-issue
description: リポジトリ内 Markdown (`docs/issues/<ID>_<title>/issue.md`) で Issue を起票・更新・自動クローズ・reopen する。「Issue を立てて」「起票して」「作って」「閉じて」と指示された時、 PR マージ後 `Closes <ID>` / `Fixes <ID>` を検出した時 (dev-workflow:pre-merge-quality-gate Phase 5 から自動呼び出し)、 親 Issue の子全 closed を検出した時に起動。 識別子 `<ID>` は同梱の `scripts/issue-id.py` が採番し、 GitHub の番号記法との混同も同スクリプトが検出する。 frontmatter は status のみ、 完了判定は本文「## タスク」チェックリスト全 [x] で行う。
---

# In-Repo Issue Management

## 用語と構造

| 用語 | 実体 |
|---|---|
| 識別子 | `scripts/issue-id.py --next` が発行する文字列。 形式と採番規則の canonical は同スクリプト。 このファイルは形も接頭辞も再掲せず、 以降 `<ID>` と書く |
| Issue | `docs/issues/<ID>_<title>/issue.md` |
| 補助資料 | 同ディレクトリ内の任意ファイル。 命名はプロジェクト規約に従う (`dev-workflow:issue-scoped-artifacts` 採用時は `<ID>-spec.md` / `<ID>-plan.md`) |
| クローズ済み | `docs/issues/closed/` (`status: closed` の保管庫。 通常は編集しない。 例外: Phase F reopen) |
| テンプレ | `docs/issues/templates/issue.md` (コピー元) |
| GitHub の `#<数字>` | GitHub の Issue / PR だけを指す番号空間。 in-repo Issue には使わない。 混入は `issue-id.py --check` / `--check-text` が検出する |

## 初期化 (新規プロジェクト)

`docs/issues/` が無いプロジェクトでは:

```bash
mkdir -p docs/issues/templates scripts
cp "${CLAUDE_SKILL_DIR}/templates/issue.md" docs/issues/templates/
cp "${CLAUDE_SKILL_DIR}/scripts/issue-id.py" scripts/
```

`issue-id.py` をプロジェクトへ配るのは、 記法の検査を pre-commit / CI から呼ぶ経路が
`${CLAUDE_SKILL_DIR}` を持たないため (git hook も CI runner も skill を読み込まない)。 起票時の
採番は skill 同梱のものを直接呼ぶので、 配布先の copy は検査経路のためだけにある。 取り付けるか
どうかはプロジェクトの判断で、 配っただけでは検査は走らない。

置き場は `docs/issues/` の**外**にすること。 `docs/issues/` 直下のディレクトリは `templates/` と
`closed/` を除いて Issue ディレクトリとみなされ、 検査スクリプト自身が命名違反として報告される
(実測)。

templates と `issue-id.py` のセットアップは「初期化」として起票とは別コミットに切る
(例: `chore(issues): in-repo-issue のテンプレートと採番スクリプトを追加`)。

## いつ使うか

### 必ず使う

- 新規 Issue 起票指示 (「Issue を立てて」「起票して」「作って」)
- 既存 Issue の status 更新・クローズ・PR リンク追加
- 親 Issue を子 Issue に分割するとき
- dev-workflow:pre-merge-quality-gate Phase 5 から自動呼び出し (PR マージ後 `Closes <ID>` / `Fixes <ID>` を検出した時)
- ユーザーから「マージしたよ」「閉じて」と指示された時、 また次セッション開始時に main の CI 成功 + `Closes <ID>` を検出した時 (フォールバック)

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
parent: <ID>            # 子 Issue のときのみ (親の識別子)
children: [<ID>, <ID>]  # 親 Issue のときのみ (子の識別子)
```

これ以外は持たない。

## ライフサイクル

### Phase A: 起票

A.0 **内容の重複を先に検索する (採番より前)**。 A.1 が保証するのは識別子の一意性だけで、 話題の一意性は誰も見ていない。 識別子さえ衝突しなければ、 同じ話題の Issue が既に open でも静かに通る。

タイトルから主題語を 2〜3 語抜き、 `docs/issues/` を closed 込みで横断検索する:

```bash
grep -rli '<主題語>' docs/issues --include='issue.md'
```

主題語は「ツール名・機構名・症状語」から採る (例: pnpm 移行なら `pnpm` / `packageManager` / `lockfile`)。 タイトルの語をそのまま使わないこと。 既存 Issue が別の言い回しで立っていると引っかからない。

ヒットしたら、 新規起票ではなく**既存への追記**を既定とする。 新規に立てるのは、 扱う対象が実際に別だと言えるときだけ。

**既存 Issue と方針が食い違う場合は、 どちらかに片寄せせず両方へ相互リンクし、 変わったのが「内容」か「着手順序」かを明記する** (理由: 識別子が別なので機械的には衝突せず、 両方 open のまま残る。 着手した人がどちらを読むかで結論が変わる。 実際に「低リスクなルールから順に削る」と「移行まで触らない」が別 Issue に併存した)。

分割して新規を立てるときは、 既存側にも新規への参照を入れる。 片方向リンクだと既存だけを読んだ人に新しい決定が届かない。

A.1 採番:

```bash
NEXT=$(python3 "${CLAUDE_SKILL_DIR}/scripts/issue-id.py" --next)
```

`--next` は現ツリーだけでなく全 ref を走査する。 現ツリーしか見ないと、 未マージブランチで起票済みの識別子と衝突する (実例: main で採番した番号が、 未マージの PR ブランチに既に存在した)。 重複を検出したときは識別子を印字せず非 0 で終了するので `${NEXT}` が空になる (実測)。 空なら起票を止め、 stderr に印字された重複を先に解消すること。 何をどう数えるかの canonical は `issue-id.py`。

A.2 ディレクトリ名 `${NEXT}_<sanitized-title>` を作る。 タイトルから FS-safe 文字列を作るサニタイズ規則 (3 項目):

- 削除する文字: `/` `\` `:` `?` `*` `<` `>` `|` `"` (FS-unsafe な 9 文字のみ)
- 長さ制限: Unicode コードポイント 50 文字以内
- 末尾の半角/全角空白・`_`・`-`・`.` を除去

サニタイズ規則の境界 (規則を読む前に必ず確認):

- **サニタイズ対象はディレクトリ名タイトル部のみ**。 識別子の側は `--next` の出力をそのまま前置する。 本文 H1 は元タイトル (`:` を含む conventional commits prefix と記号類) を**そのまま保持**する
- **conventional commits prefix の除外境界**: `feat:` 等の prefix とその**直後の半角空白 1 つ**を丸ごと除外してからサニタイズ規則を適用 (例: `feat: タイトル` → ディレクトリ名タイトル部 `タイトル`)
- **シェル特殊文字を含むディレクトリ名**: `#` `!` や半角空白を含む場合、 シェルコマンドでは**常にダブルクォート**で囲う (例: `git add "docs/issues/${NEXT}_ラベルに #tag を含む/issue.md"`)。 ただし `$` と `` ` `` はダブルクォートの中でも展開されるので、 これらを含むタイトルはシングルクォートで囲うか `\$` へエスケープする (実測: `"... $VAR ..."` は変数の値へ置き換わったディレクトリを作る)。 Markdown リンクで `#` を含むパスを書くときは URL エンコードせず素のパスで OK (git / 多くの renderer が解決する)

それ以外 (日本語、 英数字、 半角空白、 全角空白、 括弧、 句読点、 ハイフン等) は全部そのまま保持。 結果が空文字列なら `untitled`。

```bash
DIR="docs/issues/${NEXT}_<sanitized-title>"
mkdir -p "${DIR}"
cp docs/issues/templates/issue.md "${DIR}/issue.md"
```

例: 入力 `feat: 作品詳細ページに SNS シェアボタンを追加` → ディレクトリ `${NEXT}_作品詳細ページに SNS シェアボタンを追加/`、 本文 H1 `# feat: 作品詳細ページに SNS シェアボタンを追加`

A.3 issue.md を Edit:

- H1 の `<タイトル>` を実タイトル (prefix 含む) に置換
- `## タスク` セクションにチェックボックスを書き込む

A.4 コミット: `docs(issues): <ID> <title> を起票` (`<title>` は本文 H1 と同じ元タイトル = conventional commits prefix を含むフルタイトル。 サニタイズ後のディレクトリ名タイトル部ではない)

A.5 分割 (親 → 子 Issue):「Plan が複数必要」「PR が PR-A, PR-B に分かれる」場合、 子 Issue を順次起票して子 frontmatter に `parent: <親の ID>`、 親 frontmatter に `children: [<子の ID>, <子の ID>]` を追記。 親 issue.md の `## タスク` を `- [ ] [<子の ID>: <子タイトル>](../<子の ID>_<子タイトル>/issue.md)` 形式に置き換えて子の俯瞰用にする (Phase E の close 判定はディレクトリ位置のみで、 親 `## タスク` のチェック状態は判定に使わない)。 コミット: `docs(issues): <ID> を子 Issue (<子の ID>, <子の ID>) に分割`

### Phase B: 更新

- `open → in_progress` 遷移時のみ frontmatter `status` を書き換え
- タスク進捗は本文 `- [ ]` → `- [x]`
- 親子化したら `parent: <ID>` / `children: [<ID>, <ID>]` を必要時に追記
- コミット: `docs(issues): <ID> を <変更内容> に更新`

### クローズ経路: feature PR 同梱を優先 (main 直 push を避ける)

Phase C/D は既定で post-merge に main へ直接 commit して close する。 だが **main への直 push を禁じるプロジェクト** (default branch への push を PR で迂回する方針、 CI-on-PR や auto-mode classifier のガードがある環境) では、 この直 push が方針に反する。

その場合は feature PR にクローズを同梱する:

- feature ブランチ内で Phase D.1〜D.3 と同じ操作 (issue.md を `status: closed` 化 + `git mv` で `closed/` へ移動 + 相対リンクの補正) をコミットに含める。
- PR マージで issue が closed のまま main に入る。 post-merge の別コミット/push は不要で、 CI 追加 run も出ない (別 docs PR だと ci が PR+マージで 2 run 走りコスト増)。
- マージ後に Phase C が走っても、 対象が `closed/` 配下にあるため C.3 の既存分岐で「既に closed」→ no-op になり破綻しない。
- 親 Issue の Phase E 伝播 close も、 親を閉じる PR に同梱するか、 直 push が許されない環境では別 PR で行う。

プロジェクトが main 直 push を許すなら従来どおり post-merge 直 push (Phase C→D) でよい。

### Phase C: 自動クローズ

C.1 マージ PR の title と body から `Closes` / `Fixes` の対象識別子を抽出 (本 skill 規定の `Closes [<ID>](...)` 形式と、 リンクを伴わない `Closes <ID>` 形式の両方に対応):

```bash
gh pr view <PR> --json title,body --jq '.title, .body' \
  | grep -oE '(Closes|Fixes) \[?[^] ]*[0-9]+' \
  | sed -E 's/^(Closes|Fixes) \[?//' \
  | sort -u
```

抽出 0 件なら Phase C 終了 (close 対象なし)。

- **title も読むのは body への記入漏れに対する保険**。 `Closes` / `Fixes` のキーワードは依然必須で、 PR タイトル規約の `(<ID>)` だけを書いた PR は close 対象にならない (キーワードが無いため)
- **パターンは識別子の形そのものを持たない**。 `Closes` / `Fixes` の直後から「`]` と半角空白以外が続いて数字で終わる」塊を切り出すだけなので、 識別子の形が変わっても壊れない。 代わりに `Fixes 2 つある` のような散文も拾うが、 実在しない識別子は C.3 の「見つからない」分岐で no-op になる (実測)
- `(Closes|Fixes) #[0-9]+` のような GitHub 素形式へ戻さないこと。 in-repo Issue の識別子は数字記法を使わないので抽出が静かに 0 件化し、 自動クローズが止まる

C.2 起動契機 2 (gate 未経由フォールバック) の場合のみ、 main の最新 CI 成功を確認:

```bash
CI=$(gh run list --branch main --limit 1 --json status,conclusion --jq '.[0]')
echo "$CI" | jq -e '.status == "completed" and .conclusion == "success"' >/dev/null
```

CI 未成功なら Phase C 終了 (Phase D 実行を保留)。 gate Phase 5 経由の場合は gate が既に検証済みなのでこのチェックをスキップ。

C.3 該当 Issue の `## タスク` チェックリスト判定 (`${ID}` は C.1 が抽出した識別子):

```bash
ISSUE_PATH=$(find docs/issues -mindepth 2 -maxdepth 3 -path "*/${ID}_*/issue.md" | head -1)
[ -z "$ISSUE_PATH" ] && { echo "${ID} が見つからない"; exit 0; }
case "$ISSUE_PATH" in docs/issues/closed/*) echo "既に closed"; exit 0 ;; esac
has_task_section=$(grep -c '^## タスク' "$ISSUE_PATH")
unchecked=$(grep -c '^- \[ \]' "$ISSUE_PATH")
```

`ls docs/issues/${ID}_*/issue.md docs/issues/closed/${ID}_*/issue.md` のようにグロブを並べる形は使わないこと。 マッチしないグロブがあったときの挙動がシェルの状態に依存する。 zsh の既定 (`nomatch`) では**片方がマッチしないだけでコマンド全体が中止され、 マッチした側の出力も出ない**ため、 実在する Issue が「見つからない」に化けて Phase C が黙って no-op になる。 `nonomatch` を設定したシェルと bash では出る (3 者を実測して確認)。 `find` は設定に依存せず、 0 件なら空を返す。

分岐:

- `has_task_section == 0`: 自動 close 対象外。 「`<ID>` にチェックリスト未定義、 手動 close 推奨」とログ出力のみ
- `unchecked > 0`: status を `in_progress` に更新するだけ、 close しない。 「`<ID>` にまだ未完タスクがある」とログ
- `unchecked == 0`: Phase D を実行

### Phase D: クローズ実行

順序は **D.1 (Edit) → D.2 (git mv) → D.3 (リンク補正) → D.4 (add + commit)** で固定 (理由は D.4 参照)。

D.1 frontmatter `status` を `closed` に書き換え (Edit ツールで `status: open` を `status: closed` に置換)。

D.2 `git mv` で `closed/` 配下に移動 (ディレクトリ名は識別子もタイトル部も変えずに維持):

```bash
mkdir -p docs/issues/closed
git mv "docs/issues/${ID}_<title>" "docs/issues/closed/${ID}_<title>"
```

D.3 移動でパスの深さが 1 段変わるので、 壊れる相対リンクを補正する。 方向は 3 つあり、 3 つとも見ること:

| 方向 | 移動前 | 移動後 |
|---|---|---|
| 外部 → 移動対象 | `../<ID>_<title>/issue.md` | `../closed/<ID>_<title>/issue.md` |
| 移動対象 → 外部 | `../<他の ID>_<title>/issue.md` | `../../<他の ID>_<title>/issue.md` |
| 移動対象 → 移動先の兄弟 | `../closed/<他の ID>_<title>/issue.md` | `../<他の ID>_<title>/issue.md` |

```bash
grep -rn "${ID}_" --include='*.md' docs/                       # 外部から移動対象への参照
find docs/issues/closed -mindepth 2 -maxdepth 2 -path "*/${ID}_*/*.md" \
  -exec grep -nE '\]\(<?\.\./' {} +                            # 移動対象から出る参照
```

2 つ目のパターンで `<` を任意にしているのは、 パスに半角空白や日本語を含むと Markdown リンクが山括弧形式 (`](<../...>)`) で書かれるため。 `](\.\./` だけで数えると山括弧形式が丸ごと落ちる (両形式を並べて実測)。 しかも欠落は「補正不要」という妥当に見える 0 件で返るので、 出力を見ても気づけない。

3 つ目の方向は移動元を見ても出てこない。 移動先の階層にだけ依存する変化なので、 `closed/` へ入った後の位置で数え直すこと。 相対リンク検査を持つプロジェクトはコミット時に検出するが、 持たないプロジェクトでは沈黙したまま壊れる。

D.4 frontmatter 編集と git mv の rename とリンク補正を 1 コミットにまとめる。 `git mv` が新パスへ stage するのは **HEAD の内容** なので、 D.1 の Edit は unstaged のまま残る (`git status` が `RM` を返す)。 明示 stage を省くと `status: open` のまま `closed/` 配下に入り、 検索手順が頼る `^status: open$` の grep が壊れる。 他の変更を巻き込まないためにも **新パスの明示 stage** を行う:

```bash
git add "docs/issues/closed/${ID}_<title>/issue.md"
git commit -F .cache/commit-close.txt
```

コミット文言の経路別形式は `## PR / コミット規約` 節参照。 文言に含める `PR #<M>` の `<M>` は GitHub の PR 番号で、 これは in-repo Issue ではなく GitHub のオブジェクトなので数字記法のまま書く。メッセージ本文の渡し方は `dev-workflow:commit-and-pr-message` の Phase A に従う。`<title>` に日本語が入る経路ではインラインの `-m` がブロックされうる。

D.5 Phase D 完了後、 close した Issue の `parent` を確認して Phase E に進む。

### Phase E: 親伝播 (子全 closed なら親 close を提案)

E.1 / E.2 close した Issue の `parent`、 親の `children`、 各子の所在を順に読む:

```bash
CLOSED_PATH=$(find docs/issues/closed -mindepth 2 -maxdepth 2 -path "*/${ID}_*/issue.md" | head -1)
PARENT=$(grep -E '^parent:' "$CLOSED_PATH" | sed -E 's/^parent: *//')
[ -z "$PARENT" ] && exit 0
PARENT_PATH=$(find docs/issues -mindepth 2 -maxdepth 3 -path "*/${PARENT}_*/issue.md" | head -1)
grep -E '^children:' "$PARENT_PATH" | sed -E 's/.*\[(.*)\].*/\1/' | tr ',' '\n' | tr -d ' ' \
  | while IFS= read -r child; do
      [ -n "$child" ] && find docs/issues -mindepth 2 -maxdepth 2 -path "*/${child}_*/issue.md"
    done
```

最後のパイプラインが 1 行でも出せば active な子が残っている → Phase E 終了。 出力が空なら全件 closed で親も close 可能。 ディレクトリ位置と frontmatter の不整合は Phase D の手順違反なので、 E ではディレクトリ位置だけで判定する (`-maxdepth 2` は `closed/` 配下を含まないので、 出るのは active な子だけ)。

`CHILDREN=$(...)` へ入れて `for child in $CHILDREN` で回す形は使わないこと。 zsh は変数展開を単語分割しないので**子が 1 つの塊として 1 回だけ回り、 全件を取りこぼす** (bash では分割される。 両方を実測)。 取りこぼした側は「active な子が 1 件も無い」に見えるので、 まだ open な子がいる親を close する提案が出る。 分割はシェルに任せず `tr` で行に割って読む。

E.3 AskUserQuestion で「`<親の ID>` の子 Issue が全て closed です。 `<親の ID>` も close しますか?」と提案。

E.4 承認 → 親に対して Phase D を実行 → 親に祖父母がいれば E を再帰実行。 拒否 → 何もしない。 親伝播 close のコミットメッセージは `## PR / コミット規約` 節参照。

### Phase F: Reopen

クローズ後の再オープン。 `closed → in_progress` への巻き戻しはこの経路でのみ許可される。

F.1 frontmatter `status: closed → in_progress`。

F.2 `git mv` で `closed/` から戻す:

```bash
git mv "docs/issues/closed/${ID}_<title>" "docs/issues/${ID}_<title>"
```

F.3 1 コミット: `docs(issues): <ID> を reopen`。 リンク補正は D.3 の 3 方向をそのまま逆向きに適用する。 stage 規約は D.4 と同じ (明示パスで他差分を巻き込まない)。

## 検索手順

```bash
# 全 open Issue (closed/ と templates/ を除外)
find docs/issues -mindepth 2 -maxdepth 2 -name issue.md -not -path '*/templates/*' \
  -exec grep -l '^status: open$' {} +

# 親 Issue 一覧 (children を持つ)
find docs/issues -mindepth 2 -maxdepth 2 -name issue.md -not -path '*/templates/*' \
  -exec grep -l '^children:' {} +

# 識別子で開く (active / closed 両方)
find docs/issues -mindepth 2 -maxdepth 3 -path "*/${ID}_*/issue.md"

# closed 一覧
find docs/issues/closed -mindepth 1 -maxdepth 1 -type d
```

`-exec ... {} +` は該当ファイルが 0 件なら `grep` を起動しない (実測)。 `xargs` へ繋ぐ形は空入力での起動有無が実装で分かれる (BSD 版は起動せず、 GNU 版は最低 1 回起動すると BSD の `xargs(1)` が `-r` の項で述べている。 起動されると `grep` が標準入力を待つ) ので、 実装に依存しない `-exec` の形にしてある。

## PR / コミット規約

PR タイトル: `<prefix>(<scope>): <subject> (<ID>)`

PR 本文に Issue 本体への相対リンクと `Closes <ID>` を必ず書く (`Closes` キーワードが Phase C.1 のトリガーになる):

```markdown
Closes [<ID>](../../docs/issues/<ID>_<title>/issue.md)
```

これがない PR は close されない。

識別子が数字記法でないため、 GitHub Issues を併用するプロジェクトでもこの行が GitHub の自動クローズ構文として解釈されることはない (GitHub のクローズキーワードは `#<数字>` / URL / `<owner>/<repo>#<数字>` のいずれかを要求する)。 番号空間の衝突は「プロジェクトごとに規約を書いて避ける」ものではなく、 記法が交わらないことで構造的に起きない。 手書きで数字記法が混入した場合の検出は `issue-id.py` の検査入口が担う (プロジェクトへ配る手順は「初期化」節。 hook / CI への取り付けはプロジェクト側の判断なので、 配っただけでは検出は走らない)。

コミットメッセージ形式 (Phase 別):

| Phase | 形式 |
|---|---|
| A 起票 | `docs(issues): <ID> <title> を起票` |
| B 更新 | `docs(issues): <ID> を <変更内容> に更新` |
| A.5 分割 | `docs(issues): <ID> を子 Issue (<子の ID>, <子の ID>) に分割` |
| D PR マージ起点 close | `docs(issues): <ID> をクローズ (PR #<M>)` |
| E 親伝播起点 close | `docs(issues): <ID> をクローズ (子 Issue <子の ID>, <子の ID> 完了に伴う伝播)` |
| F Reopen | `docs(issues): <ID> を reopen` |

## Red flags

| 思考の罠 | 実態 |
|---|---|
| 「frontmatter は適当でいい」 | NG。 `^status: open$` の grep が生命線。 クォート/コロンのフォーマット崩壊で grep が壊れる |
| 「クローズ時 status だけ更新」 | NG。 `status: closed` への遷移と Phase D.2 の git mv (`closed/` 配下へ) は常にセット |
| 「`## タスク` 不在でも自動 close したい」 | NG。 チェックリスト不在 Issue は手動 close。 自動 close はタスク全消化を判定する仕組みで、 起点が無いと暴走する |
| 「in-repo Issue を `#<数字>` で参照する」 | NG。 数字記法は GitHub の Issue / PR の番号空間で、 同じ番号の GitHub オブジェクトと文脈でしか区別できない。 GitHub 側の autolink も発火する。 in-repo Issue は `--next` が発行する識別子だけで指す。 混入は `issue-id.py --check` / `--check-text` が検出する |
| 「reopen で識別子を採り直す」 | NG。 reopen は元の識別子を維持したまま `closed/` から戻す。 過去コミット内の参照がブレる |
| 「Issue ディレクトリのタイトル部を後から直す」 | NG。 `--next` は同じ番号で別名のディレクトリを別 Issue の重複と見て採番を止めるので、 以降どの Issue も起票できなくなる。 識別子もタイトル部も保存すること (詳細は `issue-id.py` の「既知の限界」) |
| 「採番は自分で書いた find / grep で足りる」 | NG。 現ツリーだけを見ると未マージブランチで起票済みの識別子と衝突する。 `git ls-tree` を使う場合も既定は非再帰で `closed/` 配下を列挙しない。 どちらも「エラー」ではなく小さい最大値として返るので出力を見ても気づけない。 採番は `issue-id.py --next` に任せる |
| 「識別子の一意性を確認したから起票してよい」 | NG。 識別子が衝突しないことは、 同じ話題の Issue が無いことを意味しない。 A.0 の内容検索を先に通す。 実際に pnpm 移行と eslint-suppressions で既存 Issue と重複した Issue を 2 本立てた |

## 関連

- `scripts/issue-id.py` (本 skill 同梱): 識別子の採番 (`--next`) と記法の検査 (`--check` / `--check-text`)。 形式と採番規則の canonical
- `dev-workflow:git-branch-switcher`: Issue 起票後、 必ずブランチ作成
- `dev-workflow:pre-merge-quality-gate`: Phase 5 から本 skill の Phase C を呼ぶ
- プロジェクト `CLAUDE.md`: `docs/issues/` 配置のオーバーライドが必要な場合のみ
