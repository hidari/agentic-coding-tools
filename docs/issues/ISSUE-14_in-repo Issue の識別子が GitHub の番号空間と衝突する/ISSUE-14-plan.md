# ISSUE-14 実装プラン

Spec は同ディレクトリの `issue.md`。設計判断の根拠と却下した案はそちらが持つ。
このプランは実装手順だけを持ち、設計の理由を再掲しない。

## Global Constraints

すべてのタスクを拘束する。

- **識別子の形**: in-repo Issue は `ISSUE-<N>`。`<N>` は 10 進の整数で zero-pad しない。
  ディレクトリは `docs/issues/ISSUE-<N>_<title>/` と `docs/issues/closed/ISSUE-<N>_<title>/`
- **`#<N>` の扱い**: GitHub の PR / Issue 専用。直前が `PR ` (自リポの PR) か
  `<owner>/<repo>` (他リポ) のときだけ許す。それ以外の裸の `#<N>` は違反
- **既存の参照を書き換える規則** (Task 2 / 3 / 4 が従う)
  - 本リポの in-repo Issue → `ISSUE-<N>` (`Issue #8` → `ISSUE-8`)
  - 本リポの GitHub PR → `PR #<N>`。**列挙は 1 件ごとに `PR ` を前置する**
    (`PR #3 / #6 / #9` → `PR #3` / `PR #6` / `PR #9`)。免除の判定は直前の文字列だけを見る
  - 他リポジトリの **GitHub** Issue / PR → `<owner>/<repo>#<N>`
  - 他リポジトリの **in-repo** Issue → `#` を使わず `<repo> の in-repo Issue <N>` と書く。
    向こうが記法分離をまだ採っていないので、`#` を付けるとその番号の GitHub オブジェクトへ
    解決してしまう。実測: astralys-art の GitHub `#1267` は `chore: reserve issue number`
    という中身の無い予約ダミーで、in-repo Issue 1267 の investigation とは別物。
    GitHub `#1190` は Dependabot の bump で、in-repo Issue 1190 とは無関係
  - 参照先が復元できないコード内コメント → 推測で番号を割り当てず、番号を伴わない記述へ
    書き換える (コード内のタスク参照コメントは rot する、と既に規定がある)
  - コードフェンス / インラインコードの中は免除なので書き換えない
- **依存を増やさない**: 標準ライブラリのみ。pip install しない。テストは `unittest`。
  外部プロセスは `git` だけ。ネットワークを使わない
- **ツールの surface を広げない**: `issue-id.py` の入口は `--next` / `--check` /
  `--check-text` の 3 つだけ。将来別ツールへ切り出す前提なので機能を足さない。
  `gh` を呼ぶ経路・設定ファイル・プロジェクト別の調整をツールに持たせない
- **二重管理を作らない**: 接頭辞と regex の canonical は `issue-id.py` の docstring。
  SKILL.md も CLAUDE.md も README も、regex も接頭辞の literal も再掲せずファイル名で参照する
- **コメントは日本語**。説明するのは WHY。WHAT は識別子で示す
- **外部コマンドの挙動に由来する実装には実測した内容を添える**
- **変異注入**: 検査機構を足したタスクは 3 種の変異 (検査対象を壊す / 検査機構を壊す /
  取り付けを外す) と「緩めすぎる方向」の変異を置き、それぞれ赤になることを実測して報告する。
  変異は一度に 1 箇所ずつ隔離して行う
- **git を歩く経路のテストは fixture リポを作る形にする**。実ツリーに依存したテストは
  dead pin になる前例がある
- **NUL 区切り**: `git ls-files -z` / `find -print0` / `git ls-tree -z` を使う。
  パスに半角空白と日本語が含まれるので、空白分割は静かに取りこぼす
- テストを増減・改名したら `python3 scripts/run-python-tests.py --update-manifest` を実行し、
  `scripts/python-tests-manifest.txt` の diff ごとコミットする
- コミット本文は Write でファイルに書いて `git commit -F` で渡す (日本語をコマンド文字列に載せない)

## Task 1: issue-id.py と そのテスト

`plugins/dev-workflow/skills/in-repo-issue/scripts/issue-id.py` を新規作成する。
同ディレクトリに `test_issue_id.py` を置く。

### 入口

```
issue-id.py --next               次の識別子を 1 行で印字する (例: ISSUE-14)
issue-id.py --check              リポジトリを走査する
issue-id.py --check-text <path>  テキスト 1 本を走査する ("-" で stdin)
```

`--check` と `--check-text` は違反を 1 行 1 件で stderr へ印字し、最後に「何を何件見たか」の
サマリを stdout へ印字する。exit code は 0 (合格) / 1 (違反あり) / 2 (検査不能)。
exit 2 の先例は `scripts/check-leak-guard-rules.py`。

`--root <path>` を共通オプションとして受ける (テストが fixture リポを指すため)。既定は
`git rev-parse --show-toplevel`。

### 定数 (canonical はこのファイルのみ)

```python
PREFIX = "ISSUE-"
ISSUE_DIR = re.compile(r"^" + re.escape(PREFIX) + r"([0-9]+)_")
GITHUB_REF = re.compile(r"(?<!&)#([0-9]+)\b")
GITHUB_REF_ALLOWED_PREFIX = "PR "
CROSS_REPO_REF = re.compile(r"[0-9A-Za-z._-]+/[0-9A-Za-z._-]+$")
```

`GITHUB_REF` の後読みで除くのは `&` だけにする。英数字を除くと `PR#9` のような形が
無検査で通る (実測で確認すること)。

### `--next` の採番

現ツリーの `find` と全 ref の `git ls-tree` を混ぜて最大値を取り、+1 する。

- `git ls-tree` には **`-r` を付ける**。付けないと `docs/issues/closed` が tree 1 件として
  出るだけで配下が列挙されず、ブランチ内で起票と close を同梱した Issue を取りこぼす
- `git ls-tree` には **`-z` を付ける**。既定出力は非 ASCII のパスを C クォートする
- ref の列挙は `git for-each-ref --format=%(refname) refs/heads refs/remotes`
- `docs/issues` を持たない ref は静かに飛ばす
- 番号が重複していたら exit 1 (どの ref のどのパスかを印字する)

### `--check` が見るもの

1. `docs/issues/` と `docs/issues/closed/` 直下のディレクトリ名が `ISSUE_DIR` に一致する
   (`templates/` は除外)
2. 番号がツリー全体で一意 (active と closed をまたいで見る)
3. 追跡下の全ファイルを `scan_text()` に通す。`git ls-files -z` で列挙し、
   拡張子 `.css` / `.scss` は除外する (色指定の `#` が偽陽性になるため)。
   デコードできないファイルは飛ばし、飛ばした件数をサマリに出す

### `scan_text()`

コードフェンスとインラインコードを潰してから `GITHUB_REF` を探す。

- フェンスは N 連バッククォート (3 個以上) に対応する。3 連で開いて 4 連で閉じる形を
  誤判定しないこと
- **フェンスの閉じ忘れは違反として報告する**。閉じ忘れは免除を末尾まで広げる最大の穴で、
  黙って広がると「違反 0 件」に化ける
- 違反行は `<label>:<lineno>: ...` の形で、何をどう書き換えればよいかを含める

### テスト (`test_issue_id.py`)

`--next` と `--check` の git を歩く経路は **fixture リポを `tempfile` + `git init` で作る**。
実ツリーに依存させない。実測で dead pin になった前例がある。

最低限これらを覆う。exact 値で検証し、弱い assertion (存在チェックのみ) にしない。

- `--next`: 空リポ / 旧形式のみ / 新形式のみ / 混在 / 未マージブランチの `closed/` 配下に
  最大値がある場合 (`-r` が無いと取りこぼすケース)
- 番号重複の検出
- `scan_text()` の陽性: 裸の `#8` / 行頭の `#8` / `Issue #8` / `[Issue #8](...)` / `PR#9`
- `scan_text()` の陰性 (通すべき): `PR #9` / `owner/repo#9` / フェンス内の `#8` /
  インラインコード内の `#8` / `&#39;`
- フェンス閉じ忘れの検出
- N 連バッククォートのフェンス

### 完了条件

`python3 scripts/run-python-tests.py --update-manifest` を実行して manifest を更新し、
`python3 scripts/run-python-tests.py` が exit 0 になること。

この時点で `--check` をリポジトリに対して実行すると**違反が大量に出るのが正しい**
(既存の参照がまだ旧記法のため)。取り付けは Task 5 で行う。

### 変異注入 (このタスクで実施し報告する)

- 検査対象を壊す: fixture へ裸の `#8` を含むファイルを足す → 赤
- 検査機構を壊す: `GITHUB_REF_ALLOWED_PREFIX` を空文字にする → `PR #9` の陰性テストが赤
- 検査機構を壊す: フェンス閉じ忘れの報告を落とす → 対応テストが赤
- 検査機構を壊す: `ls-tree` の `-r` を外す → 未マージ `closed/` のテストが赤
- 緩めすぎる方向: `GITHUB_REF` の後読みへ `[0-9A-Za-z]` を足す → `PR#9` の陽性テストが赤

## Task 2: 既存 13 件の rename とリンク修正

`docs/issues/` と `docs/issues/closed/` の既存 13 ディレクトリを `<N>_<title>` から
`ISSUE-<N>_<title>` へ `git mv` する。**番号とタイトルは変えない**。

Issue 間の相対リンクは実測で 19 件 (すべて `](../` の素の形。山括弧 `](<../` 形は 0 件)。
リンク先のパスに含まれるディレクトリ名を新形式へ直す。

- 移行スクリプトは `.cache/` に書き捨てで作る。配布物 (`issue-id.py`) に入れない
- 対象ファイルの列挙は NUL 区切りで受ける
- rename 後に「旧形式のディレクトリが 0 件」かつ「新形式が 13 件」であることを数えて確認する
- リンクの検証は、修正後に各リンク先が実在ファイルへ解決することを確認する。
  「置換後の語を数える」のではなく「置換前の語が残っていないこと」と「解決すること」の両方を見る
- `.gitleaksignore` の 1 件は歴史コミットの path を指しているので**触らない**。
  rename 後も `gitleaks` が通ることを確認する

`docs/issues/ISSUE-14_...` のディレクトリ名は既に新形式なので rename の対象外。

### 裸参照の書き換え (このタスクが `docs/issues/**` を所有する)

`docs/issues/` 配下の全ファイル (`ISSUE-14` 自身の `issue.md` と `ISSUE-14-plan.md` を含む)
の裸の `#<N>` を、Global Constraints の「既存の参照を書き換える規則」に従って直す。

近似計測で `docs/issues/**` に約 60 件ある (フェンス未考慮なので上界)。フェンス内は免除
なので書き換えない。**`issue-id.py --check-text` を各ファイルへ当てて、対象を自分で数えること。**
私が渡す件数は近似であって網羅ではない。

## Task 3: in-repo-issue SKILL.md の書き換え

`plugins/dev-workflow/skills/in-repo-issue/SKILL.md` を新記法へ移す。

- **frontmatter の description**: `docs/issues/<NNN>_<title>/` と `Closes #NNN` / `Fixes #NNN`
  を新記法へ。この description は README 生成の canonical でもある
- **「用語と構造」表**: 番号の行を識別子の行へ。採番と形式の canonical が
  `scripts/issue-id.py` であることを書く (regex も接頭辞も再掲しない)
- **A.1 採番**: inline bash を `issue-id.py --next` の呼び出しへ置き換える。
  8 進解釈・`sort -n | tail -1`・ブレースグループの散文による説明はスクリプト化で不要になるので削除する
- **A.2**: ディレクトリ名を `${NEXT}_<sanitized-title>` の形で組む (`${NEXT}` が `ISSUE-14`)
- **Phase C.1**: `gh pr view` が `--json body,title` を読むようにする。現行は body のみで、
  実測で PR #3 / PR #6 / PR #9 はタイトル側にしか Issue 参照が無い
- **Phase C.3 / E.1 の `ls` グロブ**: マッチしないときの挙動がシェルの `nomatch` 設定に
  依存する。`find` ベースへ置き換えて設定非依存にする
- **「PR / コミット規約」**: PR タイトル・PR 本文・コミットメッセージ表の `#NNN` を新記法へ。
  `採番衝突回避ルールをプロジェクト CLAUDE.md に明示すること` の 1 文を**削除**し、
  スクリプト同梱で置き換える
- **「初期化」節**: templates のコピーに加えて `issue-id.py` を配る手順を足す
- **Red flags 表**: 記法と `-r` に関する行を足す。スクリプト化で不要になった行は削除する

**裸参照の書き換え**: このタスクが `in-repo-issue/SKILL.md` の裸の `#<N>` を所有する
(近似計測で 4 件。フェンス内は免除)。Global Constraints の規則に従う。

CLAUDE.md の「散文を参照へ書き換えたら 4 点で検算する」に従うこと。
(1) 消した列挙が全体の部分集合でなかったか (2) 指名した参照先が本当にその情報を持つか
(3) 書いた宣言の範囲が実態より広くないか (4) 足した注記自身が値を再掲していないか。

## Task 4: skill 間契約と CLAUDE.md と README

記法を変えると、SKILL.md の外にある契約消費者が drift する。数えて直す。

- `plugins/dev-workflow/skills/pre-merge-quality-gate/SKILL.md` の `Closes #NNN` /
  `Fixes #NNN` を新記法へ。ここが drift すると Phase C の起動判定が空振りし、
  自動クローズが静かに止まる
- `plugins/dev-workflow/SKILL.md` の `docs/issues/<NNN>_<title>/` の記述
- `plugins/dev-workflow/skills/issue-scoped-artifacts/SKILL.md` の `<NNN>-spec.md` /
  `<NNN>-plan.md` と `docs/issues/<NNN>_<title>/` の記述
- リポジトリの `CLAUDE.md` 「Issue 管理」節。canonical の参照を足す (値は再掲しない)。
  「規約は散文ではなく検査に落とす」節の表にも 1 行足す
- `python3 scripts/gen-readme.py` を実行して README を再生成する

**まず `grep` で契約消費者を数え上げてから直すこと。**上の列挙が全体の部分集合でないことを
確認する (棚卸しの表は見つけた分だけが載るので必ず過小評価に外れる)。

### 裸参照の書き換え (このタスクが Task 2 / Task 3 の範囲外すべてを所有する)

`docs/issues/**` と `in-repo-issue/SKILL.md` 以外の追跡ファイル全部。
**私の列挙からではなく `issue-id.py --check` の出力から対象を決めること。**

既知の 1 件: `plugins/security-blue-red-team/schemas/tests/cleanup-queue.schema.test.mjs:130`
の `// ... (Issue #1 の中核意図)`。本リポの ISSUE-1 は winvm の CP932 クラッシュで、
コメントの文脈 (cleanup_command の設計意図) と一致せず**参照先が復元できない**。
推測で番号を割り当てず、番号を伴わない記述へ書き換える。

## Task 5: 取り付けと Attachment テスト

`issue-id.py --check` と `--check-text` を pre-commit と CI へ取り付ける。

- `.pre-commit-config.yaml` に local hook を 2 本足す
  - pre-commit stage: `--check` (`language: system` / `pass_filenames: false` /
    `always_run: true`。既存の package-shape と同じ形。`files:` で絞ると
    docs/issues を触らないコミットで走らず、走らなかったことが見えない)
  - commit-msg stage: `--check-text`。`default_install_hook_types` に `commit-msg` を足す
- `.github/workflows/ci.yml` は**新 job を作らず**既存 job に step を 1 つ足す。
  runner を増やすと CI コストが上がる。job 名は変えない (required status checks を壊さない)
- `scripts/test_issue_id_attachment.py` を置き、`.pre-commit-config.yaml` と `ci.yml` が
  checker を呼んでいることを pin する。先例は `scripts/test_run_python_tests.py` の
  `Attachment` クラス

### 完了条件

- `python3 scripts/run-python-tests.py --update-manifest` 後に exit 0
- `python3 plugins/dev-workflow/skills/in-repo-issue/scripts/issue-id.py --check` が
  **リポジトリ全体に対して exit 0** (Task 2〜4 で参照が新記法へ揃っているため)

### 変異注入 (このタスクで実施し報告する)

- 取り付けを外す: `.pre-commit-config.yaml` の hook を消す → Attachment テストが赤
- 取り付けを外す: `ci.yml` の step を消す → Attachment テストが赤
- 検査対象を壊す: 追跡ファイルに裸の `#8` を書く → pre-commit が赤
- 検査対象を壊す: コミットメッセージに裸の `#8` を書く → commit-msg が赤

commit-msg stage は `pre-commit install --hook-type commit-msg` を打たないと発火しない。
実際に発火することを実測して報告すること (取り付けたつもりで効いていない形を避ける)。
