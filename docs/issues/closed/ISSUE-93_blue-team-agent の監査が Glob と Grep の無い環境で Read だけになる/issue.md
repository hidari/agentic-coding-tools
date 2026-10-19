---
status: closed
---

# fix(agents): blue-team-agent の監査が Glob と Grep の無い環境で Read だけになる

## 背景

ISSUE-91 の確認中に分かった。red-team-agent / blue-team-agent / monkey-explorer-agent は `tools:` に `Glob` / `Grep` / `TodoWrite` を挙げている。Claude Code 2.1.303 (macOS のネイティブ版) のヘッドレス実行で、`--plugin-dir` から 3 つを起動したところ、自己申告されたツールの一覧にはどれも無かった。

公式の tools reference (2026-10-19 に確認) の記述は次のとおり。

- `Glob` と `Grep` は macOS / Linux / WSL では既定のツールに含まれず、検索は Bash 経由の `find` / `grep` になる。ただし戻る場合がいくつかあり、その 1 つは「subagent が `tools` に `Glob` か `Grep` を挙げ、`Bash` を挙げない」とき。3 つの agent はどれも `tools:` に `Bash` も挙げているので、この条件に当たらない。プローブの結果はこれと整合する
- `TodoWrite` が既定で無い経路は 2 つある。Opus 4.7 / Sonnet 4.6 / Haiku 4.5 より新しいモデルでは、opt-in しない限り Task 系のツールごと渡されない。それ以前のモデルでは、`TodoWrite` の代わりに Task 系のツールが渡される。プローブでは agent のモデルの版を記録しておらず (frontmatter は `model: opus`)、どちらの経路だったかは分からない。ただ、自己申告に Task 系のツールが無かったので、前者の可能性が高い

影響は agent ごとに違う。

- blue-team-agent の Mode B は、静的で読み取りだけの監査として「`Glob` / `Grep` / `Read` だけを使う」と定めている (本文の B.0 と、モードを問わない制約の節)。これらの環境では `Read` しか残らず、ファイルを列挙も検索もできない。`Bash` の `find` / `grep` を使えば「だけ」の文言には反するが、制約の目的 (読み取りだけ、HTTP を送らない) には反しない。Mode B を実際に走らせてはいないので、agent が網羅性を落とすのか `Bash` に寄せるのかは観測していない
- blue-team-agent が `Bash` を使うのは、Phase 0 の profile 検証の任意の手段だけである。`tools:` から `Bash` を外せば上の条件に当たって `Glob` / `Grep` が戻り、Mode B の「だけ」が文言ではなく構造で効くようになる
- red-team-agent の Layer 1 は、秘密情報の検出などで `Grep` を使う前提で書かれている。同じ Layer の他の手順は `Bash` を使っていて、`Bash` で代わりができる。Layer 1 は opt-in で、既定は Layer 3 と 4 なので、影響は blue-team-agent より小さい
- monkey-explorer-agent の本文は `Glob` / `Grep` に依存していない
- `TodoWrite` に依存する本文は 3 つとも無い

## タスク

- [x] blue-team-agent の Mode B で、`Glob` / `Grep` が無い環境での探索手段を決める。選択肢は、`tools:` から `Bash` を外して `Glob` / `Grep` を戻す形と、読み取りだけという制約を保ったまま `Bash` の検索を許す形
- [x] red-team-agent の `Grep` を前提にした手順を、`Grep` が無い環境でも読める書き方にする
- [x] 3 つの agent の `tools:` に挙げている `Glob` / `Grep` / `TodoWrite` を残すか外すか決める。`Glob` / `Grep` が渡される条件 (Windows などの既定、`Bash` を挙げない subagent、`--tools` で明示したとき) を踏まえる
- [x] 変更したあと、`Glob` / `Grep` が既定で無い環境で agent を起動し、決めた手段で探索できることを確かめる

## 結果

決めたこと:

- blue-team-agent の `tools:` を `Read, Glob, Grep, Write` にした。`Bash` を外したので `Glob` / `Grep` が戻る。着手してから、`Bash` がレポート (`blue-team.md`) を書く唯一の手段でもあったと分かったので、`Bash` を `Write` に置き換えた。本文が「どのモードでも HTTP を送らない」と定めているのに合わせて、`WebFetch` / `WebSearch` も外した。HTTP を送らないこととシェルを使わないことは、文言ではなく受け取るツールで効くようになった。ソースを書き換えない (読み取りだけ) ことと「`OUTPUT_DIR` の外に書かない」は、`Write` がどこにでも書けるので文言のままである
- blue-team-agent の本文も新しいツールに合わせた。Mode B の「`Glob` / `Grep` / `Read` だけ」は監査の手段に限り、`Write` は `blue-team.md` を書くときだけ使うと明記した。Mode A の findings.json の schema 検証は、読んで必須項目を確かめる形にした。同じ日付ディレクトリに `blue-team.md` が既にあるときは、`Write` の前に `Read` する (`Write` は読んでいないファイルを上書きできない)
- blue-team-agent の Phase 0 の profile 検証から、`Bash` で schema validator を回す任意の手段を外し、必須項目を読んで確かめる形だけにした
- red-team-agent は `Bash` を残し、`Grep` を前提にした 2 つの手順に「`Grep` が無いところでは Bash の `grep`」を書き足した
- 3 つの agent の `tools:` から `TodoWrite` を外した。本文はどれも使っておらず、既定でも渡されない。`Glob` / `Grep` は残した。blue-team-agent では今回の変更で渡されるようになり、ほかの 2 つでも Windows など既定で渡される環境がある

確認 (Claude Code 2.1.303、macOS のネイティブ版。ISSUE-91 と同じく、plugin を別名でコピーして `--plugin-dir` のヘッドレス実行で起動した):

- blue-team-agent が自己申告したツールは `Read, Write, Glob, Grep` と harness が足すツールだった。ISSUE-91 の確認 (`Bash` を挙げていた定義) では `Glob` / `Grep` が無かったので、それが対照になる
- blue-team-agent に、無かった日付ディレクトリの下へファイルを書かせ、`Glob` でそのファイルを見つけさせた。どちらも成功し、書いたファイルはディスク上でも別に確かめた
- red-team-agent と monkey-explorer-agent は `Read, Bash, WebFetch, WebSearch` と harness が足すツールで、`TodoWrite` を外す前と変わらない

## 関連

- ISSUE-91
