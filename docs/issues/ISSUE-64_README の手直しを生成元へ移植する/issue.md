---
status: open
---

# docs: README の手直しを生成元へ移植する

## 背景

README.md を読みやすくする手直しが、生成元ではなく README.md そのものに入っていた。README.md は
`scripts/gen-readme.py` の生成物なので、そのままでは次の生成で消え、`--check` も赤くなる。

手直しした版を、このディレクトリの `ISSUE-64-readme-hand-edit.md` にそのまま保存してある。
差分をパッチではなく全文で持つのは、差分の空行 (行頭の空白 1 つ) が pre-commit の行末空白の
hook に削られてパッチとして壊れるため。生成版との差分は次で見られる。

```
diff README.md "docs/issues/ISSUE-64_README の手直しを生成元へ移植する/ISSUE-64-readme-hand-edit.md"
```

手直しは、移す先で 3 つに分かれる。

| 移す先 | 手直し |
|---|---|
| `gen-readme.py` の `HEADER` / `FOOTER` | 冒頭の紹介文、「使い方」、plugin 節と skill 節の導入文、「構造の規約」の段落分け |
| `gen-readme.py` の生成コード | plugin 表から「component 数」列を消す |
| 各 SKILL.md の frontmatter の `description` | plugin 3 本、`context-loading-mechanics`、`session-handoff`、`herdr` (日本語化)、`markdown-to-pdf` |

### 移す前に決めること

- `description` は README の表示だけでなく、skill を呼ぶかどうかの判断材料でもある。手直しで
  削られた文言がある (plugin の「個別の実行は ... を呼ぶ」、`context-loading-mechanics` の
  「予算の検査機構そのものは持たない」、`session-handoff` の「ここでは数えない」)。frontmatter へ
  そのまま移すと呼ばれ方も変わるので、README 用に短い説明を別に持つか、`description` ごと変えるかを
  先に決める
- 文面の意味が変わる手直しがある。1 つずつ採否を決める
  - `<sha>` の固定が「固定する」から「推奨する」へ弱まった
  - apm のコピーの説明から「verbatim」が消えた
  - 「Claude Code」が「Coding Agent」になった。`.claude-plugin/plugin.json` を読むのは Claude Code
  - 紹介文から「誰でも参考にできる状態にしておく」と、apm の宣言と実体の関係の説明が消えた
  - 「パッケージの root」が「各 plugin の root」になった。これは事実に近づく修正で、単体 skill の
    root は `plugin.json` を持たない (起票時に `skills/` の 7 本すべてで確認した。対照の
    `plugins/` は 3 本とも持つ)
- 「component 数」列を消すと、`gen-readme.py` の `count_components()` の使い道がなくなる。
  ISSUE-30 が扱う「component の定義が生成器と形の検査に分裂している」の生成器側が消えるので、
  ISSUE-30 の内容が変わる。どちらを先にやるかを決める

同じ手直しのうち、SKILL.md 本文の改行の詰め直しは、この Issue を起票したブランチで先にコミットした。
そのとき `web-monkey-qa` の誤字 (「行わな」) を直し、`context-loading-mechanics` の「Coding Agent」は
「Claude Code」へ戻した。中身が Claude Code で実測した挙動だからで、ユーザーの判断による。

## タスク

- [ ] `description` を README 用と呼び出しの判断用に分けるか、`description` ごと変えるかを決める
- [ ] 「component 数」列を消すかを、ISSUE-30 との順序と合わせて決める
- [ ] 意味が変わる手直しの採否を 1 つずつ決める
- [ ] 採ったものを `HEADER` / `FOOTER`、生成コード、frontmatter へ移し、`python3 scripts/gen-readme.py` で生成し直す
- [ ] 生成した README.md と `ISSUE-64-readme-hand-edit.md` の差分が、採らなかった分だけになっていることを確かめる
- [ ] 生成コードを変えたら `scripts/test_gen_readme.py` を合わせて直し、テストの manifest を更新する
- [ ] `ISSUE-64-readme-hand-edit.md` を削除する

## 関連

- ISSUE-30: 「component 数」列を消すと、こちらが扱う分裂の生成器側が消える
