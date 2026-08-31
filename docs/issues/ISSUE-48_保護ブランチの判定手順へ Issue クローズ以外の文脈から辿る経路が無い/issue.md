---
status: open
---

# fix: 保護ブランチの判定手順へ Issue クローズ以外の文脈から辿る経路が無い

## 背景

`dev-workflow:in-repo-issue` の「クローズ経路: feature PR 同梱を優先 (main 直 push を避ける)」節が、
default branch が保護されているかの判定手順を持っている。持っている中身は判定式そのものだけでなく、
classic branch protection API と repository ruleset の両方を見ること、rule を返す endpoint の選択、
空出力を「保護なし」と読まない罠、パイプへ繋ぐと `gh` の rc が消える罠までを含む。

この事実が要るのは Issue のクローズ経路を選ぶときだけではない。「保護ブランチへ直 push してよいか」を
問うあらゆる文脈が同じ判定を要る。ところが節はライフサイクル節の内側にあり、skill の description が
挙げるトリガも Issue の起票・更新・クローズ・親伝播に限られる。手順は正しいのに、Issue を扱っていない
文脈からは存在しないのと同じになる。

## 実測 (2026-08-31)

到達経路の棚卸し。`plugins/` と `.claude/skills/` の Markdown 22 ファイルを
`ruleset|protection|直 ?push` で走査し、判定手順の本体がある in-repo-issue の SKILL.md を除外した。
ヒットは 2 行 (本体側は同じ述語で 10 行)。

| 経路 | 起動トリガ | 判定手順への到達 |
| --- | --- | --- |
| in-repo-issue 自身 | Issue の起票 / 更新 / クローズ / 親伝播 | 節が本体にある |
| pre-merge-quality-gate | `gh pr create` / `gh pr merge` の直前 | 値を写さず節名で委譲 (ISSUE-41 の成果) |
| 保護ブランチへ直 push する前 | (該当する skill が無い) | 到達しない |

ヒット 2 行のうち 1 行は上表の委譲で、残る 1 行は release skill の「tag の push は ruleset に
妨げられない」。後者は判定の帰結であって手順ではないので、到達経路には数えない。

委譲の書き方そのものは正しい。gate は判定式を再掲せず節名で指しており、二重記述になっていない。
欠けているのは委譲の質ではなく、3 つめのトリガから委譲元へ入る辺である。

### 走査述語と射程の限界 (自分で組んだ述語が取りこぼした分)

上の走査は述語も射程も自分で選んだものなので、別述語と別射程で数え直した。3 つ落ちていた。

- **gate から節への参照は 1 本ではなく 3 本** (Phase 0 / Phase 2 の決定表 / Phase 3)。
  `直 ?push` を含むのは Phase 0 の 1 行だけで、残る 2 行は述語から落ちた。節名で数えれば 3 行出る
- **帰結を持つ場所は release skill だけではない**。`scripts/check-issue-closure.py` の docstring が
  「main が保護されていて直 push を選べないこのリポジトリではそれが正しい」を根拠に
  「そのまま配布しないこと」を宣言している。この検査の配布可否がその帰結に乗っている。
  `scripts/` は上の走査の射程外だった
- **射程の宣言が無かった**。走査したのは `plugins/` と `.claude/skills/` だけで、配布物にはトップ
  レベルの `skills/` も含まれる。そちらは 9 ファイルを同じ述語で走査してヒット 0 行だったので
  結論は変わらないが、射程は書かれていなければ読み手には見えない

### 置き場所を動かすと反応する機械層が 1 つある

`scripts/test_check_issue_closure.py` の `SectionReferences` が、
`` `<plugin>:<skill>` の「<節名>」節 `` という形の参照について、参照先ファイルにその節名で始まる
見出しが実在するかを検査している。母集団は `CLAUDE.md` と `plugins/**/SKILL.md` と `skills/**/SKILL.md`
で、参照先の解決は `plugins/<plugin>/skills/<skill>/SKILL.md` に固定されている。

つまり下の案は機械層に対して等価ではない。案 2 は参照先が `plugins/` 配下のままなので緑を保つが、
案 3 は節見出しが消えるとこの検査が赤くなる。見出しを残す形にすれば回避できるが、その場合は
「節が本文を持たない」状態を意図として説明する必要がある。

なお `.claude/skills/**` はこの検査の母集団に入っていないので、release skill からの参照は見ていない。

### 消費側 (dotfiles)

配布先では常時ロードされる規範が「保護ブランチへ直 push する前に classic API の 404 だけで結論するな」を
要求しており、その参照先が判定手順の写しを持っている。当初は報告として受け取ったものだったが、
こちらでも中身を読んで照合した。classic 404 では足りないこと、list endpoint が `rules` を持たないこと、
`/rules/branches/<default-branch>` を使うこと、判定式が `pull_request` の有無であること、罠 3 つ
(空出力・branch 名の literal・パイプで消える rc) の 6 点すべてが一致し、現時点で drift は無い。
写しであることと、その重複が意図的である理由 (トリガが交わらない) も向こうの本文に明記されている。

drift は「これから起きうる」ものであって、現に起きている状態ではない。案 1 の代償はその見込みで
評価する。ただしこのリポジトリ内には既に古びた写しが 1 つある。ISSUE-41 の plan が持つコマンド片は
list endpoint から `rules` を取る形で、SKILL.md が「動かない」と書いている当のものにあたる
(同 Issue の本文が訂正と canonical を記録しているので、歴史的成果物として残す判断)。

## 検討の起点

決めるのは置き場所。候補は 3 つある。

1. 現状維持。この repo には PR ワークフロー経由の経路があり、実害は消費側にしか出ていない。
   代償は写しが 2 リポジトリに分かれて drift しうること
2. 判定手順を `plugins/dev-workflow/` 配下の独立した skill へ切り出し、in-repo-issue と gate が
   参照する。3 つめのトリガから発火でき、消費側も skill 名で参照できるようになる。代償は配布表面が
   1 つ増え、消費側の pin 更新がもう一度要ること。`SectionReferences` は緑のまま
3. in-repo-issue 同梱の reference ファイルへ移し、SKILL.md は参照だけ持つ。トリガは増えないので
   到達不能は解決しないが、将来 2 へ移すときの受け皿になる。節見出しを残さないと
   `SectionReferences` が赤くなる

判断の軸は消費文脈の数。現時点で 3 つあるが、うち 2 つ (Issue クローズ / PR 作成前) は同じ PR
ワークフロー上にあり既に委譲で繋がっている。独立しているのは 3 つめだけで、しかもそれは
この repo の外にある。切り出しは「消費者が 3 つある」ではなく「独立した消費者が repo の外に 1 つ
ある」ことと釣り合うかで決める。

## タスク

- [ ] 判定手順の消費文脈を数え直す。上の 3 つは走査 2 回の結果なので、述語と射程を変えても
      増えないことを確かめる
- [ ] 置き場所を 3 案から決める。切り出す場合は、配布表面が 1 つ増える代償と釣り合う根拠を PR で
      説明する
- [ ] 切り出す場合、in-repo-issue と pre-merge-quality-gate の参照を値の再掲なしで張り替える。
      gate 側の参照は 3 箇所 (Phase 0 / Phase 2 / Phase 3) で、1 箇所だけ直すと残りが古い節名を
      指したまま `SectionReferences` の緑をすり抜ける
- [ ] 張り替えた側を CLAUDE.md の 4 点 (消した列挙が部分集合でないか / 参照先が本当に持つか /
      宣言の射程が実態より広くないか / 注記自身が値を再掲していないか) で検算する
- [ ] `scripts/check-issue-closure.py` の docstring が判定の帰結に乗っている件を、置き場所の決定と
      整合させる。配布可否の宣言がその帰結を根拠にしている
- [ ] 消費側が持つ写しをどう扱うかを決める。切り出さないなら写しは意図的な重複のまま残るので、
      その判断を上流側にも記録する

## 関連

- `plugins/dev-workflow/skills/in-repo-issue/SKILL.md` — 「クローズ経路: feature PR 同梱を優先」節が
  判定手順の実体を持つ
- `plugins/dev-workflow/skills/pre-merge-quality-gate/SKILL.md` — Phase 0 / Phase 2 / Phase 3 の
  3 箇所がその節を名指しする
- `scripts/test_check_issue_closure.py` — `SectionReferences` が節見出しの実在を検査する。置き場所を
  動かしたときに反応する唯一の機械層
- `scripts/check-issue-closure.py` — docstring が「main が保護されている」ことを根拠に配布可否を宣言する
- ISSUE-41: クローズ同梱の判定を促す入口がゲートのどのフェーズにも無い。本 Issue はその委譲先そのものの
  置き場所を扱う
- ISSUE-36: skill と plugin が消費側へ要求する取り付けを棚卸しする。案 2 を採ると配布表面が 1 つ増える
