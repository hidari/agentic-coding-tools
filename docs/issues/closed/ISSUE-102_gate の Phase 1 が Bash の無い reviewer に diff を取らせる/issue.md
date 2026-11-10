---
status: closed
---

# fix: gate の Phase 1 が Bash の無い reviewer に diff を取らせる

## 背景

出所は dotfiles のセッションから受けた skill の欠陥報告 (受け箱経由、2026-11-10)。

`dev-workflow:pre-merge-quality-gate` の Phase 1 の 2 番目は、`feature-dev:code-reviewer` へ渡す prompt に「全 diff の取得方法 (`git diff <base>..HEAD`)」を入れるよう指示している。しかしこの agent 型の道具に Bash は無い。

- 報告 (実測): 指示どおりに起動すると、reviewer は `git diff` を実行できない。diff をファイルへ書き出して Read させる形が要る
- 本リポジトリでの確認: 2026-11-10 のセッションで Agent ツールが示す agent 型の一覧では、`feature-dev:code-reviewer` の道具は Glob / Grep / LS / Read / NotebookRead / WebFetch / TodoWrite / WebSearch / KillShell / BashOutput で、Bash を含まない。起動して挙動を見たわけではない

追記 (2026-11-10、dotfiles のセッションから追加で受けた報告): 別のリポジトリ 2 つから同じ報告が届いた。

- 報告 (実測): 差分を書いた diff ファイルのパスを code-reviewer に渡しても読めなかった。置き場は git-ignore されたディレクトリ (`.cache/`) の下だった。2 回とも作業ツリーの一部を Read と Grep で読むだけになり、報告は「部分的なレビュー」と自己申告で返ったので見落としに気づけた。報告者の提案: (a) か (b) のどちらでも、実際に何を何件読んだかを報告させる
- 報告 (提案): diff を prompt に埋め込む形も候補
- dotfiles 側には「diff はファイルへ書き出して Read させる」で通った記録がある。読めなかった原因が置き場の違いか、検索ツールの ignore の扱いかは切り分けていない。(a) を採るなら、ignore 配下のファイルが code-reviewer から読めるかを先に確かめる

Bash が無い reviewer は、取れない diff の代わりに変更ファイルの全文を Read して読むことがある。そうなると削除された行は見えないので、「消した内容が正しいか」という観点が黙って落ちる。

## 決定 (2026-11-10)

(b) を採った。Hidari の裁定で ISSUE-103 とまとめ、コードレビューのレーンを `general-purpose` にした。Phase 1 の `Agent` tool で起動するレーンと反証役には、何を何件読んだかを報告させる。プロジェクトの CLAUDE.md が `feature-dev:code-reviewer` を指定しているときに限り、(a) の形 (差分を書いたファイルの絶対パスを渡す) を残した。書き出し先は skill で固定せず、プロジェクトの CLAUDE.md が定める書き捨ての置き場に従い、定めが無ければ ignore されることを確かめたディレクトリにした。固定するとプロジェクトの規約と食い違い、リポジトリの外の置き場は読めるかを測っていない。

### 実測 (2026-11-10)

ignore された `.cache/` の下に差分 (75 行、先頭が `+` の行 50) を書き、2 つの agent 型に同じ問いを出して自己申告させた。

| 項目 | `feature-dev:code-reviewer` | `general-purpose` |
|---|---|---|
| 持っている道具 | Read / Glob / Grep / WebFetch / WebSearch / TaskStop ほか。Bash は無い | Bash / Read / Edit / Write / Agent / Skill ほか。Glob と Grep は無い |
| `git diff main..HEAD` | 実行できない | 75 行 / 50 行 |
| 差分のファイルを絶対パスで Read | 75 行 / 50 行 | 75 行 / 50 行 |
| 同じファイルを Grep / Glob | どちらもヒット | 道具が無い |

報告にあった「ignore 配下のファイルが code-reviewer から読めなかった」は、絶対パスで渡す形では再現しなかった。渡し方 (相対パスか、検索経由で探させたか) の違いかもしれないが、切り分けていない。SKILL.md には、指定されたときは読めた行数を報告させて突き合わせる形で書いた。

Phase 1 の他の項目も見た。`simplify` は親が `Skill` tool で起動するので、起動先の道具の問題は起きない。boy-scout sweep と PUBLIC 漏洩スイープと反証役は `general-purpose` で、prompt は Read と Bash で足りる指示だけを持ち、Glob と Grep を前提にしていない。

## タスク

- [x] 手当ての形を決める。候補は 2 つ: (a) 親が diff をファイルへ書き出し、そのパスを prompt で渡して Read させる (b) diff を見る観点だけ Bash を持つ agent 型 (general-purpose) へ移す。(a) は書き出し先を配布先でどこに置くかも決める (本リポジトリの `.cache/` の規約は配布先の規約ではない)
- [x] 変更前と変更後で reviewer に diff が実際に届くかを、agent に受け取った道具と読めた diff の範囲を自己申告させて確かめる。変更前を対照に並べる
- [x] Phase 1 の他の項目 (simplify への args、boy-scout sweep の prompt) にも、起動先が持たない道具を前提にした指示が無いかを見る

## 関連

- ISSUE-47: gate の Phase 1 の記述が実際の起動と違う。同じ Phase 1 の同じ項目を触る
- ISSUE-99: gate の手順に足す項目がある。Phase 1 の文面が衝突しうる
- ISSUE-103: gate のレビューの形を観点ごとの reviewer と反証役へ組み替える提案。組み替えるなら reviewer の agent 型の選び方もそちらで決まる
