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

つまり下の案は機械層に対して等価ではない。案 2 は参照先が `plugins/` 配下のままなので
`test_referenced_sections_exist` の緑を保つ。ただし同じクラスの `test_gate_names_the_bundling_skill`
が gate の本文に `dev-workflow:in-repo-issue` が現れることを hard pin しているので、gate の参照を
すべて別 skill へ向けるとこちらが赤くなる。案 3 は節見出しが消えると
`test_referenced_sections_exist` が赤くなる。見出しを残す形にすれば回避できるが、その場合は
「節が本文を持たない」状態を意図として説明する必要がある。

母集団に入っていないのは `.claude/skills/**` と `docs/issues/**` の 2 つである。前者のせいで
release skill からの参照は見ていない。後者には本 Issue 自身の参照が 1 本ある (「## 背景」冒頭が
同じ形で節を名指ししている) ので、節を動かす変更はこの issue.md の参照を無検査のまま壊す。

### 消費側 (dotfiles)

配布先では常時ロードされる規範が「保護ブランチへ直 push する前に classic API の 404 だけで結論するな」を
要求しており、その参照先が判定手順の写しを持っている。当初は報告として受け取ったものだったが、
こちらでも中身を読んで照合した。classic 404 では足りないこと、list endpoint が `rules` を持たないこと、
`/rules/branches/<default-branch>` を使うこと、判定式が `pull_request` の有無であること、罠 3 つ
(空出力・branch 名の literal・パイプで消える rc) の 6 点すべてが一致し、現時点で drift は無い。
写しであることと、その重複が意図的である理由 (トリガが交わらない) も向こうの本文に明記されている。
向こうはこの修正自体を Issue に紐づけず `dotfiles の PR #190` で済ませている。参照の相手は
主題の近い open Issue 2 本にした (`## 関連` 参照)。参照を入れた時点では片方向だったが、
向こうの PR #193 (`b892a17`, 2026-09-01) が dotfiles の ISSUE-50 と ISSUE-53 の両方へ
本 Issue への参照を入れたので、これで双方向になった。`b892a17` 以降の変化は追跡していない。

drift は「これから起きうる」ものであって、現に起きている状態ではない。案 1 の代償はその見込みで
評価する。ただしこのリポジトリ内には既に古びた写しが 2 つある。リポジトリ全体の `*.md` と `*.py` を
`rulesets` で走査してヒット 9 件、うち古い版を持つのは ISSUE-41 の plan と spec だった。plan が持つ
コマンド片は list endpoint から `rules` を取る形で SKILL.md が「動かない」と書いている当のものにあたり、
さらに branch 名を literal で書いていて同 SKILL.md の「branch 名を literal で書かないこと」にも反する
(2 軸で古い)。spec は同じ判定式を散文で持つ。どちらも ISSUE-41 の本文が訂正と canonical を記録して
いるので、歴史的成果物として残す判断。

### 追加の実測 (2026-09-01): 消費側の機械層は逆向きに効く

dotfiles の `config-guard` が持つ `instruction_refs` を読んだ。抽出の母集団は `SOURCE_GLOBS` の
3 本で、抽出関数は 2 つとも `~/.claude/` 接頭辞に錨を打っている (`extract_home_refs` は
`startswith(HOME_PREFIX)` で絞り、`_HEADING_REF` は同じ接頭辞を先頭に固定した regex)。
`<plugin>:<skill>` はどちらの母集団にも入らない。

射程はこの 1 モジュール。`config-guard` が登録する検査を全部当たったわけではないので、
「向こうに skill 名参照を見る層が 1 つも無い」とまでは言わない。言えるのは、パス参照と見出し
参照の実在を見ているこのモジュールが skill 名を見ていないことである。

つまり案 2 で切り出して「この規範は `<plugin>:<skill>` へ委譲する」と消費側に書かせても、
少なくともこのモジュールは委譲先の実在を見ない。こちら側は節見出しの実在まで検査するので、
同じ「委譲を書く」行為に対する検証力が repo をまたぐと落ちる。

しかもこれは将来書かせる委譲の話ではない。消費側の `home/.claude/references/git-workflow.md` は
既に `dev-workflow:in-repo-issue` を literal で持っており、重複を意図して残す理由も併記されている。
案 2 で skill 名が変われば この 1 本は確実に古びるが、上の抽出は `~/.claude/` 接頭辞に錨を打つので
赤くならない。案 2 の代償は見込みではなく実在の 1 件から数え始める。

影響を受けるのは案 2 が挙げる 2 つの利得のうち「消費側も skill 名で参照できるようになる」側
だけで、「3 つめのトリガから発火できる」側は skill を登録した時点で成立するので下がらない。

あわせて、`scripts/check-issue-closure.py` の docstring が乗る前提も確かめた。docstring が書いて
いるのは「main が保護されていて直 push を選べない」という連言で、成立するのは前半だけである。

前半は成立する。classic は rc 1 (404) を返す一方、default branch に効いている rule は
`pull_request` を含む 4 件を返すので、保護そのものは効いている。後半は成立しない。ruleset の
`bypass_actors` にリポジトリのオーナーが `bypass_mode: always` で入っており、直 push は実行できる。
しかも rule を返す endpoint は bypass 特権を反映しないので、この判定手順からは差が見えない
(bypass を持つ当人が叩いても同じ 4 rule が返る)。強制の根拠は不可能性ではなく方針である。

この訂正は新しい発見ではない。ISSUE-41 の spec が 2026-08-29 のレビュー中に同じ結論へ到達して
記録している。本 Issue は起票時にそれを引き継がず、前提を「(main が保護されている)」と半分に
弱めて言い換えたうえで、弱めた側だけを検証して「前提は成立している」と結論していた。検証に使った
検査が偽である側を構造的に見られないので、緑は前提の成立を意味しない。

判定手順そのものが bypass の面を持たないことは、この節の主題とも地続きである。安全側へ倒れるのは
Issue クローズの文脈だけで (bypass できるのに PR 経路を選ぶだけ)、3 つめのトリガ「直 push して
よいか」へ流用すると危険側へ倒れる。グローバル CLAUDE.md が名指しする「bypass 特権で素通り push」の
盲点がそこに重なるので、bypass の面を判定手順へ足すかどうかは置き場所の決定と一緒に扱う。

## 検討の起点

決めるのは置き場所。候補は 3 つある。

1. 現状維持。この repo には PR ワークフロー経由の経路があり、実害は消費側にしか出ていない。
   代償は写しが 2 リポジトリに分かれて drift しうること
2. 判定手順を `plugins/dev-workflow/` 配下の独立した skill へ切り出し、in-repo-issue と gate が
   参照する。3 つめのトリガから発火でき、消費側も skill 名で参照できるようになる。代償は配布表面が
   1 つ増え、消費側の pin 更新がもう一度要ること。`SectionReferences` は緑のままだが、消費側の
   `instruction_refs` は skill 名参照を母集団に持たないので、向こうの参照が壊れても赤くならない
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
      gate 側の参照は 3 箇所あるが主題が割れており、案 2 で動くのは Phase 0 の 1 本 (保護の判定手順)
      だけで、Phase 2 と Phase 3 はクローズ同梱の手順が主題なので in-repo-issue を指したまま残る。
      3 本すべてを別 skill へ向けると `test_gate_names_the_bundling_skill` の skill 名 pin が赤くなる
- [ ] 張り替えた側を CLAUDE.md の 4 点 (消した列挙が部分集合でないか / 参照先が本当に持つか /
      宣言の射程が実態より広くないか / 注記自身が値を再掲していないか) で検算する
- [ ] `scripts/check-issue-closure.py` の docstring が判定の帰結に乗っている件を、置き場所の決定と
      整合させる。配布可否の宣言がその帰結を根拠にしている
- [ ] 同 docstring の「直 push を選べない」という能力表現を方針表現へ直すか、判定手順へ
      `bypass_actors` を見る段を足すかを決める。前者はこのリポジトリだけの修正で済むが、後者は
      配布物の変更なので置き場所の決定と一緒に扱う
- [ ] 消費側が持つ写しをどう扱うかを決める。切り出さないなら写しは意図的な重複のまま残るので、
      その判断を上流側にも記録する
- [ ] 案 2 を採る場合、消費側に skill 名参照の実在を見る層を足すか、足さないことを引き受けるかを
      決める。足すならその canonical を上流と消費側のどちらへ置くかも併せて決める
- [x] dotfiles 側 (ISSUE-50 / ISSUE-53) へ本 Issue への参照を入れて双方向にする。片方向のままだと
      向こうだけを読んだ人にこちらの決定が届かない

## 関連

- `plugins/dev-workflow/skills/in-repo-issue/SKILL.md` — 「クローズ経路: feature PR 同梱を優先」節が
  判定手順の実体を持つ
- `plugins/dev-workflow/skills/pre-merge-quality-gate/SKILL.md` — Phase 0 / Phase 2 / Phase 3 の
  3 箇所がその節を名指しする
- `scripts/test_check_issue_closure.py` — `SectionReferences` が節見出しの実在を検査する。`plugins/`
  配下の参照については、置き場所を動かしたときに反応する唯一の機械層。同クラスに gate の skill 名を
  hard pin する検査がもう 1 本ある
- `scripts/check-issue-closure.py` — docstring が「main が保護されていて直 push を選べない」ことを
  根拠に配布可否を宣言する。連言の後半は成立していない (上の実測)
- ISSUE-41: クローズ同梱の判定を促す入口がゲートのどのフェーズにも無い。本 Issue はその委譲先そのものの
  置き場所を扱う
- ISSUE-36: skill と plugin が消費側へ要求する取り付けを棚卸しする。案 2 を採ると配布表面が 1 つ増える
- ISSUE-49: 保護状態を根拠に書いた記述が保護導入後も更新されていない。本 Issue が扱うのは判定手順の
  置き場所で、あちらは同じ保護状態の記録が古びている側を扱う
- dotfiles の ISSUE-50: GitHub Repository Rulesets を導入する。判定手順そのものの主題を持つ側で、
  タスクに本リポジトリへの展開を含む
- dotfiles の ISSUE-53: 配布先の加入状況と写しの drift を見る層が無い。写しの側の主題を持つ。
  本 Issue が実測した「6 点一致・drift 無し」は、あちらが「どこにも無い」と言っている検査を
  手で 1 回やった形にあたる
