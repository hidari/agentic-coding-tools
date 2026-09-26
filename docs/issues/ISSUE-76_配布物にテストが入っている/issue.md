---
status: open
---

# refactor: 配布物にテストが入っている

## 背景

apm は root に `SKILL.md` を持つパッケージのディレクトリをまるごと展開先 (消費側の `.claude/skills/` の下) へコピーする (README の「使い方」)。そのため、本体の隣に置いたテストも展開先へ届く。これとは別に、`in-repo-issue` の初期化手順は `issue-id.py` を消費側のリポジトリへ写すが、写すのは本体だけでテストは写さない (ISSUE-44 が限界として記録している)。

dotfiles のマージ前ゲートからの提案 (2026-09-25) が、`commit-and-pr-message` の `test_check_*.py` の 2 本 (計 123,107 B) が plugin の配下と flat の写しの 2 か所に届き、展開先で読むものは無いこと、`test_issue_id.py` なども含めて配るディレクトリの外へ出せることを挙げた。下の「確かめたこと」はこちらで測り直したもの。

Python のテスト 5 本はどれも本体と同じディレクトリにある。ISSUE-58 の spec の配置の表は、単体テストを「本体の隣に置く」と決めている。

## 確かめたこと (2026-09-27、origin/main が cbb555d の時点)

- 配布の単位は `scripts/check-package-shape.py` が走査する plugin と単体 skill のディレクトリで、README の「使い方」はこの単位を apm の依存に書く。今は 10 パッケージある
- テストは 5 パッケージに 7 本あり、計 335,886 B で、パッケージの追跡ファイルの合計 924,820 B の約 36% にあたる。残りの 5 パッケージにテストは無い
  - `plugins/dev-workflow`: 3 本 188,728 B。`skills/commit-and-pr-message/scripts/` の `test_check_leak_guard_denylist.py` (60,536 B) と `test_check_outgoing_text.py` (62,571 B)、`skills/in-repo-issue/scripts/test_issue_id.py` (65,621 B)
  - `skills/devops/macos-vm-verification`: `test_macvm.py` (75,670 B)
  - `skills/devops/windows-vm-verification`: `test_winvm.py` (59,463 B)
  - `plugins/security-blue-red-team`: `schemas/tests/cleanup-queue.schema.test.mjs` (6,564 B)
  - `plugins/web-monkey-qa`: `schemas/tests/findings.schema.test.mjs` (5,461 B)
- Python の 5 本は `scripts/run-python-tests.py` が拾う 11 本に入っていて、manifest の 734 件のうち 546 件がこの 5 本のテストである。`.mjs` の 2 本は pre-commit にも CI にも実行の経路が無い (両方の設定に node と mjs の語が 0 件。対照として python の語は両方に現れる)
- 配布物の中の Markdown 33 本に、テストのファイルを名指す行は無い (対照として、同じ 33 本のうち 6 本は `scripts/` を名指す)
- この開発機の展開先 (apm 0.30.0 が pin 66231ad から展開したもの) では、`dev-workflow` の 3 本が plugin の配下 (`dev-workflow/skills/<名前>/scripts/`) と flat の写し (`<名前>/scripts/`) の 2 か所に、残りの 4 本が 1 か所にあり、計 10 ファイル 524,614 B だった。どれも cbb555d の同じファイルと sha256 が一致する (66231ad から cbb555d までにパッケージの変更は無い)。消費側のロックファイルも `dev-workflow` の配布ファイルとして 2 か所の両方を記録しているので、2 か所に置くのは apm である。flat の写しは plugin の内側の skill を展開先の最上位へも写したもので、提案の付記はこれを apm の回帰として dotfiles 側で扱っているとしている。提案の 123,107 B は、`commit-and-pr-message` の 2 本の 1 か所分と一致する
- テストが対象を見つける方法。`test_check_leak_guard_denylist.py`、`test_check_outgoing_text.py`、`test_issue_id.py` は、自分の `__file__` の親ディレクトリから隣のスクリプトをパスで読み込む。`test_check_outgoing_text.py` はさらに 1 つ上の `SKILL.md` の表を読み、入口の定数と突き合わせる。`test_issue_id.py` の `HERE` は、`issue-id.py` の所在と、子プロセスで自分のテストを起動し直すときの cwd の 2 役を兼ねる。`test_macvm.py` と `test_winvm.py` は `import macvm` と `import winvm` で読み、テストのディレクトリが import path にあることに頼る。runner はテストのファイルごとに cwd をそのディレクトリへ切り替えて起動することで、これを満たしている (runner の docstring)。`.mjs` の 2 本は `import.meta.url` から 1 つ上のディレクトリの schema を読む。パスを組み立てる箇所を検索した範囲では、どのテストも自分のパッケージの外のファイルを指さない
- テストの置き場を前提にした記述が 5 つある。runner の docstring (テストが検証対象と同じディレクトリにある前提)、runner の `child_env` の docstring (配布される skill のテストは runner の無い環境へ配られるので、各テストファイルが `GIT_*` の消毒を自前で持つ)、`scripts/test_check_related_refs.py` のコメントと `scripts/test_issue_id_attachment.py` の docstring (配布物のテストの置き場を名指す)、`check-leak-guard-denylist.py` の fold の説明 (テストのファイルを名前で指す)
- runner (`scripts/run-python-tests.py` の discover) はリポジトリの root からテストのファイルを再帰的に探し、実行したテスト ID を、テストファイルの相対パスを前に付けた形で manifest と照合する
- `scripts/check-package-shape.py` は、パッケージの中にテストがあることもないことも求めない。規約 7 の走査はテストを含むパッケージの全ファイルを読む
- `GIT_ENV` という名前の定義は、配布物の `test_check_leak_guard_denylist.py` と `test_issue_id.py`、`scripts/` の `test_check_issue_closure.py` と `test_check_related_refs.py` の 4 本にある。ISSUE-55 の R2 の追記は、配布物のテストが配布元の `scripts/` の共有モジュールを読むと配布先では成立しないことを理由に、寄せる対象を `scripts/` の 2 本に絞っている
- apm で配布から外す手段。apm 0.30.0 のタグのソース (`deployable_source_plan.py`、`skill_integrator.py`、`skill_support.py`、`security/gate.py`) と作者向けのドキュメント (`skills.md`)、`apm --help` を読んだ範囲では、root に `SKILL.md` を持つパッケージはディレクトリ全体がコピーされる。外れるのはシンボリックリンク、キャッシュの目印のファイル、`.apm/`、条件付きの `bin/`、marketplace plugin の root の `apm.yml` だけで、パッケージの作者がファイルを指定して外す設定は見つからなかった。install と pack のコマンドの option にも、ファイルを外すものは無い (install の `--exclude` は MCP/LSP の runtime を外すもの)。0.32.0 でも `deployable_source_plan.py` は 0.30.0 と同じ blob で、pack の option も同じだった

## 決めること

- テストを配布物の外へ移すか。移すと、展開先へ届くファイルが減り、テストが `scripts/` の共有モジュールを使えるようになる (ISSUE-55 の R2 の制約が外れる)。代わりにテストは本体の隣を離れ、ISSUE-58 の spec が決めた置き方を改めることになる。配布物だけを見る読み手は、本体の仕様をテストで読めなくなる。残すときは、これらが裏返しに続く
- 移すなら置き場所。パッケージのパスをリポジトリの別のディレクトリの下に写す形では、パッケージを改名したり動かしたりするたびに、写した側の配置も追従させることになる (探索は再帰なので、追従のたびに runner を直す必要はない)。既存の `scripts/` に並べる形では、`scripts/` の検査スクリプトのテストと、配布物のテストが同じ場所に並ぶ。`scripts/` には既に配布物の取り付けを見るテスト (`test_issue_id_attachment.py`、`test_leak_guard_attachment.py`) がある
- 移すならモジュールの見つけ方。今は 3 本が `__file__` の隣を、2 本が cwd を頼りにしている。移した先からはリポジトリの中のパッケージのパスを指すことになり、テストがリポジトリの配置に依存する。`test_issue_id.py` の `HERE` は 2 役を兼ねるので、指し先だけを変えると自分のテストを起動し直す箇所が壊れる。`import macvm` と `import winvm` の 2 本は、パスで読み込む形に変えるか、runner が import path を足す形にするか。「確かめたこと」に挙げた、テストの置き場を前提にした記述も実態と合わなくなる
- 移すなら manifest と runner。manifest の ID はテストファイルの相対パスを含むので、移す 5 本のテスト ID がすべて消えたテストと未記録のテストに分かれ、`--update-manifest` で作り直すことになる。集合の照合は移動と削除と追加を区別しないので、移動はテストの増減や改名を含まない単独のコミットにする。リポジトリの CLAUDE.md の「規約は散文ではなく検査に落とす」に従うなら、移したあと、パッケージの中にテストが戻ることを `scripts/check-package-shape.py` の検査で止めるかも、あわせて決める
- 今のまま残すか。残す側にだけある事項は次のとおり。どのテストもパッケージの外を指さないので、展開先で走らせる余地が残る (確かめた時点では、展開先で走らせる手順も経路も無い。ISSUE-32 が作れば変わる)。テストはどの Markdown からも名指されていないので、skill の読み込みでは読まれず、文脈の量には効かない。払い続けるのは展開先のディスクと install ごとのコピーと検査で、量は「確かめたこと」の展開先の項目にある。apm の側に作者がファイルを外す手段が入れば、テストを本体の隣に残したまま配布からだけ外せるが、0.32.0 までの apm には無い
- `.mjs` の 2 本を同じ扱いにするか。どちらも実行の経路が無く、経路を作るタスクは ISSUE-34 が持つ。置き場を変えるなら、ISSUE-34 が作る経路の置き場とも関わる。どちらも schema を相対で読むので、移すなら Python のテストと同じ手当てが要る

## タスク

- [ ] 移すか今のまま残すかを決める。残すなら、apm の側に除外の手段が入るのを待つか (上流へ要望するかを含む) も決める。`.mjs` の 2 本の扱いも決める
- [ ] 移すなら、置き場所とモジュールの見つけ方を決めて移し、「確かめたこと」に挙げたテストの置き場を前提にした記述を実態に合わせる
- [ ] 移すなら、移動をテストの増減や改名を含まない単独のコミットにし、`--update-manifest` で manifest を作り直して、その前後で相対パスを除いたテスト ID の集合が一致することを確かめる
- [ ] 移したあと、消費側で pin を上げてから、展開先にテストのファイルが届いていないことを確かめる (ISSUE-75 の消費側の確認と同じ pin 上げに相乗りしてよい)

## 関連

ISSUE-58 (closed。層 2 のテストを `scripts/` から配布物へ移し、入口のテストを本体の隣に新設した。spec の配置の表が「本体の隣に置く」と決めている)
ISSUE-55 (R2 の追記が、配布物のテストは配布元の `scripts/` の共有モジュールを読めないことを理由に共有の対象を絞っている。移せばこの制約は外れる)
ISSUE-44 (closed。消費側のリポジトリへ写す `issue-id.py` の隣にテストが無いことを限界として記録し、配布先でテストを走らせる件を ISSUE-32 の射程とした)
ISSUE-32 (配布先で in-repo Issue の検査を走らせる。ISSUE-44 が配布先でテストを走らせる件を渡した先で、移すとこの選択肢が消える)
ISSUE-34 (配布物の中の `.mjs` の 2 本を走らせる経路を作るタスクを持つ)
ISSUE-68 (同じ提案のうち層 2 をまとめる案を項目 2 で扱う。項目 2 と 3 は `test_check_outgoing_text.py` を触るので、どちらが先でも、移動はテストの増減や改名を含まない単独のコミットにする)
ISSUE-75 (同じ提案のうち、SKILL.md の大きさと公開範囲の判定を扱う。消費側の確認は同じ pin 上げに相乗りできる)
