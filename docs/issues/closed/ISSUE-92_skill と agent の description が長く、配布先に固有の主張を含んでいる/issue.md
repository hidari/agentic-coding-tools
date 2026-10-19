---
status: closed
---

# docs: skill と agent の description が長く、配布先に固有の主張を含んでいる

## 背景

消費側の棚卸しから、skill の一覧がコンテキストの予算を超え、呼ばれることの少ない skill の説明が名前だけに落ちている、という報告があった。このリポジトリが配る skill と agent の description には長いものがあり、予算を圧迫している側にあたる。

description の中には、特定のプロジェクトの事情を一般の事実のように書いたものもある。例えば `plugins/dev-workflow/skills/e2e-scenario-impact-check/SKILL.md` の description は、staging の E2E が唯一の自動検出網で、ローカルの E2E は CI で動かない、と断定している。配布物の description は配布先の判断材料になるので、汎用の記述であるべき。

ISSUE-64 は、description を README 用と呼び出しの判断用に分けるか、description ごと変えるかを決めるタスクを持っている。description を書き換える作業はその決定に依存するので、ISSUE-64 と順序を合わせる。

## タスク

- [x] ISSUE-64 の description の扱いを決めるタスクと順序を合わせる
- [x] 配る skill と agent の description の長さを数え、短くする対象と方針を決める
- [x] description から、配布先に固有の主張を外す (e2e-scenario-impact-check を含む)
- [x] 書き換えたら `python3 scripts/gen-readme.py` で README を生成し直す

## 結果

方針 (ユーザーの判断): `description` は README 用に分けず、1 つのまま短く書き換える。README は今までどおり frontmatter から生成する。

公式の文書 (2026-10-19 に確認) によれば、skill の一覧はコンテキストの 1% が上限で、超えると呼ばれる回数の少ない skill から説明を落として名前だけにする。1 つの説明は 1,536 字で切られる。agent の説明は別枠で、合計が 15,000 トークンを超えると警告が出る (読み込みは止まらない)。

- 対象は、配る SKILL.md、agent、command の `description` 25 本。目安を 200 字前後、上限を 300 字にした。何をするかと、いつ使うかを先に書く
- 削った説明のうち本文に無かったものは本文へ移した (e2e-scenario-impact-check の `getByLabel` / `getByText`)
- 25 本のうち変えたのは 23 本 (security-blue-red-team と web-monkey-qa の plugin の SKILL.md は変えていない)。合計は 8,300 字から 4,691 字になった (frontmatter の行の値を引用符ごと数えた字数)。いちばん長かった monkey-explorer-agent は 1,164 字から 276 字になった。jev-lint-curated だけは 153 字から 179 字に増えた。何を確かめる skill かが書かれていなかったので足した
- 配布先に固有の主張は、description から外し、本文では条件付きの書き方にした。e2e-scenario-impact-check と pre-merge-quality-gate にあった「staging E2E が唯一の自動検出網」「ローカル E2E は CI で動かない」「staging デプロイ後の E2E 実行まで爆発しない」は、そういう構成のプロジェクトでは、という書き方に直した
- 書き直しは 4 つのグループに分け、グループごとに案を作る役と、別の目で確かめる役を回した。確かめる役が直したのは 5 本で、主なものは次のとおり
  - commit-and-pr-message: 「言語を問わず」を戻した
  - in-repo-issue: マージ後の起動を「Closes 行があるときだけ」と読める書き方から戻した
  - pre-merge-quality-gate: PR を作成する経路を「マージする」とだけ書いていたのを直した
- monkey-explorer-agent の旧 `description` は値の途中に `: ` を含み、YAML として読めなかった (YAML パーサで確認)。書き直しでこれも解消した。変えたあとの frontmatter は、YAML パーサで全部読めることを確かめた

仕上げのレビューで、削りすぎたきっかけの言葉を 4 本で戻した。security-vulnerability-assessment の「security headers 監査」、commit-and-pr-message の「コメントして」、e2e-scenario-impact-check の a11y・フォーム・ルーティングの変更、red-team-agent が Layer 1 と 2 (受動の検査) にも使われること。

## 関連

- ISSUE-64
- ISSUE-91
