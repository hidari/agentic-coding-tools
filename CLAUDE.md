# CLAUDE.md

## このリポジトリ

Claude Code のための skill と plugin を集めた PUBLIC リポジトリ。個人の開発ワークフローを
そのまま置き、誰でも参考にできる状態にしておくことを目的にしている。配布は apm 経由。

パッケージの構造と命名の規約は README の「構造の規約」節を読むこと。実体は
`scripts/check-package-shape.py` の docstring が canonical で、README も CLAUDE.md も
規約の内容を再掲しない。

## [MUST] PUBLIC であることに由来する制約

このリポジトリは公開されている。private リポジトリの作業中に得た知見を持ち込むときは、
持ち込んでよい形に落としてから書くこと。

- 絶対パス (`/Users/<name>/...`) を書かない。開発機の配置は公開されるべきものではなく、
  配布先でも解決しない
- メールアドレスや個人を特定する情報を書かない
- private リポジトリの内部事情 (未公開の設計、社内固有の運用) を書かない。一般化できる
  知見だけを、出所を伴わない形で書く

`gitleaks` が検査する。ルールの canonical は `.gitleaks.toml` で、既定ルールだけでは
ユーザー名を含むパスは捕捉されないため custom ルールを 2 つ置いてある (macOS と Windows)。

**検査の網は書いた分しか広がらない。** 実際、当初は macOS の `/Users/<name>` しか見て
いなかったため `C:\Users\<name>` が素通りして履歴へ入った。新しい形の絶対パスを書くときは、
それが既存ルールに掛かるかを確かめること。ルールを足したら、検出すべき例と許可すべき例の
両方を実際に `gitleaks` へ通して確かめる (regex を読むだけでは false positive に気づけない)。

`.gitleaksignore` は「既に push 済みの履歴に入っていて、内容としては修正済み」のものだけを
記録する場所である。現在のツリーに残っている漏洩をここへ足して黙らせない。

## [MUST] 依存を増やさない

対象は **CI と pre-commit が回す Python** (`scripts/` 配下と、検証対象の `winvm.py`)。

- 標準ライブラリのみで書く。CI は runner の `python3` をそのまま使い、`setup-python` も
  `pip install` も置かない
- テストは `unittest`。pytest を入れない (理由は `scripts/run-python-tests.py` の docstring)

skill が配るスタンドアロンスクリプトはこの制約の外にある。`uv run --script` で PEP 723 の
依存宣言を持つものは実行時に uv が解決するので、リポジトリの検証系に依存が増えない
(例: `skills/tooling/markdown-to-pdf/scripts/render.py`)。

どちらの側でも、依存を足すときは pin すべきものが 1 つ増えることと引き換えに何を得るのかを
PR で説明する。

## [MUST] README.md を手で編集しない

`scripts/gen-readme.py` が生成する。パッケージ一覧の表は各 SKILL.md の frontmatter から
読むので、説明を変えたいときは frontmatter を直して `python3 scripts/gen-readme.py` を
実行する。`--check` は CI と pre-commit が回す。

**表以外の散文 (「使い方」「構造の規約」「ライセンス」) は frontmatter 由来ではなく、
`gen-readme.py` の `HEADER` / `FOOTER` 定数に入っている。** ここを直したいときに README.md を
編集すると、次の生成で書いた内容が消える。`--check` は赤くなるが文面は「frontmatter を
直せ」なので、原因に辿り着けない。

## [MUST] 規約は散文ではなく検査に落とす

機械検証できる制約 (命名・長さ・enum・ファイルの有無) を散文と検査の両方に literal で
書かない。散文は値を再掲せず、canonical な定義をファイル名で参照する。

| 領域 | canonical |
|---|---|
| パッケージの形と命名 | `scripts/check-package-shape.py` の docstring |
| `plugin.json` のフィールド | `claude plugin validate --strict` |
| README の内容 | 各 SKILL.md の frontmatter |
| secret とパスの漏洩 | `.gitleaks.toml` |

新しい規約を作るときは、まず検査に落とせないかを考える。落とせないものだけを散文で書く。

## コードとコメント

- コメントは日本語で書く
- 説明するのは WHY。WHAT は識別子で示す
- 外部コマンドの挙動や OS 固有の癖に由来する実装は、実測した内容を添える。
  `winvm.py` の「SSH は session 0 なのでスクリーンショットが黒画面になる」のような
  記述がそれで、これが無いと後から「無駄な回り道」に見えて消される

## 検証

```
pre-commit run --all-files
```

CI (`.github/workflows/ci.yml`) は 4 job だが、ローカルの pre-commit は 9 hook 走る。
`claude plugin validate` と `end-of-file-fixer` 系はローカルにしか無いので、
**CI が緑であることは全検査を通ったことを意味しない**。マージ前にローカルで回すこと。

逆向きの穴もある。ローカルは CI の上位集合ではない。

- **漏洩検査**: pre-commit の hook は `gitleaks git --staged` なので、`--all-files` を
  付けても走査対象は staged 差分だけ。index が clean だと `0 commits scanned` で緑になる。
  全履歴を走査するのは CI だけ
- **Python テスト**: `scripts/run-python-tests.py` が探すのは `skills/` と `plugins/` の
  配下だけ (`SEARCH_DIRS`)。`scripts/` に `test_*.py` を置いても収集されず、緑のまま
  何も実行されない。規約を担保している検査スクリプト自身が現状 無検査であることを意味する

つまり「ローカルで 9 hook 緑」も全検査を意味しない。どちらの緑も、何を何件見た結果なのかを
確かめてから根拠にすること。

### 外部コマンドを組み立てるコードは緑だけで完了にしない

`winvm` は ssh / scp / prlctl / cmd.exe / pwsh を組み合わせて動く。argv の組み立てや
パス変換の純粋ロジックはユニットテストで覆えるが、シェルのクォート規則・PATH 解決・
exit code の伝搬・OS 差はランタイムでしか壊れない。この層に手を入れたときは、対象の
VM で full chain を一度通してから完了とすること。

判定に使う目印は ASCII に保つ。ja-JP の VM は非対話出力を CP932 で書くので、
localized な文字列を判定に混ぜると環境依存で壊れる。

「そのコマンドが VM に存在するか」を確認するときは、PATH にあるかではなく起動できるかを
見ること。Microsoft Store の実行エイリアス (stub) は `where` に出るが非対話セッションから
起動するとアクセス拒否される。

## Issue 管理

`docs/issues/<NNN>_<title>/issue.md` の in-repo Markdown で管理する。起票・更新・
クローズ・reopen の手順は skill `dev-workflow:in-repo-issue` が canonical。

PR 本文には `Closes [Issue #NNN](../../docs/issues/...)` 形式で相対リンクを書く。
