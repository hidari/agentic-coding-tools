# 後から plugin へ昇格する

初版は `skills/tooling/network-debug-mac/` の単体 skill。agents / commands / hooks /
MCP を持つ必要が出た時点で plugin へ移す。

## 昇格が必要になる条件

そのどれかに当たるまで移さない。

- キャプチャの採取と解析を別コンテキストで走らせたい (agents)
- `/netdebug-capture` のように副作用のある操作を明示的に叩きたい (commands)
- 承認プロンプトの制御を hooks でやりたい
- MCP サーバを同梱したい

レイヤーごとの文書が増えただけでは昇格の理由にならない。それは
`references/` を足すだけで済む。

## 差分

`scripts/check-package-shape.py` の規約 1 が「plugin パッケージの root に
SKILL.md がある」を要求しているので、**既存ファイルの移動は要らない。**
`plugins/network-debug-mac/` へディレクトリごと移し、2 ファイル足すだけ。

```
plugins/network-debug-mac/
├── .claude-plugin/
│   └── plugin.json      <- 追加
├── apm.yml              <- 追加
├── SKILL.md             <- そのまま (入口の案内のみに書き換える)
├── references/          <- そのまま
└── skills/              <- component を作るならここ
    └── <component>/SKILL.md
```

`.claude-plugin/plugin.json`:

```json
{
  "name": "network-debug-mac",
  "version": "0.1.0",
  "description": "TODO: apm.yml と同じ文言にする",
  "author": { "name": "Hidari" },
  "skills": ["./skills"]
}
```

`apm.yml`:

```yaml
name: network-debug-mac
version: 0.1.0
description: TODO: plugin.json と同じ文言にする
author: Hidari
type: hybrid
```

## 昇格時に効く規約

`check-package-shape.py` が機械検証する。手で気をつけるのではなく走らせる。

- `plugin.json` の `name` はディレクトリ名と一致させる
- root SKILL.md の `name` を内部 `skills/` の `name` と重複させない
  (衝突すると skills directory loader が plugin 側の skill を skip する)
- `skills` 宣言を `["./"]` にしない (apm が無限再帰して install が落ちる)
- `author` にメールアドレスを入れない

## component skill の命名

component を作るなら、その名前は**単体で意味が通り、かつ全体で一意**である
必要がある。`http` や `capture` のような一般名は使えない。
`dev-workflow` の `git-branch-switcher` / `in-repo-issue` が満たしている水準。

この制約を満たせない分割なら、それは component skill にすべきでない分割である。
