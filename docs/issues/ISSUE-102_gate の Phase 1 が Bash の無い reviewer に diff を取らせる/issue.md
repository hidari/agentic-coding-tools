---
status: open
---

# fix: gate の Phase 1 が Bash の無い reviewer に diff を取らせる

## 背景

出所は dotfiles のセッションから受けた skill の欠陥報告 (受け箱経由、2026-11-10)。

`dev-workflow:pre-merge-quality-gate` の Phase 1 の 2 番目は、`feature-dev:code-reviewer` へ渡す prompt に「全 diff の取得方法 (`git diff <base>..HEAD`)」を入れるよう指示している。しかしこの agent 型の道具に Bash は無い。

- 報告 (実測): 指示どおりに起動すると、reviewer は `git diff` を実行できない。diff をファイルへ書き出して Read させる形が要る
- 本リポジトリでの確認: 2026-11-10 のセッションで Agent ツールが示す agent 型の一覧では、`feature-dev:code-reviewer` の道具は Glob / Grep / LS / Read / NotebookRead / WebFetch / TodoWrite / WebSearch / KillShell / BashOutput で、Bash を含まない。起動して挙動を見たわけではない

Bash が無い reviewer は、取れない diff の代わりに変更ファイルの全文を Read して読むことがある。そうなると削除された行は見えないので、「消した内容が正しいか」という観点が黙って落ちる。

## タスク

- [ ] 手当ての形を決める。候補は 2 つ: (a) 親が diff をファイルへ書き出し、そのパスを prompt で渡して Read させる (b) diff を見る観点だけ Bash を持つ agent 型 (general-purpose) へ移す。(a) は書き出し先を配布先でどこに置くかも決める (本リポジトリの `.cache/` の規約は配布先の規約ではない)
- [ ] 変更前と変更後で reviewer に diff が実際に届くかを、agent に受け取った道具と読めた diff の範囲を自己申告させて確かめる。変更前を対照に並べる
- [ ] Phase 1 の他の項目 (simplify への args、boy-scout sweep の prompt) にも、起動先が持たない道具を前提にした指示が無いかを見る

## 関連

- ISSUE-47: gate の Phase 1 の記述が実際の起動と違う。同じ Phase 1 の同じ項目を触る
- ISSUE-99: gate の手順に足す項目がある。Phase 1 の文面が衝突しうる
- ISSUE-103: gate のレビューの形を観点ごとの reviewer と反証役へ組み替える提案。組み替えるなら reviewer の agent 型の選び方もそちらで決まる
