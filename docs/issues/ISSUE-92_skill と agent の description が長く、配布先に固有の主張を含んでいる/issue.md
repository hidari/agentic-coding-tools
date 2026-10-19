---
status: open
---

# docs: skill と agent の description が長く、配布先に固有の主張を含んでいる

## 背景

消費側の棚卸しから、skill の一覧がコンテキストの予算を超え、呼ばれることの少ない skill の説明が名前だけに落ちている、という報告があった。このリポジトリが配る skill と agent の description には長いものがあり、予算を圧迫している側にあたる。

description の中には、特定のプロジェクトの事情を一般の事実のように書いたものもある。例えば `plugins/dev-workflow/skills/e2e-scenario-impact-check/SKILL.md` の description は、staging の E2E が唯一の自動検出網で、ローカルの E2E は CI で動かない、と断定している。配布物の description は配布先の判断材料になるので、汎用の記述であるべき。

ISSUE-64 は、description を README 用と呼び出しの判断用に分けるか、description ごと変えるかを決めるタスクを持っている。description を書き換える作業はその決定に依存するので、ISSUE-64 と順序を合わせる。

## タスク

- [ ] ISSUE-64 の description の扱いを決めるタスクと順序を合わせる
- [ ] 配る skill と agent の description の長さを数え、短くする対象と方針を決める
- [ ] description から、配布先に固有の主張を外す (e2e-scenario-impact-check を含む)
- [ ] 書き換えたら `python3 scripts/gen-readme.py` で README を生成し直す

## 関連

- ISSUE-64
- ISSUE-91
