---
status: open
---

# docs: pre-merge-quality-gate が未検査の増分と未開始の CI を見逃す

## 背景

出所は dotfiles の ISSUE-132 (受け箱に届いた skill の改善提案) の「pre-merge-quality-gate: ゲートの順序、増分、CI の待ち方」節の最初の 3 項目。どれも別のリポジトリのセッションの申告で、本リポジトリでも dotfiles でも再現していない。同じ節の 4 項目め (simplify の Efficiency 観点の判断の軸) と「simplify: 並列の reviewer を読み取り専用にする」節は、dotfiles 側で確かめてから渡されるのでここには含めない。

`dev-workflow:pre-merge-quality-gate` は、前のゲートが見た commit と HEAD の差を扱う手順を持たない。CI を待つ手順は `gh pr checks <num> --watch` を名指ししている (Phase 3 のクローズ同梱の項)。

## 報告

- 報告 (実測): 前回のゲートが CI や別のレビューと並行で走っていた場合、その指摘を直して積んだコミット自体はどのゲートも通っていない。ゲートが見た commit を特定し、HEAD までの増分にゲートを当てると、約 600 行の増分から確定の指摘が 2 件出た。報告者の提案: マージの経路にこの手順を足す
- 報告 (実測): push の直後で新しい run が pending のとき、`gh pr checks --watch` は待たずに「no checks reported」で rc=1 で終わった。前の head の run が in_progress でも同じだった。同じ報告で、新しい run を `gh run list --branch <branch> --json databaseId,headSha` で引き、push した commit と一致する run の ID を `gh run watch --exit-status` に渡すと、pending の間も待ち続けて rc 0 で終わった (実測)。報告者の提案: CI を待つ手順を headSha で run を特定して `gh run watch` で待つ形へ置き換えるか、rc=1 と「no checks reported」の組を「まだ始まっていない」と扱う注記を足す
- 報告 (実測): 長いゲート (20〜30 分) の前に並列レビューを先に回し、修正を入れてから最終のゲートを 1 回だけ回す順序にした

## タスク

- [ ] 前のゲートが見た commit から HEAD までの増分にゲートを当てる手順を、マージの経路に足すかを決める
- [ ] CI を待つ手順を、headSha で run を特定して `gh run watch --exit-status` で待つ形へ置き換えるか、「no checks reported」の rc=1 を未開始として扱う注記にするかを決める。本リポジトリで pending の直後に `gh pr checks --watch` を打って挙動を確かめてから決める
- [ ] 長いゲートの前に並列レビューを先に回す順序を、ゲートの手順に書くかを決める

## 関連

- ISSUE-17: C.2 の「gate が既に検証済み」が指す CI が別物。同じく gate が CI のどの run を見ているかを扱う
- ISSUE-47: gate の Phase 1 が実際の同時起動数を書かない。並列レビューの順序と同じ Phase を触る
