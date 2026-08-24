---
name: commit-and-pr-message
description: Tirith フックが有効な環境で git / gh コマンドに日本語の散文を渡すときに使う。対象はコミット本文 / PR 本文 / PR コメント / レビュー / Issue 本文 / リリースノート / 注釈付きタグ。日本語を Bash コマンド文字列に載せると confusable_text ルールでコマンドごとブロックされるため、本文は例外なく Write でファイルに書き `-F` / `--body-file` / `--notes-file` で渡す。「コミットして」「PR を作って」「PR 本文を書いて」「コメントして」「リリース切って」と指示された時、および該当コマンドを実行する直前に使う。
---

# Commit and PR Message Authoring

git / gh に渡す日本語の散文は Bash コマンド文字列に載せない。Write でファイルに書き、file 系フラグで渡す。

## 目的

- **判定の機会を消す**: 「この文字列は安全か」を書くたびに考えさせる設計は実績として繰り返し失敗している。ファイル経由なら判定の対象にならない
- **日本語を自然な文体で書く**: 文字種の自己規制が要らなくなる
- **書式の canonical を一元化する**: prefix 一覧や `Closes` 書式をここに写経せず所在だけを示す

## いつ使うか

### 必ず使う

下表の左側を打ちそうになったとき。ユーザーが「コミットして」「PR を作って」「PR 本文を書いて」「コメントして」「リリース切って」と指示したときも同じ。

| 面 | 使わない | 使う |
|---|---|---|
| コミット本文 | `git commit -m` | `git commit -F <file>` |
| PR 本文 | `gh pr create --body` / `gh pr edit --body` | 同コマンドの `--body-file <file>` |
| PR コメント | `gh pr comment --body` | `gh pr comment --body-file <file>` |
| PR レビュー | `gh pr review --body` | `gh pr review --body-file <file>` |
| Issue 本文 | `gh issue create --body` | `gh issue create --body-file <file>` |
| リリースノート | `gh release create --notes` | `gh release create --notes-file <file>` |
| 注釈付きタグ | `git tag -a -m` | `git tag -a -F <file>` |

境界は「どのコマンドか」ではなく「日本語の散文が Bash コマンド文字列を通るか」である。表に無いコマンドでも同じ条件なら同じ扱いにする。

### 使わない

- 本文を伴わない操作 (`git commit --amend --no-edit` / `git revert --no-edit` など)
- 他者が書いた本文を読むだけのとき

## 前提: なぜファイル経由なのか

PreToolUse フックの Tirith は **Bash ツールの command 文字列だけ** を検査する。Write と Edit は素通しする。したがって本文をファイルに書けば、本文が何を含んでいても検査対象にならない。

発火するルールは `confusable_text` (severity HIGH)。tirith の policy 側では緩められない (severity / action の override も paranoia も upgrade only)。最終判定を下しているのは `~/.claude/hooks/tirith-check.py` なので機構的には wrapper 側で握り潰せるが、**それは homoglyph 難読化への防御を全コマンドで殺す変更なので採らない。**

### 発火条件

判定は 2 系統ある。tirith 0.3.3 の実装 (`terminal.rs` / `confusables.txt`) と実測で確認した。

- **confusable 文字**: 句点 `。` / 全角ピリオド `．` / 半角句点 `｡` / 全角ラテン / キリル・ギリシャの lookalike。**同一 word 内に ASCII 英字があると発火**する。日本語の文字と半角空白は word 境界になる
- **数学英数字** (U+1D400-U+1D7FF): **前後 16 byte 以内に ASCII 英字があると発火**する。空白を挟んでも守られない

読点 `、` / 全角括弧 `（）` / 全角カンマ / 全角コロン / 全角感嘆符 / 全角ソリダス / 波ダッシュ / 全角数字は集合に含まれず発火しない。全集合の canonical は tirith の `confusables.txt`。

| 入力 | 判定 |
|---|---|
| `修正した。nabla のテスト` | BLOCK (同一 word 内に ASCII 小文字) |
| `テストが緑。CI も通った` | BLOCK (同一 word 内に ASCII 大文字) |
| `修正した。テストも追加` | ALLOW (カタカナが word 境界) |
| `nabla を修正した。` | ALLOW (行末) |
| `修正した。 nabla` | ALLOW (半角空白が word 境界) |

日本語の技術文書では「句点の直後に識別子」が頻出するので、この条件は実質的に頻繁に踏む。条件を覚えて避けるのではなく、ファイル経由にして判定ごと消す。

条件は tirith のバージョンに結合する。再測は `tirith check --json --non-interactive --shell posix -- '<文字列>'` の exit code (0=allow / 1=block) で、プローブを Write でファイルに書いて `bash <file>` から回す (Bash に渡すコマンド文字列を ASCII に保つため)。

## ワークフロー

どの面でも形は同じ 3 手である。**(1) 本文を Write でファイルに書く (2) file 系フラグで渡す (3) 載ったことを確認する。** 以下は代表として commit と PR を詳しく書くが、他の面も同じ 3 手を踏む。

コマンド例は cwd がリポジトリルート (`git rev-parse --show-toplevel`) である前提で相対パスを使っている。別の場所から打つならパスを絶対にすること。

### Phase A: コミット

**A.1 メッセージを書く**

`<repo>/.cache/commit-<slug>.txt` に **Write ツールで**書く。`cat <<EOF` は中身が Bash コマンド文字列を通るので使えない。

`<repo>` はコミット対象のリポジトリのルートで、`git rev-parse --show-toplevel` で解決する (置き場のルール自体はグローバル CLAUDE.md が持つ)。`<slug>` は対象を一意に指す短い識別子にする (`commit-readme.txt` / `comment-pr4.md` / `notes-v0.7.0.md` のように、ファイル名だけで何の本文か読めること)。

名前が決め打ちなので、前回の実行で作ったファイルが残っていることがある。Write する前に存在を確認し、自分が作った残骸なら上書きしてよい。別物なら slug を変える。

本文は自然な日本語でよい。prefix と `(wip)` の一覧は `~/.claude/references/git-workflow.md` が canonical で、グローバル CLAUDE.md の「コミットメッセージのプレフィックスと本文の渡し方」節がそこを名指ししている。末尾に harness が指示するセッショントレーラを `Claude-Session: <URL>` の形式で置く。

**A.2 コミットして着地を確認する**

```bash
git commit -F .cache/commit-<slug>.txt && git log -1 --format=%B
```

`--cleanup` は渡さない。既定の `whitespace` は行頭 `#` を保持するので、行頭に置いた Issue 参照が落ちない。

### Phase B: push

手順はグローバル CLAUDE.md の push ルールが持つ。

### Phase C: PR とその他の本文

**C.1 本文を書く**

`<repo>/.cache/pr-<slug>.md` に Write ツールで書く。入れるものは変更の要約と背景、検証結果、`Closes` リンク (書式は `dev-workflow:in-repo-issue` が canonical)、末尾に harness が指示するセッション URL。

**フッタはコミットと非対称である。** PR 本文は URL の裸置きで `Claude-Session:` キーを付けない。

**C.2 作成して本文が載ったことを確認する**

```bash
gh pr create --body-file .cache/pr-<slug>.md --title "<1 行>" && gh pr view --json url,title,body
```

`--assignee` や `--base` などのフラグと、作成後の PR 確認手順はプロジェクトの CLAUDE.md が定める。プロジェクトごとに異なるのでここには書かない。

**`--title` だけはファイルで渡せない。** これが本 skill の方針を貫けない唯一の箇所である。タイトルは 1 行の名詞句で書き、句点を入れないこと。

**C.3 その他の面**

同じ形で渡す。置き場は `<repo>/.cache/` で、ファイル名は面がわかるものにする。

| 渡す | 載ったことを確認する |
|---|---|
| `gh pr edit <num> --body-file .cache/pr-<slug>.md` | `gh pr view <num> --json body` |
| `gh pr comment <num> --body-file .cache/comment-<slug>.md` | `gh pr view <num> --json comments` |
| `gh pr review <num> --approve --body-file .cache/review-<slug>.md` | `gh pr view <num> --json reviews` |
| `gh issue create --title "<1 行>" --body-file .cache/issue-<slug>.md` | `gh issue view <num> --json body` |
| `gh release create <tag> --title "<1 行>" --notes-file .cache/notes-<slug>.md` | `gh release view <tag> --json body` |
| `git tag -a <tag> -F .cache/tag-<slug>.txt` | `git tag -n99 -l <tag>` |

`--title` と同じく `gh issue create --title` / `git tag <tag>` もインラインなので句点を入れない。

フッタの既定は面ごとに次のとおり。harness がこれと異なる指示を出したらそちらが優先。

| 面 | フッタ |
|---|---|
| コミット本文 | `Claude-Session: <URL>` (キー付き) |
| PR 本文 | URL の裸置き (キーなし) |
| コメント / レビュー / Issue / リリースノート / タグ | 付けない |

## 落とし穴 (Red flags)

| 思考の罠 | 実態 |
|---|---|
| 「短いから `-m` でいい」「ブロックされたら半角に直せばいい」 | どちらも例外判定を続ける道。その判定こそが繰り返し失敗した箇所なので、無条件にファイル経由にする |
| 「heredoc でファイルに書けば同じこと」 | heredoc の中身は Bash コマンド文字列を通る。ブロックされるのはコマンド全体なのでファイルすら作られない |
| 「`--body-file` を使ったから全部安全」 | `--title` はインライン。タイトルに句点を入れると弾かれる |
| 「読点や全角括弧も避けるべき」 | 発火しない。過剰な自己規制で日本語が不自然になる |
| 「コミットと PR に同じフッタを付ける」 | 非対称。コミットは `Claude-Session: <URL>`、PR 本文は URL の裸置き |
| 「コミットと PR だけ気をつければいい」 | `gh pr comment` / `gh release create` / `git tag -a` も同じ機構で踏む。境界は面ではなく「日本語の散文が Bash を通るか」 |

## 関連

- `dev-workflow:in-repo-issue` (sibling skill): PR タイトル書式と `Closes` リンク書式の canonical。in-repo Issue は同 skill が Write と Edit でファイルを直接扱うので Bash コマンド文字列を通らない (GitHub Issues を使うリポジトリで `gh issue create` を打つ場合は本 skill の対象)
- `dev-workflow:pre-merge-quality-gate` (sibling skill): `gh pr create` と `gh pr merge` の直前に通すゲート。本 skill は何をどう書いて渡すかだけを持ち、いつ実行してよいかは持たない
- `dev-workflow:git-branch-switcher` (sibling skill): 作業前のブランチ選択

本 skill が持つのは本文の作成と受け渡しだけである。その前後の工程 (リリースの版更新やタグ付けの順序、Issue の起票フロー、レビューの approve 判断など) は各プロジェクトの CLAUDE.md と上記の兄弟 skill が持つ。本 skill が沈黙している工程は「不要」ではなく「他所の担当」と読むこと。

## 関連 CLAUDE.md ルール

- `~/.claude/CLAUDE.md`: push ルール / 一時ファイルの置き場 / prefix 一覧の所在
- `~/.claude/references/git-workflow.md`: prefix と `(wip)` の一覧 (CLAUDE.md が指す先)
- 各プロジェクトの `CLAUDE.md`: `gh pr create` のフラグと PR 確認手順
