---
status: open
---

# fix: C.2 の「gate が既に検証済み」が指す CI が別物

## 背景

`dev-workflow:in-repo-issue` の Phase C.2 は、フォールバック経路のときだけ main の最新 CI が
成功しているかを確認する。確認しない側の根拠として、

> gate Phase 5 経由の場合は gate が既に検証済みなのでこのチェックをスキップ

と書いてある。この根拠が 2 つの意味で実態と合っていない。

### 1. gate は CI の run をどこでも確認していない

`plugins/dev-workflow/` 全体を `gh run` / `pr checks` / `--watch` で走査すると、ヒットするのは
C.2 の 1 行だけである (実測、grep rc 0)。`pre-merge-quality-gate` の SKILL.md は全 6 Phase の
どこにも run を見る手順を持たない。

gate が実際に確認するのは Phase 3 の `make ci-<area>` で、これは**ローカル実行**である。
Phase 4 は `gh pr merge` / `gh pr create` を実行するだけで、その前後に run の状態を見ない。

つまり C.2 のスキップ根拠は、**gate が文書化していない検証**を指している。読んだ人が
「gate が PR の run を見たのだろう」と補完して読める形になっており、補完の中身は人によって
変わる。

### 2. 仮に gate が PR の run を見ていたとしても、C.2 が見るのは main の run

gate Phase 5 は `gh pr merge` の成功**直後**に走る。マージによって作られる main の run は
まだ開始していない可能性がある。PR の run と main の run は別のオブジェクトなので、
片方の成功はもう片方の成功を意味しない。

### 本リポでの実害は薄い。配布先では違う

`.github/workflows/ci.yml` の trigger は `push: branches: [main]` と `pull_request:` で、
path filter は無く、4 job (leak-guard / package-shape / readme-drift / python-tests) が
どちらの trigger でも同一に走る。したがって本リポでは PR の run と main の run の内容が
一致し、取り違えても結果が変わらない。

**main 限定 job を持つプロジェクトへ配布されると意味を持つ**。デプロイやリリース系の job は
`push: branches: [main]` にしか無いことが多く、その run の成否は PR の run から分からない。

### ISSUE-14 の退行ではない

C.2 のこの記述はリポジトリ創設時から存在する。ISSUE-14 で識別子の記法を変えたときに
入り込んだものではない。

## タスク

- [ ] 「gate が既に検証済み」が何を指すのかを決める。選択肢は 2 つ以上ある
      (gate 側へ run を確認する step を足す / C.2 のスキップ根拠を実態へ書き換える /
      スキップそのものをやめる)
- [ ] main の run がまだ開始していない場合の扱いを決める (待つ / 保留して次回に回す / 見ない)
- [ ] main 限定 job を持つプロジェクトで何が変わるかを、決めた側の文書へ書く
- [ ] 決めた内容を C.2 と gate Phase 5 の片側だけに書かない。条件を両側へ literal で持つと
      ISSUE-14 が gate 側で潰したのと同型の二重管理になる

## 関連

- ISSUE-14 (この記述を発見した作業。本 Issue はその最終レビューで triage された)
- ISSUE-19 (同じ「いつ使うか」節のフォールバック契機が C.2 と条件を二重に持つ)
