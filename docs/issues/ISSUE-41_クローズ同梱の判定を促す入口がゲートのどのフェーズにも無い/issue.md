---
status: open
---

# fix: クローズ同梱の判定を促す入口がゲートのどのフェーズにも無い

## 背景

`dev-workflow:in-repo-issue` は「クローズ経路: feature PR 同梱を優先 (main 直 push を避ける)」節で、
main への直 push を禁じるプロジェクトでは Issue のクローズを feature PR に同梱せよと規定している。

一方 `dev-workflow:pre-merge-quality-gate` は Phase 4 で `gh pr create` と `gh pr merge` を実行し、
Phase 5 で `gh pr merge` の**後**に in-repo-issue の Phase C を起動する。

同梱するかどうかの判断が要るのは `gh pr create` の**前**である。したがって gate の既定の経路を
そのまま辿ると、判断の機会が一度も訪れないまま PR が作られ、マージされ、そのあとで
「クローズをどうするか」が問われる。その時点では同梱はもう選べない。

## 実測 (2026-08-28)

### gate は保護ブランチの存在を一度も話題にしない

`plugins/dev-workflow/skills/pre-merge-quality-gate/SKILL.md` を `同梱` / `直 push` / `ruleset` /
`保護` で走査すると **0 件**。gate が in-repo-issue を名指しするのは 1 箇所だけで、それは
Phase 5 の中にある (= マージ後)。

同梱節が置かれているのは in-repo-issue のライフサイクル節の Phase B と Phase C の間で、
「PR を作ろうとしている人」が読みに行く位置ではない。

### 規約は守れる。既定の経路が導かないだけ

同じリポジトリ・同じ規約のもとで結果が分かれている。

| PR | 実装と close | 結果 |
| --- | --- | --- |
| PR #30 (ISSUE-31) | 同梱した | main への push が 1 回 |
| PR #32 (ISSUE-11) | 同梱しなかった | close 用に PR #33 を追加で立てた |

「規約を知らないと守れない」のではなく、gate の手順どおりに進むと同梱の分岐に触れずに
通過できてしまう。

### コスト

CI の run を数えた。同梱しなかった側は `pull_request` と `push` が PR ごとに 1 本ずつ出るので、
**run 4 本 (job 16 個)** かかった。同梱していれば run 2 本 (job 8 個) で済む。

このリポジトリの CLAUDE.md は CI コストの抑制を明記しており、実害がある。

### 副作用: squash subject の規約が衝突する

PR が分かれると、`## PR / コミット規約` の D 形式が要求する `(PR #<Issue を閉じた PR>)` と、
squash subject の規約が要求する `(PR #<マージされる PR 自身>)` が別の番号になる。
実際 PR #33 のマージでは両方を書く形 (`(PR #32) (PR #33)`) になった。

規約どうしの矛盾ではない。同梱していれば 2 つは同じ番号を指すので、分割したときだけ現れる。

## 何を直すか

同梱の判定を、判断が有効な時点で促す形にする。置き場の候補は 2 つある。

1. gate の Phase 0 か Phase 4 の手前に「クローズ同梱の要否」を判定する手順を足す
2. in-repo-issue の同梱節を、Phase C の直前ではなく PR 作成の文脈から見える位置へ移す

1 は gate が in-repo-issue の規約を写すことになるので、二重管理を避けるなら
「in-repo-issue の同梱節を読んで判定する」というポインタに留める必要がある。

判定に要る事実 (main が保護されているか) は機械で取れる。ただし `~/.claude/CLAUDE.md` が
記録しているとおり、classic API の 404 だけでは判定できず repository ruleset も見る必要がある。

## タスク

- [ ] 同梱の要否を判定する入口を、`gh pr create` より前に置く。置き場は gate 側か
      in-repo-issue 側かを決める
- [ ] 判定を散文の指示ではなく実行できる形にする (保護の有無を `gh api` で確かめる手順)。
      classic API の 404 だけで「保護なし」と結論しないこと
- [ ] 同梱しなかったときに squash subject が 2 つの PR 番号を持つ件を、規約として
      許容するのか避けるのかを決める
- [ ] 変異注入で確認する: 判定の入口を外すと、既定の経路が同梱に触れずに通過できることを
      再現できる形にする

## 関連

- `plugins/dev-workflow/skills/pre-merge-quality-gate/SKILL.md` — Phase 4 が `gh pr create` を、
  Phase 5 が merge 後の close 起動を持つ
- `plugins/dev-workflow/skills/in-repo-issue/SKILL.md` — 「クローズ経路: feature PR 同梱を優先」節と
  「PR / コミット規約」節
- ISSUE-17 — 同じ継ぎ目 (gate と in-repo-issue の受け渡し) の別の欠陥。C.2 の skip 根拠が
  gate の持たない検証を指している
- ISSUE-33 — 同じ gate の別の欠陥。出力書式が全レーン skipped でも合格を許す
- ISSUE-42 — 本 Issue と同じ PR で起票した別件
