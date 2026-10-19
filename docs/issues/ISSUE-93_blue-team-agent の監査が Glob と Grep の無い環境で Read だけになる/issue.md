---
status: open
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

- [ ] blue-team-agent の Mode B で、`Glob` / `Grep` が無い環境での探索手段を決める。選択肢は、`tools:` から `Bash` を外して `Glob` / `Grep` を戻す形と、読み取りだけという制約を保ったまま `Bash` の検索を許す形
- [ ] red-team-agent の `Grep` を前提にした手順を、`Grep` が無い環境でも読める書き方にする
- [ ] 3 つの agent の `tools:` に挙げている `Glob` / `Grep` / `TodoWrite` を残すか外すか決める。`Glob` / `Grep` が渡される条件 (Windows などの既定、`Bash` を挙げない subagent、`--tools` で明示したとき) を踏まえる
- [ ] 変更したあと、`Glob` / `Grep` が既定で無い環境で agent を起動し、決めた手段で探索できることを確かめる

## 関連

- ISSUE-91
