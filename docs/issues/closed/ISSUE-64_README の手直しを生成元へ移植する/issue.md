---
status: closed
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

追記 (2026-10-01): 手直し版の土台は a126db2 の時点の README で、今の README より古い。その後、ISSUE-58 の 66231ad で dev-workflow の description が書き換わり、ISSUE-65 の 257add2 で jev-lint-curated の行が加わった。この 2 点は手直しではなく土台の差なので、上の diff にも現れる。

手直しは、移す先で 3 つに分かれる。

| 移す先 | 手直し |
|---|---|
| `gen-readme.py` の `HEADER` / `FOOTER` | 冒頭の紹介文、「使い方」、plugin 節と skill 節の導入文、「構造の規約」の段落分け |
| `gen-readme.py` の生成コード | plugin 表から「component 数」列を消す |
| 各 SKILL.md の frontmatter の `description` | plugin 3 本、`context-loading-mechanics`、`session-handoff`、`herdr` (日本語化)、`markdown-to-pdf` |

追記 (2026-10-16): ISSUE-89 で `herdr` の skill を撤去したので、上の表の `herdr` は移す先が無い。手直し版の `herdr` の行は採らなかった分として扱う。

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

追記 (2026-10-19): ISSUE-92 で、`description` は README 用に分けず、`description` ごと短く書き換えると決めた (ユーザーの判断)。README は引き続き frontmatter から生成する。ISSUE-92 で配布物の `description` の大半を書き直したので、上の表で frontmatter の `description` へ移すことにしていた手直しのうち、`dev-workflow`、`context-loading-mechanics`、`session-handoff`、`markdown-to-pdf` の行は ISSUE-92 の書き直しで置き換わったものとして扱う。`security-blue-red-team` と `web-monkey-qa` の plugin の `description` は ISSUE-92 で変えていないので、この 2 行は引き続き採否を決める対象に残る。

## タスク

- [x] `description` を README 用と呼び出しの判断用に分けるか、`description` ごと変えるかを決める
- [x] 「component 数」列を消すかを、ISSUE-30 との順序と合わせて決める
- [x] 意味が変わる手直しの採否を 1 つずつ決める
- [x] 採ったものを `HEADER` / `FOOTER`、生成コード、frontmatter へ移し、`python3 scripts/gen-readme.py` で生成し直す
- [x] 生成した README.md と `ISSUE-64-readme-hand-edit.md` の差分が、採らなかった分だけになっていることを確かめる (背景の追記にある土台の差 2 点と、ISSUE-92 で置き換わった `description` の行を除いて比べる)
- [x] 生成コードを変えたら `scripts/test_gen_readme.py` を合わせて直し、テストの manifest を更新する
- [x] `ISSUE-64-readme-hand-edit.md` を削除する

## 結果

採否はどれもユーザーの判断による。

- 「component 数」列は消し、使い道の無くなった `count_components()` とそのテスト 7 件も消した。この Issue を先に進め、ISSUE-30 は `check-package-shape.py` の側の問題だけを持つ形に書き換えた
- 意味が変わる手直しは 4 つとも採った。紹介文を短くする、`<sha>` の固定を「推奨する」にする、「verbatim」を消す、「Claude Code」を「Coding Agent」にする。「パッケージの root」を「各 plugin の root」にする直しも採った
- `security-blue-red-team` と `web-monkey-qa` の plugin の `description` は手直し版を採った。コマンド名の案内は消えたが、どちらの本文にもコマンド名は残っている
- plugin 節の導入文は、手直し版の「複数のコンポーネント、skill、agent をまとめたもの」が component と skill・agent を並べていたので、「skill・agent・command をまとめたパッケージ」に直して採った。「skill と agent は」も戻した
- 段落分けと、文の途中の改行を詰める手直しは採った。全角と半角の間の空白 (「自作skill」「1つのplugin」など) は、リポジトリのほかの文面に合わせて空白を入れた
- 表のテストは、行が「パスと frontmatter の description の 2 列」であることを pin する形にした。列を足し戻す、plugin を 1 つ落とす、説明を空にする、の 3 つの変異でどれも赤くなることを確かめた

生成した README.md と手直し版の差分は、空白を揃えた 3 行、plugin 節の導入文、ISSUE-92 で置き換わった `description` の行、撤去した `herdr` の行、土台の差 (`jev-lint-curated` の行の追加) だけになった。

## 関連

- ISSUE-30: 「component 数」列を消すと、こちらが扱う分裂の生成器側が消える
- ISSUE-89: `herdr` の skill を撤去した
