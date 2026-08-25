---
status: open
---

# fix: monkey-explorer-agent が production ガードの写しを持たない

## 背景

`web-monkey-qa` の production ガード (匿名 read-only への強制) は dispatcher 側の 1 層しか
ない。`agents/monkey-explorer-agent.md` の description は
"Refuses nothing itself — the dispatcher gates production" と明示しており、`READ_ONLY` と
空の `SUBMIT_ALLOWLIST` は引数として渡されるだけである。

`web-monkey-qa:monkey-explorer-agent` は agent type として独立に dispatch できるので、
**dispatcher を経由しない呼び出しでは環境ガードが一度も走らない**。渡されなかった引数の
既定は「production ではない」側に倒れる。

グローバル CLAUDE.md は「攻撃者が使える面を最小に保ち防御を 1 層に頼らない」を MUST として
いる。1 層しかないこと自体は ISSUE-27 の撤去より前から続いているが、ガードを sub-skill から
command へ移して「ここが canonical」と書き直した時点が、層の数に気づくべき地点だった。

### 隣の bundle が正しい深さの実例を持っている

同じリポジトリの `security-blue-red-team` は、同じ production gate を agent 側にも持って
いる。`agents/red-team-agent.md` と `agents/blue-team-agent.md` の Phase 0 が profile を
自分で読んで `kind == production` を拒否し、Safety constraints は "hard abort at Phase 0.
No exceptions." と書いている。dispatcher 側の事前検証と合わせて二層になっている。

つまりこのリポジトリには既に「宣言層 (dispatcher) と手続き層 (agent) を独立に効かせる」
形の実装があり、monkey 側だけがその形になっていない。

### 二層にするときの注意

explorer は profile のパス (`MONKEY_PROFILE`) を引数で受け取っているので、agent 側でも
profile を読み直して `environment.kind` を判定できる。ただし引数が欠けた呼び出しでは
profile 自体が届かないため、「profile が無い場合は探索を拒否する」まで決めないと層が
成立しない。security 側の agent が profile 必須にしているのと同じ形になる。

## タスク

- [ ] `agents/monkey-explorer-agent.md` に Phase 0 相当の production gate を足す。profile を
      自分で読み、`environment.kind == production` なら fill / submit / 認証へ到達しない
      モードを自分で強制する
- [ ] 引数が欠けた呼び出し (profile が届かない) の扱いを決める。安全側に倒すなら拒否
- [ ] description の "Refuses nothing itself" を実態に合わせて直す
- [ ] `commands/monkey-qa.md` の実行フロー 2 が canonical であることは維持する。agent 側は
      再定義ではなく独立した歯止めとして書く (security 側の agent と同じ関係)
- [ ] 二層になっていることを、dispatcher を経由しない呼び出しで実測する

## 関連

- ISSUE-27: production ガードを sub-skill から command へ移した PR。1 層であることはそこで
  可視化された
