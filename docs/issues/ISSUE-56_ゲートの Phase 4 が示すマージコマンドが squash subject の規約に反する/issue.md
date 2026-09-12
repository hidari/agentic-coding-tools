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

## 洗い出しの結果

ゲートが示す具体コマンドを全部拾い、それぞれ canonical の所在を確かめた。

| 箇所 | 具体形 | canonical の所在 | 判定 |
|---|---|---|---|
| Phase 0 | `git diff <base>..HEAD --name-only` / `--stat` / `gh pr view --json` | 無し (ゲート自身の材料収集) | 問題なし |
| Phase 1 | `git diff <base>..HEAD` | 同上 | 問題なし |
| Phase 3 | `gh pr checks <num> --watch` | 無し | 問題なし |
| Phase 3 | `make format` / `make ci-<area>` / `make help` | プロジェクトの CLAUDE.md | 別型の違反 (下記) |
| Phase 4 | `gh pr merge` のフラグ | `dev-workflow:commit-and-pr-message` の C.3 | 本 Issue の主題 |
| Phase 4 | `gh pr create` | 同 skill の C.2 | 既に委譲済み |

### 関わる skill は 2 つではなく 3 つだった

背景節は「2 つの skill が同じ操作について逆のことを言っている」と書いたが、実際には
`dev-workflow:commit-and-pr-message` も同じ操作を持つ。しかも同 skill は求められている形を
既に実装していた。

> **`gh pr merge --subject` は省略しないこと。** 省略すると GitHub が subject を生成し、
> その形が in-repo Issue の識別子規約に違反する。書くべき形の canonical は
> `dev-workflow:in-repo-issue` の `## PR / コミット規約` 節で、本 skill は形を再掲しない。

同 skill の「関連」節は責務の境界も宣言している。「本 skill は何をどう書いて渡すかだけを持ち、
いつ実行してよいかは持たない」。この分割で見るとゲート側の欠陥の形が変わる。ゲートは
`gh pr create` を既に委譲済みで、`gh pr merge` だけ委譲していない。**問題は矛盾ではなく
非対称**だった。

### Phase 3 の `make` 前提はこのリポジトリで成立していない

`make format` / `make ci-<area>` / `make help` と書いてあるが、配布元のこのリポジトリに
Makefile は無い (実測)。検証の入口は `pre-commit run --all-files` で、canonical はリポジトリの
CLAUDE.md の「検証」節。

`gh pr merge` の件とは型が違う。あちらは「別 skill が canonical を持つ操作を具体形で書いた」、
こちらは「存在しないツールを既定にした」。どの経路も赤くならない点と、手順どおりに実行すると
失敗する点は同じ。

### `--delete-branch` は落とせない

当初案 (a) はこの具体形ごと落として `in-repo-issue` へ委ねる形だったが、参照先が
`--delete-branch` を持っていない。`git-branch-switcher` も持たず、リポジトリ全体でゲートの
1 行にしか無い (実測)。委ねると情報が消える。

リモート設定で代替もできない。`gh pr merge --delete-branch` はローカルとリモートの両方を消すが、
リポジトリ設定の `delete_branch_on_merge` が消すのはリモートだけで、ローカルは残る。なお
配布元の現在の設定は無効。

```json
{"allow_merge_commit":true,"allow_rebase_merge":true,"allow_squash_merge":true,
 "delete_branch_on_merge":false,"squash_merge_commit_message":"COMMIT_MESSAGES",
 "squash_merge_commit_title":"COMMIT_OR_PR_TITLE"}
```

### subject へ数字記法が入る経路

| 経路 | 数字記法が入るか | 塞ぎ方 |
|---|---|---|
| `gh pr merge --squash` (`--subject` 無し) | 入る (PR #46 で実測) | `--subject` を渡す |
| `gh pr merge --squash --subject` | 入らない (PR #47 で実測) | 適用済み |
| `gh pr merge --merge` | **未実測** | リポジトリ設定で方式を無効化 |
| `gh pr merge --rebase` | 入らない。rebase は新しいコミットメッセージを作らず元のコミットをそのまま乗せる (GitHub のドキュメントが明記)。commit-msg hook が既に見た経路 | 構造的に安全 |
| GitHub web UI でのマージ | `--subject` を経由しないので既定生成 | リポジトリ設定 / 運用 |

`--merge` を未実測としたのは、GitHub の API ドキュメントが `commit_title` の既定値を書いておらず、
「About pull request merges」も merge commit の既定メッセージを書いていないため (両方とも実際に
読んで確認した)。慣行として知られる形はあるが、実測していないので断定しない。

配布元では 3 方式が全て有効だが、履歴に merge commit は 0 件で squash 一本で運用されてきた。
設定で方式を絞れば経路自体を消せる。これはリポジトリ設定の変更なので別途判断する。

### 検査に落とさない判断

プロジェクトの CLAUDE.md は「規約は散文ではなく検査に落とす」を MUST に置いているが、今回の
規約は落とさない。禁じたいのは「canonical を持たない場所が具体形を持つこと」で、canonical の
所在はファイル間の関係なので regex で表現できない。特定のフラグ名を禁じる検査は書けるが、別の
フラグで同じ欠陥が起きたとき漏れる。同型の失敗はこのリポジトリの漏洩ガードで既に踏んでいる
(「検査の網は書いた分しか広がらない」)。

## タスク

- [x] ゲートの Phase 4 をどう直すか決める → `gh pr create` と対称にする。マージも
      `commit-and-pr-message` の Phase C へ委譲し、`--delete-branch` だけはゲートに残す
      (誰の canonical でもなく、かつリモート設定で代替できないため)
- [x] 同種の矛盾が他に無いか、ゲートが示す具体コマンドを全部洗う → 上の表。別型の違反を
      1 件 (Phase 3 の `make` 前提) 発見
- [x] Phase 3 の `make` 前提を、入口を決め打ちしない形へ直す
- [ ] 直したあと、ゲートの手順どおりに 1 回マージして subject が検査を通ることを実測する
      (散文を直しただけでは、次に踏むまで効いたか分からない)
- [ ] マージ方式を squash 一本へ絞るかを決める。`--merge` の未実測を残したまま経路を開けておくか、
      設定で閉じるか

## 関連

ISSUE-20 (同じ症状を扱って closed。裁定は本 Issue の前提で、射程が規則の記述までだった)
ISSUE-14 (識別子と GitHub の番号空間の衝突。ISSUE-20 の親にあたる話題)
ISSUE-32 (配布先で検査が走らない問題。「どの経路も赤くならない」型として同じ構造を持つ)
