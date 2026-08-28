---
status: open
---

# fix: パス指定のコミットで pre-commit が必ず落ちる

## 背景

`git commit -- <paths>` (パスを指定したコミット) を実行すると、pre-commit の Python
テスト hook が `scripts/test_check_related_refs.py` で落ちる。

落ちるのはこの形だけである。コミットしたいものだけを stage してからパス指定なしで
`git commit` する形は通る (実測。この Issue を含む変更を 3 コミットへ分けるのに使った)。
既存 20 コミットが一度も踏んでいないのはそのためで、欠陥が無いからではない。

診断が実態を指さないのも問題になる。落ちたテストは fixture の `ISSUE-1_alpha` を
**実リポジトリのパス**で探して「読めない」と報告するので、読んだ人は実ツリーが壊れたと
考える。実際には fixture が実ツリーを走査している。

## 実測 (2026-08-29)

git は `git commit -- <paths>` のときだけ一時 index を使い、その位置を `GIT_INDEX_FILE`
として hook へ渡す。テストの subprocess はそれを継承する。

| 条件 | `scripts/test_check_related_refs.py` |
| --- | --- |
| 通常 | 56 件 pass |
| `GIT_INDEX_FILE` を立てて実行 | 1 failure / 49 errors |
| `git commit -- <paths>` の hook 内 | 1 failure / 1 error で commit が中断 |

`scripts/test_check_issue_closure.py` は同じ条件で 34 件 pass のまま影響を受けない。
`check-issue-closure.py` は母集団を `iterdir` で作り index を読まないため。つまり
影響するのは `git ls-files` を使う検査だけで、この差は偶然の免責であって設計ではない。

## 手当ての候補と、単純な案が効かない理由

`GIT_ENV` から `GIT_*` を落とす形は**効かない**。原因の呼び出しが subprocess ではなく
プロセス内だから。`Fixture.check()` は `crr.check(self.dir, ...)` を直接呼び、その中の
`git ls-files` が `os.environ` を読む。`GIT_ENV` は subprocess にしか渡らない。

production 側で環境を消毒するのも採れない。`check-related-refs.py` は pre-commit hook
として走るので、部分コミットの一時 index を見るのはむしろ正しい挙動になる。

したがって手当てはテスト側の構造に入る。プロセス内呼び出しの周りだけ環境を差し替えるか、
プロセス内呼び出しをやめて CLI 経由へ揃えるかの判断が要る。後者は
`scripts/test_check_issue_closure.py` が既に採っている形。

## タスク

- [ ] プロセス内呼び出しの環境をどう隔離するかを決める (呼び出しの周りで差し替えるか、
      CLI 経由へ揃えるか)
- [ ] 決めた形を実装し、`GIT_INDEX_FILE` を立てた状態でも緑になることを確認する
- [ ] パス指定のコミットが実際に通ることを、この 2 つを同時に満たすコミットで確かめる
- [ ] 同じ形の隔離漏れが他のテストにも無いかを、プロセス内呼び出しの有無で棚卸しする

## 関連

- ISSUE-12: scripts の検査スクリプトが自分自身のテストを持たない。テストの構造を触る点が
  重なる
- ISSUE-41: クローズ同梱の判定を促す入口。本件はその実装中に、コミットを 3 つへ分けようと
  して踏んだもの。原因のファイルが対象外だったため切り出した。ISSUE-41 側は stage して
  からパス指定なしで commit する形へ切り替えて着地させている
