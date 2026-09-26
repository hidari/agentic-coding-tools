---
status: open
---

# refactor: 配布物にテストが入っている

## 背景

apm は root に `SKILL.md` を持つパッケージのディレクトリをまるごと展開先 (消費側の `.claude/skills/` の下) へコピーする (README の「使い方」)。そのため、本体の隣に置いたテストも展開先へ届く。配布物の中にテストを名指す手順は無い。これとは別に、`in-repo-issue` の初期化手順は `issue-id.py` を消費側のリポジトリへ写すが、写すのは本体だけでテストは写さない (ISSUE-44 が限界として記録している)。

dotfiles のマージ前ゲートからの提案 (2026-09-25) が、`commit-and-pr-message` の `test_check_*.py` の 2 本 (計 123,107 B) が prefix 付きと flat の 2 経路に届き、展開先で読むものは無いこと、`test_issue_id.py` なども含めて配るディレクトリの外へ出せることを挙げた。下の「確かめたこと」はこちらで測り直したもの。

Python のテスト 5 本はどれも本体と同じディレクトリにある。ISSUE-58 の spec の配置の表は、単体テストを「本体の隣に置く」と決めている。`scripts/run-python-tests.py` も、テストが検証対象と同じディレクトリにあることを前提に組まれている。

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
- この開発機の展開先 (apm 0.30.0 が pin 66231ad から展開したもの) では、`dev-workflow` の 3 本が plugin の配下 (`dev-workflow/skills/<名前>/scripts/`) と平らな置き場 (`<名前>/scripts/`) の 2 か所に、残りの 4 本が 1 か所にあり、計 10 ファイル 524,614 B だった。どれも cbb555d の同じファイルと sha256 が一致する (66231ad から cbb555d までにパッケージの変更は無い)。消費側のロックファイルも `dev-workflow` の配布ファイルとして 2 か所の両方を記録しているので、2 か所に置くのは apm である。2 か所目は plugin の内側の skill を展開先の最上位へも写す flat の写しで、提案の付記はこの flat 分解を apm の回帰として dotfiles 側で扱っているとしている。flat の写しを除くと 7 ファイル 335,886 B で、リポジトリの中のテストと同じ量になる。提案の 123,107 B は、`commit-and-pr-message` の 2 本の 1 か所分 (60,536 B と 62,571 B の和) と一致する
- テストが対象を見つける方法。`test_check_leak_guard_denylist.py`、`test_check_outgoing_text.py`、`test_issue_id.py` は、自分の `__file__` の親ディレクトリから隣のスクリプトをパスで読み込む。`test_check_outgoing_text.py` はさらに 1 つ上の `SKILL.md` の表を読み、入口の定数と突き合わせる。`test_issue_id.py` の `HERE` は、`issue-id.py` の所在と、子プロセスで自分のテストを起動し直すときの cwd の 2 役を兼ねる。`test_macvm.py` と `test_winvm.py` は `import macvm` と `import winvm` で読み、テストのディレクトリが import path にあることに頼る。runner はテストのファイルごとに cwd をそのディレクトリへ切り替えて起動することで、これを満たしている (runner の docstring)。`.mjs` の 2 本は `import.meta.url` から 1 つ上のディレクトリの schema を読む。パスを組み立てる箇所を検索した範囲では、どのテストも自分のパッケージの外のファイルを指さない
- runner (`scripts/run-python-tests.py` の discover) はリポジトリの root からテストのファイルを再帰的に探し、実行したテスト ID を、テストファイルの相対パスを前に付けた形で manifest と照合する。runner の `child_env` の docstring は、各テストファイルが `GIT_*` の消毒を自前で持つ理由として、配布される skill のテストは runner の無い環境へ配られることを挙げている
- `scripts/check-package-shape.py` は、パッケージの中にテストがあることもないことも求めない。規約 7 の走査はテストを含むパッケージの全ファイルを読む
- `GIT_ENV` という名前の定義は、配布物の `test_check_leak_guard_denylist.py` と `test_issue_id.py`、`scripts/` の `test_check_issue_closure.py` と `test_check_related_refs.py` の 4 本にある。ISSUE-55 の R2 の追記は、配布物のテストが配布元の `scripts/` の共有モジュールを読むと配布先では成立しないことを理由に、寄せる対象を `scripts/` の 2 本に絞っている。`scripts/test_check_related_refs.py` のコメントと `scripts/test_issue_id_attachment.py` の docstring は、配布物のテストの置き場を名指している
- apm で配布から外す手段。apm 0.30.0 のタグのソース (`deployable_source_plan.py`、`skill_integrator.py`、`skill_support.py`、`security/gate.py`) と作者向けのドキュメント (`skills.md`)、`apm --help` を読んだ範囲では、root に `SKILL.md` を持つパッケージはディレクトリ全体がコピーされる。外れるのはシンボリックリンク、キャッシュの目印のファイル、`.apm/`、条件付きの `bin/`、marketplace plugin の root の `apm.yml` だけで、パッケージの作者がファイルを指定して外す設定は見つからなかった。install と pack のコマンドの option にも、ファイルを外すものは無い (install の `--exclude` は MCP/LSP の runtime を外すもの)。0.32.0 でも `deployable_source_plan.py` は 0.30.0 と同じ blob で、pack の option も同じだった

## 決めること

- テストを配布物の外へ移すか。移すと、展開先へ届くファイルが減り、テストが `scripts/` の共有モジュールを使えるようになる (ISSUE-55 の R2 の制約が外れる)。代わりにテストは本体の隣を離れ、ISSUE-58 の spec が決めた置き方を改めることになる。配布物だけを見る読み手は、本体の仕様をテストで読めなくなる
- 移すなら置き場所。パッケージのパスをリポジトリの別のディレクトリの下に写す形では、パッケージを改名したり動かしたりするたびに、写した側の配置も追従させることになる (runner は再帰で探すので runner の変更は要らない)。既存の `scripts/` に並べる形では、`scripts/` の検査スクリプトのテストと、配布物のテストが同じ場所に並ぶ。`scripts/` には既に配布物の取り付けを見るテスト (`test_issue_id_attachment.py`、`test_leak_guard_attachment.py`) がある
- 移すならモジュールの見つけ方。今は 3 本が `__file__` の隣を、2 本が cwd を頼りにしている。移した先からはリポジトリの中のパッケージのパスを指すことになり、テストがリポジトリの配置に依存する。`test_issue_id.py` の `HERE` は 2 役を兼ねるので、指し先だけを変えると自分のテストを起動し直す箇所が壊れる。`import macvm` と `import winvm` の 2 本は、パスで読み込む形に変えるか、runner が import path を足す形にするか。runner の docstring の、テストが検証対象と同じディレクトリにあるという前提と、`child_env` の docstring の配布物についての記述も実態と合わなくなる。本体の docstring がテストのファイルを名前で指している箇所もある (`check-leak-guard-denylist.py` の fold の説明)
- 移すなら manifest と runner。manifest の ID はテストファイルの相対パスを含むので、546 件がすべて消えたテストと未記録のテストに分かれ、`--update-manifest` で作り直すことになる。集合の照合は、移動と削除と追加を区別しない。リポジトリの CLAUDE.md の「規約は散文ではなく検査に落とす」に従うなら、移したあと、パッケージの中にテストが戻ることを `scripts/check-package-shape.py` の検査で止めるかも、あわせて決める
- 今のまま残すか。テストを仕様として実装の隣に置いたままにでき、配布物だけを見る読み手も本体の仕様をテストで確かめられる。どのテストもパッケージの外を指さないので、展開先で走らせる余地も残る。ただし展開先で走らせる手順も経路も今は無い。テストはどの Markdown からも名指されていないので、skill の読み込みでは読まれず、文脈の量には効かない。払い続けるのは、展開先のディスクと install ごとのコピーと検査 (この開発機では 10 ファイル 524,614 B、flat の写しを除くと 335,886 B) と、`scripts/` の共有モジュールを使えないことによる定義の重複である。apm の側に作者がファイルを外す手段が入れば、テストを本体の隣に残したまま配布からだけ外せるが、0.32.0 までの apm には無い
- `.mjs` の 2 本を同じ扱いにするか。どちらも実行の経路が無く、経路を作るタスクは ISSUE-34 が持つ。置き場を変えるなら、ISSUE-34 が作る経路の置き場とも関わる。どちらも schema を相対で読むので、移すなら Python のテストと同じ手当てが要る

## タスク

- [ ] 移すか今のまま残すかを決める。残すなら、apm の側に除外の手段が入るのを待つか (上流へ要望するかを含む) も決める。`.mjs` の 2 本の扱いも決める
- [ ] 移すなら、置き場所とモジュールの見つけ方を決めて移し、runner と本体の docstring、`scripts/` のテストのコメントにあるテストの置き場についての記述を実態に合わせる
- [ ] 移すなら、`--update-manifest` で manifest を作り直し、相対パスを除いたテスト ID の集合が移す前と一致することを確かめる
- [ ] 移したあと、消費側で pin を上げてから、展開先にテストのファイルが届いていないことを確かめる

## 関連

ISSUE-58 (closed。層 2 のテストを `scripts/` から配布物へ移し、入口のテストを本体の隣に新設した。spec の配置の表が「本体の隣に置く」と決めている)
ISSUE-55 (R2 の追記が、配布物のテストは配布元の `scripts/` の共有モジュールを読めないことを理由に共有の対象を絞っている。移せばこの制約は外れる)
ISSUE-44 (closed。消費側のリポジトリへ写す `issue-id.py` の隣にテストが無いことを限界として記録し、配布先でテストを走らせる件を ISSUE-32 の射程とした)
ISSUE-32 (配布先で in-repo Issue の検査を走らせる。ISSUE-44 が配布先でテストを走らせる件を渡した先で、移すとこの選択肢が消える)
ISSUE-34 (配布物の中の `.mjs` の 2 本を走らせる経路を作るタスクを持つ)
ISSUE-68 (同じ提案の (3) を項目 2 で扱う。項目 2 と 3 が触る `test_check_outgoing_text.py` はここで移す対象に入るので、着手の順序が関わる)
