---
status: open
---

# fix: ゲートの Phase 4 が示すマージコマンドが squash subject の規約に反する

## 背景

`dev-workflow:pre-merge-quality-gate` の Phase 4 は、マージの実行をこう示している。

> ここで初めて `gh pr merge <num> --squash --delete-branch` または `gh pr create`。

一方 `dev-workflow:in-repo-issue` の「squash merge の subject は既定に任せず明示する」節は、
`--subject` で免除形を書くことを要求している。`--subject` を渡さないと GitHub がサーバ側で
subject の末尾へ括弧付きの数字記法を付けるため。

**ゲートの例示をそのまま実行すると必ず記法違反が出る。** 2 つの skill が同じ操作について
逆のことを言っており、ゲート側は姉妹 skill を参照もしていない。

実際に踏んだ。PR #46 をゲートの例示どおりにマージしたところ、main の subject がこうなった。

```
feat(security): 禁止語リストで固有名詞の流入を検査する層を足す (ISSUE-15) (#46)
```

リポジトリ自身の検査にかけると赤になる。

```
git log --format='%B' origin/main -1 > .cache/merge-msg.txt
python3 plugins/dev-workflow/skills/in-repo-issue/scripts/issue-id.py --check-text .cache/merge-msg.txt
#   [x] .cache/merge-msg.txt:1: #46 は GitHub の番号空間を指す。in-repo Issue なら
#       ISSUE-46 と書く。自リポの PR なら 'PR ' を、他リポなら 'owner/repo' を直前に置く
# rc=1
```

なお本 Issue を起票するときも同じ違反を踏んだ。上の 2 つの block を当初 4 スペース字下げで
書いたところ `--check` が両方を違反として報告した。免除されるのはフェンス内だけで、字下げの
block は免除に入らない (実測)。**違反を文書化するには違反文字列を引用するしかなく、引用の
書き方で結果が変わる。**

### どの経路も赤くならない

この違反が増え続ける理由は `in-repo-issue` 側が既に書いている。subject はサーバ側で生成される
ので commit-msg hook が届かず、`--check` は追跡ファイルしか見ず、CI にこの surface が無い。
**手順に従った結果として、誰も見ていないところで違反が 1 件ずつ増える。**

ゲートは「認知負荷で MUST ルールが飛ばないように構造化する」ことを目的に置いた skill なので、
その例示が別の MUST を破る形になっているのは目的に反する。

### ISSUE-20 との関係

同じ症状は ISSUE-20 が扱って closed になっている。裁定は「免除に加えず `--subject` で明示する」
((c) 案) で、規則を `in-repo-issue` へ書くところまでが射程だった。同 Issue のタスクに姉妹 skill の
例示を直す項目は無いので、本 Issue は reopen ではなく新規として立てる。対象が別 (規則の不在では
なく、規則に反する例示の残存)。

## タスク

- [ ] ゲートの Phase 4 をどう直すか決める。少なくとも次の 2 案がある
      (a) Phase 4 の例示から `--squash --delete-branch` の具体形を落とし、マージコマンドの
      canonical を `in-repo-issue` の該当節へ委ねる
      (b) Phase 4 に `--subject` を含む形を書く。ただし免除形を 2 箇所へ literal で書くことに
      なるので、CLAUDE.md の「同じ制約を 2 箇所に書かず canonical を 1 つ決める」に抵触する
- [ ] 同種の矛盾が他に無いか、ゲートが示す具体コマンドを全部洗う。Phase 3 の `make format` /
      `make ci-<area>` と Phase 4 の `gh pr create` も、別 skill が canonical を持つ操作を
      具体形で書いている疑いがある
- [ ] 直したあと、ゲートの手順どおりに 1 回マージして subject が検査を通ることを実測する
      (散文を直しただけでは、次に踏むまで効いたか分からない)
- [ ] `#<数字>` が subject へ入る経路が他に無いか確かめる。GitHub web UI でのマージと、
      merge commit / rebase の各方式は `--subject` を経由しない

## 関連

ISSUE-20 (同じ症状を扱って closed。裁定は本 Issue の前提で、射程が規則の記述までだった)
ISSUE-14 (識別子と GitHub の番号空間の衝突。ISSUE-20 の親にあたる話題)
ISSUE-32 (配布先で検査が走らない問題。「どの経路も赤くならない」型として同じ構造を持つ)
