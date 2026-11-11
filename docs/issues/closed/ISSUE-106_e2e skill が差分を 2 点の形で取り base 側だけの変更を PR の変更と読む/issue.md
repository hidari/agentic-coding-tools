---
status: closed
---

# e2e skill が差分を 2 点の形で取り base 側だけの変更を PR の変更と読む

## 背景

`dev-workflow:e2e-scenario-impact-check` の SKILL.md は、変更ファイル一覧、変更概要、agent へ渡す全 diff の取得のどれも `git diff <base>..HEAD` の 2 点の形で書いている。2 点の形は `<base>` と `HEAD` の比較なので、分岐の後に base が進んでいると、PR が触っていない base 側の変更が逆向きの差分として混ざる。使い捨てのリポジトリで、main だけに足したファイルが `D` として出ることを確かめた (`main...HEAD` の 3 点の形では出ない)。

e2e skill は「フロント変更が無ければ早期 skip」を変更ファイル一覧で判定するので、base 側だけのフロント変更があると skip すべき PR で agent が立ち、逆に影響を調べる対象にも PR が触っていないファイルが入る。

`dev-workflow:pre-merge-quality-gate` の同じ問題は ISSUE-102 と ISSUE-103 の PR で 3 点の形に直した。その PR は e2e skill を触っていないので、こちらは別に扱う。

## タスク

- [x] e2e skill の SKILL.md の差分の取り方を `<base>...HEAD` に揃え、理由を一言添える
- [x] gate が e2e skill へ渡す args (base branch) と食い違わないことを確かめる

## 関連

- ISSUE-102
- ISSUE-103
- ISSUE-69 (分岐後の `origin/main..HEAD` の差分に見慣れないファイルが混ざった実例を持つ)
