---
status: closed
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

- [x] `agents/monkey-explorer-agent.md` に Phase 0 相当の production gate を足す。profile を
      自分で読み、`environment.kind == production` なら fill / submit / 認証へ到達しない
      モードを自分で強制する
- [x] 引数が欠けた呼び出し (profile が届かない) の扱いを決める。安全側に倒すなら拒否
- [x] description の "Refuses nothing itself" を実態に合わせて直す
- [x] `commands/monkey-qa.md` の実行フロー 2 が canonical であることは維持する。agent 側は
      再定義ではなく独立した歯止めとして書く (security 側の agent と同じ関係)
- [x] 二層になっていることを、dispatcher を経由しない呼び出しで実測する

## 実測 (2026-08-28)

隔離コピーへ 6 本の probe を通した。gate の有無だけを変数にするため、profile は
`environment.kind` の 1 行だけが違う 2 種類を用意し、他の停止経路 (BASE_URL 欠落、
`AUTH_RECIPE` と submit 禁止の衝突、`OUTPUT_DIR` と profile の不一致) は塞いである。

| probe | 版 | kind | READ_ONLY の最終値 | SUBMIT_ALLOWLIST の最終値 | fill |
| --- | --- | --- | --- | --- | --- |
| D | gate あり | production | `true` (条項による強制) | `[]` (強制) | 不可 |
| E | gate なし | production | `true` (推論による補い) | `[]` (推論) | 不可 |
| F | gate なし・字面厳守 | production | `false` | `["検索"]` | 可 |
| B | gate あり | local | `false` | `["検索"]` | 可 (gate は素通し) |

D と F の差が gate の効果である。B は gate が local で発動しないことの陰性対照。

E は gate 無しでも安全側へ倒れたが、E 自身が「指示ファイルが明文で授権した動作ではなく、
前提が崩れたときの推論による補い」と述べ、独立に「agent 側にも条項を置くべき」と結論した。
F はその推論を止めただけで危険側へ倒れる。**gate 不在で安全なのは読み手の性質に依存する
ためであって、指示の保証ではない。**

初回の probe (A / C) は対照が汚染されていた。BASE_URL を未設定にし submit を含む recipe を
渡していたため、gate が無くても別経路で止まる。C はそれを自分で切り分けて報告した。
プローブは検証対象と同じ構造上の位置に置き、測りたい変数以外の経路を先に塞ぐこと。

### この実測が届かない範囲

試したのは隔離コピーであって、登録済みの agent type ではない。probe 3 本が独立に、
配布側の description が `Refuses nothing itself` のままである点を指摘した。配布側へ
gate が届くのは release 後で、その経路を見る層は dotfiles の ISSUE-53 が持つ。

## 関連

- ISSUE-27: production ガードを sub-skill から command へ移した PR。1 層であることはそこで
  可視化された
- dotfiles の ISSUE-53: 配布先の加入状況と写しの drift を見る層。この Issue の実測が届かない
  配布経路をそちらが持つ
