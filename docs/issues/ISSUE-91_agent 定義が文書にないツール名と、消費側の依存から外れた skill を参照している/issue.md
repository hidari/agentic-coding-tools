---
status: open
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

- [ ] 3 つの agent 定義の `tools:` から `LS` を外す
- [ ] 外したあとも、3 つの agent が実際に起動してツールを受け取れることを確かめる
- [ ] `playwright-test` / `playwright-cli` を名指す上の 5 ファイルを、それぞれ残すか直すか決めて反映する。特に e2e-scenario-impact-check の参照は、その skill が無い環境でどう読まれるかを決める

## 関連

- ISSUE-92
