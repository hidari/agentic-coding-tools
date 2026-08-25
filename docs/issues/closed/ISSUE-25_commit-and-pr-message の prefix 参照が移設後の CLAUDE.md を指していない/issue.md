---
status: closed
---

# commit-and-pr-message の prefix 参照が移設後の CLAUDE.md を指していない

## 背景

skill `dev-workflow:commit-and-pr-message` は prefix と `(wip)` の canonical を 2 箇所で
参照しているが、どちらも現在のグローバル CLAUDE.md の構造と一致しない。

| 箇所 | 現在の記述 | 実態 |
|---|---|---|
| ワークフロー Phase A | グローバル CLAUDE.md の「[MUST] コミットメッセージ」節が canonical | その節名は存在しない |
| 関連 CLAUDE.md ルール | `~/.claude/CLAUDE.md`: コミットメッセージ節 (prefix と `(wip)`) | prefix 一覧は CLAUDE.md に無い |

ずれは 2 段階で起きた。

1. 2026-08-19 のカテゴリ再編で節名が「コミットメッセージのプレフィックスと本文の渡し方」へ
   変わった。この時点で節名参照が rot した
2. 2026-08-24 の常時ロード量削減で、prefix 一覧そのものが
   `~/.claude/references/git-workflow.md` へ移った。CLAUDE.md 側に残ったのは
   references を名指しする 1 行だけになった

skill 本文は「prefix 一覧や `Closes` 書式をここに写経せず所在だけを示す」と宣言しており、
この設計自体は正しい。壊れているのは所在の指し方である。

## 検査が届かない理由

参照元と参照先が別リポジトリにある。グローバル CLAUDE.md 側には
指示ファイルどうしの参照の実在を見る検査があるが、母集団は同一リポジトリ内に閉じている。
本リポジトリ側にも、配布先の設定ファイルを参照する記述を検査する仕組みは無い。

リポジトリを越える参照は、どちらの検査からも見えない位置にある。

## 洗い出しの結果

グローバル設定を指す越境参照を全 skill / plugin から拾うと 15 件あった
(検索式は `グローバル CLAUDE.md` / `~/.claude/CLAUDE.md` / `~/.claude/references` /
`~/.claude/rules` の 4 つ、本 Issue 自身の記述は除く)。

| 参照元 | 件数 | 判定 |
|---|---|---|
| commit-and-pr-message | 2 | rot。実在しない節名と、移設済みの内容を指す |
| commit-and-pr-message | 2 | 健全。「置き場のルール」「push ルール」と節名を指さずに書いている |
| retrospective-codify | 4 | 健全。うち 2 件の「ツール」節は実在の節ではなく例示の中の架空の節名 |
| pre-merge-quality-gate | 1 | 健全。逐語引用で、半角スペースの有無の表記ゆれのみ |
| context-loading-mechanics | 5 | 健全。`~/.claude/rules/` の機構の説明でパスだけを指す |
| e2e-scenario-impact-check | 1 | 健全。節見出しのみ |

**rot したのは節名を literal で名指しした 2 件だけだった。** 節名を出さずに
「push ルール」「置き場のルール」と役割で書いた参照は、再編を越えて生き残っている。

越境参照そのものを禁じるのではなく、literal な節名で指さないことが手当てになる。
ただしこれは検査に落ちない性質の規約なので、Red flags へ書く形は採らず、
今回は実所在へ直すだけに留めた。

## タスク

- [x] Phase A の canonical 参照を references の実所在へ更新する
- [x] 「関連 CLAUDE.md ルール」節の対応を更新する
- [x] 同じ形の越境参照が skill 群に他に無いか洗い出す
      (15 件を全件判定。rot は 2 件で、どちらも節名を literal で名指ししていた)

## 関連

- 移設の経緯と判断は dotfiles の Issue 36 (CLAUDE.md を rules と skill へ分割し常時
  ロード量を減らす) が持つ
