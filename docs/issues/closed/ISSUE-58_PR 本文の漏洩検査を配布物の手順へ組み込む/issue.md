---
status: closed
---

# PR 本文の漏洩検査を配布物の手順へ組み込む

## 背景

ISSUE-15 が PUBLIC 漏洩の検査を 3 層で組み上げたが、**PR 本文の面だけが残っている**。

spec の走査面の表は PR タイトルと本文を「手順の中で層 2 を呼ぶ」扱いにしていた。同じ形で
注釈付きタグは `release` skill の手順へ入った。タグを入れられたのは `release` がプロジェクト
スコープの skill で、検査スクリプトのパスを literal で書けるからである。

PR 本文を扱う `commit-and-pr-message` は **apm で配布される**ので同じ手を使えない。配布先に
検査スクリプトは存在せず、パスを書いても動かない。

## 何が問題か

配布物の skill が、配布先に存在しないスクリプトへ依存できない。

先例は 2 つある。`in-repo-issue` は採番と記法検査のスクリプトを同梱して配布先へ配る形を採り、
取り付けるかどうかはプロジェクトの判断としている。ISSUE-32 はその取り付けを配布先で実際に
走る状態にする作業を扱っている。

漏洩検査を同じ形にするかは決まっていない。禁止語リストがリポジトリの外にあり環境変数で指す
設計なので、スクリプトを配っただけでは動かない面もある。未設定の環境では静かに skip するため、
配っただけで満足すると「取り付けたのに何も見ていない」状態が緑で通る。

## 決定

設計の決定と退けた案は、同じディレクトリの `ISSUE-58-spec.md` が持つ (2026-09-24時点のスナップショット)。実装後の振る舞いの canonical は入口 `plugins/dev-workflow/skills/commit-and-pr-message/scripts/check-outgoing-text.py` の docstring とテストで、手順の canonical は同じ skill の SKILL.md の「送る前の検査」節にある。ここには要点だけを挙げる。

- 届け方: 層 1のルール (custom ルールの config と既定ルールの config の2本) と層 2のスクリプトの canonical を `commit-and-pr-message` の `scripts/` へ移し、両層を1回で当てる入口を同梱した。配布先の hook と CI への取り付けは範囲外とした (ISSUE-32、ISSUE-61)
- 当てる面: skill が扱う全ての面。1行で渡す値 (タイトルや merge の subject) もファイルに書いて本文と一緒に検査し、`"$(cat <file>)"` の形で渡す
- skip の扱い: 入口は層ごとの状態を出し、未検査の層が残ると rc 3で止まってユーザーの確認を求める。skip を緑と読ませない
- 層ごとの適用範囲: 層 1は送り先を問わず当てる。層 2は送り先が PRIVATE と判定できたときだけ外す
- 入力の大きさ: gitleaks の1回の read に収まらない本文は input-too-large で止め、空行の位置で分けて通す。上限の値は入口の docstring が持つ。Task 3のレビューで見つかった fail-open (大きい入力の途中の検出が checked のまま消える) を塞ぐための仕様の追加で、ユーザーの承認を得た

## 実施結果

Task ごとの変更 (Task 1〜4は Task ごとに1本のコミット):

- Task 1: 層 2の本体とテストを `commit-and-pr-message` の `scripts/` へ移し、取り付けの pin を `scripts/test_leak_guard_attachment.py` へ分けた。pre-commit の設定を行で読む補助を `scripts/hook_config_lines.py` へ切り出し、2本の取り付けのテストで共有した
- Task 2: 層 1のルールを custom と既定の2本の config に分けて同じ置き場へ移し、root の `.gitleaks.toml` を消した。pre-commit と CI の gitleaks の呼び出しを config ごとの2本にし、どちらにも `--ignore-gitleaks-allow` を付けた。CI の gitleaks の導入を、版と sha256の canonical を持つスクリプトへ切り出した
- Task 3: 入口 `check-outgoing-text.py` を足した。入力ごとに前後へ canary の行を置いて gitleaks stdin へ流し、層 2を別プロセスで呼び、終了コードを0から3の4つに畳む
- Task 4: SKILL.md の手順を4手 (書く / 送る前の検査 / 渡す / 載ったことを確認する) にし、終了コードと result の組ごとの行動の表を置いた。表と入口の定数の一致をテストで pin した。release skill と in-repo-issue の squash merge の subject を同じ渡し方に揃えた
- Task 5: この記録、ISSUE-66と ISSUE-67の起票、ISSUE-55と ISSUE-61の更新。レビューで見つかったものとして、使っていない linter を抑制する `noqa` のコメント5箇所を外し (ISSUE-55の S3)、Task 1で古くなった `scripts/test_check_related_refs.py` の docstring の参照先を直した

レビューは Task 3が2観点で15件と修正の検証で7件、Task 4が2観点で18件、Task 5が2観点で21件。Task 3と Task 4の裁定は plan の「Task 3 レビューの裁定」「Task 4 レビューの裁定」節にある。Task 5の21件は、重複をまとめた7件を修正担当が直し、残りはコントローラが直すか、この記録で扱った。main のコミットメッセージの再測の記録先 (下の節) と、その記録から値の持ち主を示す記述を外すことは、ユーザーの裁定に従った。plan の基準値の表にある SHA は、測った時点を示す記録なので直さなかった。

検証 (2026-09-25、このコミットの直前の作業ツリー):

- 入口のテスト51件が python3 3.14と /usr/bin/python3 3.9の両方で緑。層 2のテスト71件、配線のテスト22件が緑
- `python3 scripts/run-python-tests.py` が 11ファイル / 732件 で manifest と一致
- `python3 scripts/check-leak-guard-rules.py` が60件 (検出されるべき30 / 許可されるべき30) で manifest と一致
- `pre-commit run --all-files` が15 hook で Passed、対象の無い1 hook で Skipped。禁止語リストの検査は `status=checked` で、追跡ファイル175本を走査して0件。stage した差分を2本の config で走査して約27KB、検出なし
- fresh clone の全履歴の走査 (Task 4のコミットの時点。76コミットで `git rev-list --count` と一致し、浅い clone ではない): `.gitleaksignore` を残すと2本の config とも検出なし。消すと custom の config で10件 (user-path 1件 / vm-uuid 9件)、既定の config で0件。Task 2の基準と同じ
- 旧パスと root の config の名指し: `docs/issues/` を除く追跡ファイル88本で0件。当たった行は全て読み、新しい config のパスか gitleaks の既定の config 名の説明だった。陽性の対照として、新しい2本の config のパスが `.pre-commit-config.yaml` と `ci.yml` で当たる
- `apm audit --file <path> --no-policy` を、このブランチで変わった配布物11本に当てて warning 0
- 変異注入: 各 Task の plan の一覧どおり、作業ツリーの写しで1つずつ入れ、狙ったテストだけが赤になることを確かめた

見込みと違ったもの:

- ISSUE-55の F34は、spec が「今回の変更で対象の文が消える」側に数えていたが、Task 5の時点では残った。層 2の docstring に、当初の3分岐の設計の経緯を述べる文が残っていたため。PR を作る前のゲートで直した

マージ前のゲート (PR を作る前):

- simplify の4観点、Boy Scout Sweep、コードレビュー、PUBLIC 漏洩スイープを並列に通した。コードレビューと漏洩スイープの指摘は0件
- 振る舞いを変えない指摘はこの PR で直した。取り付けのテストの重複を共有の補助へ寄せ、issue-id の追跡ファイルの検査の hook に実効 stage の pin を足し、入口の状態と理由の語彙を docstring と定数で突き合わせるテストを足した。タスク参照と経緯だけを述べるコメントを現在形の理由へ書き換え、層 2の allow_abbrev の説明の誤りを直し、release skill の事前検査を検査の列挙から pre-commit の実行へ変えた。変異注入16本がどれも狙ったテストだけを赤にし、runner は11ファイル / 734件で manifest と一致した
- 挙動や設計を変える指摘と、触っていないファイルのコメントは ISSUE-68に残した

残したもの:

- Linux のホームディレクトリ形のパスは層 1に当たらない (ISSUE-66)
- 計画レビューで見つかった、配布先で成立しない形の2件 (ISSUE-61)
- 数字記法の検出が、数字の直後にかな漢字が続く形で素通りする (ISSUE-67)。Task 5で数字の前後の空白を詰めたときに見つかった
- 配布経路での成立の確認 (pin を上げた消費側で Skill ツールから読み込み、置換後のパスで入口が rc 0か rc 3を返すこと。`/usr/bin/python3` で起動した場合も含む) は、マージと次のリリースのあとで dotfiles へ依頼する (plan の Task 5 Step 8)

## main のコミットメッセージへ層 1を当てた結果 (2026-09-24)

spec の前提 17 (2026-09-17の測定) を測り直した記録。gitleaks の git モードはコミットメッセージを走査しない (ISSUE-15) が、メッセージを1件ずつ stdin のモードで渡すと層 1を当てられる。

再現の手順:

1. `git rev-list --reverse origin/main` でコミットを古い順に並べ、それぞれのメッセージを `git log -1 --format=%B <commit>` でファイルへ書く
2. 空のディレクトリを cwd にして、ファイルごとに `gitleaks stdin -c <config の絶対パス> --ignore-gitleaks-allow --redact --no-banner --report-format json --report-path <report> < <file>` を、2本の config (`leak-guard.gitleaks.toml` と `leak-guard-default.gitleaks.toml`) で1回ずつ走らせる。cwd を空にするのは、stdin のモードが cwd の `.gitleaksignore` を読むためである (spec の前提 13)。cwd が空なので config は絶対パスで渡す。config を開けないときも rc は検出ありと同じ1になるので、rc だけで判定せず、レポートのファイルができたことを確かめる
3. レポートからは RuleID と StartLine だけを取り、その行がメッセージのどこにあるか (件名 / 本文 / 末尾の trailer の段落) で分ける。Match・Secret・Line の値とコミットの SHA は出力に出さない

結果 (gitleaks 8.30.1、origin/main の65件のメッセージ):

| config | ルール | 場所 | 検出 | メッセージ |
|---|---|---|---|---|
| custom | email-address | trailer (`Co-authored-by:` の行) | 8件 | 8件 |
| custom | user-path | 本文 (squash のメッセージ) | 2件 | 1件 |
| 既定 | (なし) | — | 0件 | 0件 |

- 2026-09-17の測定 (62件のメッセージ) と件数も分類も同じで、その後に増えた3件のメッセージには検出が無い
- email-address の8件は、GitHub が squash merge のメッセージに足す trailer にある。手順の検査も commit-msg hook も届かない (spec の既知の限界)
- どの値も main の公開済みの面 (trailer 自身と追跡ファイルの履歴) に既にある語と同じなので、履歴は書き換えない。書き換えると main のハッシュが変わり、既存の参照が外れる

## タスク

- [x] 配布先へ検査を届ける形を決める (同梱するか、プロジェクト側の検査を呼ぶ規約にするか、
      検査を持たないプロジェクトでは何もしないと決めるか)
- [x] 決めた形を `commit-and-pr-message` の手順へ書く。配布先で動かない形を literal で
      書かないこと
- [x] 未設定の環境で静かに skip する性質をどう扱うか決める。skip を緑と読ませない形にする
- [x] 検出すべき例と許可すべき例の両方を実際に通す

## 関連

ISSUE-15 がこの残作業を切り出した元。層 1 から層 3 までと注釈付きタグの面はそちらで入っている。

ISSUE-32 は同型の問題を in-repo Issue の記法検査について扱っている。対象のスクリプトも検査も
別だが、配布先へ届ける形の判断は互いに参照する価値がある。

ISSUE-36 が消費側へ要求する取り付けの棚卸しを扱っており、この Issue が決める形もその対象に
入りうる。

ISSUE-55は層 2のマージ前レビューの残り。この Issue の PR で F2・F25・F33・F34・F35・F36・F38・F40・S3と、F40と同種の箇所・`_locator` の過去形の記述を解決した。

ISSUE-61に、計画レビューで見つかった配布先で成立しない形の2件を記録した。

ISSUE-66は、計画レビューの D-3をユーザーの裁定でこの Issue の範囲から外した先 (Linux のホームディレクトリ形のパス)。

ISSUE-67は、Task 5で見つかった数字記法の検出の穴。

ISSUE-68は、PR を作る前のゲートで見送った改善。
