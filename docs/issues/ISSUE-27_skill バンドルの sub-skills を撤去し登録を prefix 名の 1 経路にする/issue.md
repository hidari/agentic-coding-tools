---
status: open
---

# refactor: skill バンドルの sub-skills を撤去し登録を prefix 名の 1 経路にする

## 背景

`security-blue-red-team` と `web-monkey-qa` の 2 bundle は、同じ command と agent が prefix
有無の 2 通りで system prompt に載っている。出所は Claude Code ではなく apm の deploy 規則に
ある。apm は root `SKILL.md` を持つパッケージを verbatim コピーし、`.claude-plugin/` を持つ
パッケージの `agents/` と `commands/` を flat 分解する。両方を持つパッケージは両方の規則が
走るので、同じ内容が 2 箇所に置かれ、Claude Code がそれぞれを別経路で登録する。apm 側に
これを抑止するノブは無い。

利用側 (dotfiles リポジトリの in-repo Issue「CLAUDE.md を rules と skill へ分割し常時ロード量を
減らす」) で 7 通りの案を実測して比較し、sub-skills を撤去して bundle を root `SKILL.md` +
command + agent だけにする案を採ることが決まっている。本 Issue はその上流側の担当分で、
flat 側 deploy 先の後始末は利用側が持つ。

残す側を prefix 経路にしたのは、そちらだけが plugin の契約を満たしているため。
`${CLAUDE_PLUGIN_ROOT}` が解決し `schemas/` が届くのは verbatim コピー経由の側だけで、
flat 側にはその経路が無い。

### 撤去にあたって確認済みの事実

- sub-skill 4 個のうち 2 個 (`security-vulnerability-assessment` / `monkey-qa`) は、同名の
  command が prefix 名前空間で勝つため一度も system prompt に載っていない
- `fingerprint` の算出規則と `DO NOT` の責務境界は agent 側が canonical なので、sub-skill を
  消しても失われない
- 一方「使われない条件」(他 skill への振り分け案内) は sub-skill にしか無い
- `/monkey-qa` は `Skill` tool で sub-skill を起動する薄い entry point で、dispatcher の実体が
  撤去対象の側にある
- `/security-redteam` の Purple Team 連鎖も `Skill` tool で撤去対象の `security-blue-team` を
  起動している

## タスク

- [ ] 2 bundle から sub-skills を撤去する (`security-blue-red-team/skills/` の 3 個と
      `web-monkey-qa/skills/` の 1 個)。`plugin.json` の `skills` 宣言も外す。root `SKILL.md` は
      残す (verbatim コピーが `schemas/` の唯一の deploy 経路のため)
- [ ] `/monkey-qa` に dispatcher 本体を取り込む。取り込んだ側で `${CLAUDE_PLUGIN_ROOT}` 参照が
      解決することを確かめる
- [ ] 自然言語からの起動語彙を、撤去する sub-skill の `description` から command の
      `description` へ凝縮して移す。責務説明と production 拒否は agent 側が canonical なので
      再掲しない
- [ ] 「使われない条件」を command 本文へ移す。本文は常時ロードされないので、移設で常時層の
      バイト数は増えない
- [ ] bare 名の agent dispatch を prefix 名へ揃える。`web-monkey-qa` は既に prefix 名なので
      対象外
- [ ] 撤去した skill への参照を残さない。対象は `web-monkey-qa/README.md`、
      `web-monkey-qa/commands/monkey-qa.md`、`security-blue-red-team/SKILL.md` (description と
      component 表と schema 節)、`security-blue-red-team/commands/security-redteam.md` の
      Purple Team 連鎖
- [ ] `${CLAUDE_PLUGIN_ROOT}/schemas/<name>` の参照が撤去後も残る層 (agents と commands) から
      解決することを確かめる
- [ ] `python3 scripts/gen-readme.py` で README を再生成する。パッケージ一覧の表は各 SKILL.md の
      frontmatter から読むので、撤去すると表が変わる
- [ ] `pre-commit run --all-files` を通す。CI より hook が多いのはローカル側なので、マージ前に
      ローカルで回す

## 関連

- 実測と案の比較、および採用した案の削減幅は dotfiles リポジトリの in-repo Issue
  「CLAUDE.md を rules と skill へ分割し常時ロード量を減らす」が canonical。本 Issue はその
  派生で、上流側の変更だけを持つ
- flat 分解を抑止するノブが無いこと自体は apm の設計判断なので、必要なら上流へ報告する余地が
  ある。ノブが入れば利用側の後始末は不要になるが、本 Issue は apm の変更を待たずに閉じられる
