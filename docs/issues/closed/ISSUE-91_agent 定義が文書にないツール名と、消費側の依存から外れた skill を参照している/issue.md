---
status: closed
---

# fix(agents): agent 定義が文書にないツール名と、消費側の依存から外れた skill を参照している

## 背景

`plugins/security-blue-red-team/agents/` の red-team-agent と blue-team-agent、`plugins/web-monkey-qa/agents/` の monkey-explorer-agent は、frontmatter の `tools:` に `LS` を挙げている。

公式の tools reference と subagents のページ (2026-10-19 に確認) にある組み込みツールの一覧には `LSP` はあるが、`LS` は無い。CHANGELOG で `LS` に触れているのは 0.2.82 の改名 (`LSTool -> LS`) だけで、削除を告げる項目は見つからなかった。文書には、`tools:` のどの項目も解決できないときは通常は起動を拒否する (v2.1.208 以降) とだけあり、一部の項目だけが解決できない場合にどうなるかは書かれていない。

`playwright-test` / `playwright-cli` は外部の skill で、少なくとも 1 つの消費側では apm の依存から外された。このリポジトリでは次の箇所がこれらを名指している。

- red-team-agent と blue-team-agent の「他の skill を呼ばない」の列挙
- `plugins/security-blue-red-team/README.md` の同じ趣旨の列挙
- `plugins/dev-workflow/skills/e2e-scenario-impact-check/SKILL.md` の関連の節。sibling skill として、修正案の根拠に参照している
- `skills/tooling/chrome-devtools-debugger/SKILL.md` の使い分けの表

禁止の列挙は、対象の skill が入っていない環境でも害は無い。一方 e2e-scenario-impact-check は、その skill がある前提で根拠に使っている。

## タスク

- [x] 3 つの agent 定義の `tools:` から `LS` を外す
- [x] 外したあとも、3 つの agent が実際に起動してツールを受け取れることを確かめる
- [x] `playwright-test` / `playwright-cli` を名指す上の 5 ファイルを、それぞれ残すか直すか決めて反映する。特に e2e-scenario-impact-check の参照は、その skill が無い環境でどう読まれるかを決める

## 結果

起動の確認は、3 つの agent を含む plugin を別名でコピーし、Claude Code 2.1.303 (macOS のネイティブ版) のヘッドレス実行で `--plugin-dir` から読み込んで行った。各 agent に、呼べるツールの名前だけを答えさせた (ツールの一覧は agent の自己申告)。

- `LS` を外した版も、対照として `LS` を残した版も、3 つとも起動した。自己申告されたツールは両方の版で同じで、`Read, Bash, WebFetch, WebSearch` と、harness が足すツールだった。`LS` を残しても起動もツールの一覧も変わらなかったので、外しても挙動は変わらない
- `tools:` に挙げている `Glob` / `Grep` / `TodoWrite` も、この環境では一覧に無かった。tools reference によれば、`Glob` と `Grep` は macOS / Linux / WSL では既定のツールに含まれない (検索は Bash 経由になる)。`TodoWrite` は Task 系のツールに置き換えられて既定で無効になっていて、原因が別にある。blue-team-agent の Mode B は「`Glob` / `Grep` / `Read` だけを使う」と定めていて、これらの環境では `Read` しか残らない。red-team-agent の本文も `Grep` を使う前提で書かれている。見た目の問題ではなく機能に関わるが、この Issue の範囲外なので別に扱う

`playwright-*` を名指す箇所は次のように決めた。

- red-team-agent / blue-team-agent / `plugins/security-blue-red-team/README.md` の禁止の列挙は残す。対象の skill が入っていない環境では何も起きず、入っている環境では禁止として効く
- e2e-scenario-impact-check の関連の節は、`playwright-test` と `ast-grep-practice` を「sibling skill」から「このリポジトリの外の skill」に直した。どちらもこのリポジトリには無い。`playwright-test` は、入っていなければ対象プロジェクトの既存の E2E テストの書き方を根拠にする、と書き足した。retrospective-codify の `ast-grep-practice` への参照も同じ扱いにそろえた
- chrome-devtools-debugger の使い分けの表は、2 つともこのリポジトリの外の skill だと分かる書き方にした

## 関連

- ISSUE-92
