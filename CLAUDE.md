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

`gitleaks` が staged 差分と全履歴を検査する。検査ルールの canonical は `.gitleaks.toml`。
既定ルールだけでは `/Users/<name>` は捕捉されないため custom ルールを置いてある。

## [MUST] 依存を増やさない

- Python は標準ライブラリのみで書く。CI は runner の `python3` をそのまま使い、
  `setup-python` も `pip install` も置かない
- テストは `unittest`。pytest を入れない (理由は `scripts/run-python-tests.py` の docstring)
- 依存を足したくなったら、まず標準ライブラリで書けないかを確かめる。足す場合は
  pin すべきものが 1 つ増えることと引き換えに何を得るのかを PR で説明する

## [MUST] README.md を手で編集しない

`scripts/gen-readme.py` が各パッケージの SKILL.md frontmatter から生成する。一覧を手で
書くと必ず drift するため、`name` と `description` は frontmatter を唯一の真実として読む。

説明を変えたいときは SKILL.md の frontmatter を直し、`python3 scripts/gen-readme.py` を
実行して README を再生成する。`--check` は CI と pre-commit が回す。

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
