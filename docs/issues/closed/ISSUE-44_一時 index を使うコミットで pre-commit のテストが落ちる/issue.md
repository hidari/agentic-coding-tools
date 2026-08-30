---
status: closed
---

# fix: 一時 index を使うコミットで pre-commit のテストが落ちる

## 背景

`git commit -am` と `git commit -- <paths>` を実行すると、pre-commit の Python テスト hook が
落ちてコミットが中断する。通常のコミット (`git add` してからパス指定なしの `git commit`) は
通るので、既存のコミットはこの形だけで積まれてきた。

原因はテストの fixture が呼び出し元の git 環境を継承することにある。fixture は tempfile 上に
使い捨てリポジトリを作って `git` を呼ぶが、`GIT_INDEX_FILE` を落としていないため、git が一時
index を使うコミットの形では読み書き先が呼び出し元の index へ向く。

## 実測 (2026-08-29)

### hook から見える `GIT_INDEX_FILE`

使い捨てリポジトリの pre-commit hook で環境だけを記録した。

| コミットの形 | hook が見る値 |
| --- | --- |
| 通常 (`git commit`) | `.git/index` (相対パス) |
| `-a` / `-am` | `<絶対パス>/.git/index.lock` |
| パス指定 (`-- <paths>`) | `<絶対パス>/.git/next-index-<pid>.lock` |

通常のコミットだけが安全なのは、値が相対パスで、fixture が `git -C <tempdir>` を使うため
tempdir 自身の index へ解決されるから。**変数の有無ではなく値が絶対パスかどうかが分ける。**

### コミットの形ごとの帰結

`GIT_ENV` の手当て (この PR で入れた、`os.environ` から `GIT_*` を落とす形) の前後で測った。
rc はパイプへ繋がず直接見ている。

| コミットの形 | 手当て前 | 手当て後 |
| --- | --- | --- |
| 通常 | 通る | 通る (367 件 pass) |
| `-a` / `-am` | `error: Error building trees` で中断 | テスト失敗 (問題 2 件) で中断 |
| パス指定 | 同上 | 同上 |

手当て前も `.git/index` 自体は壊れない。fixture が書くのは `index.lock` や `next-index-*.lock`
で、内容が不正なので git が中断して捨てるため。

### 実際に起きた破壊

ISSUE-41 の作業中に `.git/index` が壊れた。GUI が `fatal: unable to read a91cb66e...` を出し、
`git status` も同じエラーで動かなくなった。

| 指標 | 破壊前 | 破壊後 |
| --- | --- | --- |
| `.git/index` | 23159 byte | 4837 byte |
| エントリ数 | 123 | 1 |
| 唯一のエントリ | | `docs/issues/ISSUE-1_alpha/issue.md` |

エントリは `scripts/test_check_related_refs.py` の fixture のパスで、4837 byte は同ファイルを
`GIT_INDEX_FILE` 汚染下で走らせたときの指し先のサイズと一致する。`git read-tree HEAD` で復旧し、
コミットと作業ツリーは失われていない。

破壊には `GIT_INDEX_FILE` が `.git/index` そのものを指している必要がある。上の表のとおり
通常の git 操作はその値を作らないので、これは調査のために変数を明示した probe が踏んだ形。
とはいえ「fixture が指し先を無条件に書く」という性質そのものは同じで、手当て後は指し先が
何であっても書かなくなる。

### 終了コードでは検出できない

破壊はテストの終了コードに出ない。`GIT_INDEX_FILE` 汚染下で測った結果:

| ファイル | テスト結果 | 指し先 index |
| --- | --- | --- |
| `test_issue_id.py` | FAILED | 23159 → 357 byte |
| `test_check_issue_closure.py` | **OK (34 件)** | 23159 → 267 byte |
| `test_check_related_refs.py` | FAILED | 23159 → 4837 byte |

**緑を返しながら壊す。** この Issue の受け入れ基準にテストの rc を使わないこと。判定は指し先
ファイルのハッシュと、実際のコミットが成功するかで行う。

## 読み取り側の実測 (2026-08-30)

### 射程

`GIT_INDEX_FILE` を絶対パスで立てて各ファイルを走らせた。full chain (クローンに pre-commit を
install して `git commit -am`) でも同じ集合が落ちるので、独立した 2 経路が同じ射程を指している。

| ファイル | 清浄 | 汚染下 | 機序 |
| --- | --- | --- | --- |
| `test_check_related_refs.py` | OK 56 件 | FAILED errors=48 | `crr.check()` の先の `git ls-files` が index を読む |
| `test_issue_id.py` | OK 90 件 | FAILED failures=49 | `--check-diff` の `git diff --cached` が index を読む |
| `test_check_issue_closure.py` | OK 35 件 | OK 35 件 | 借用する `issue_dirs` がファイルシステムを走査する |

3 ファイルとも指し先 index は不変で、書き込み側の隔離は効いている。壊れているのは読み取り側だけ。

`test_check_issue_closure.py` の免疫は構造から来ているだけで、借用先が `git ls-files` へ
変わると静かに脆弱になる。読み取り側は行動で対照を置けないので、状態の pin を予防で置いた。

書き込み側は事情が違う。このファイルの fixture 自身が `git add -A` を走らせるので、指し先へ
書かないことを直接見られる。当初「行動で対照を置けない」を両側に効く話として書いていたが、
それは読み取り側にしか当てはまらず、書き込み側は無 pin のまま残っていた (実測: `env=GIT_ENV`
を 4 箇所すべて外しても 36 件緑)。sentinel pin を足して塞いだ。

### 入口は 2 ファイル × 各 1 関数

`test_issue_id.py` の 66 箇所の呼び出しは全て `run(argv)` を、`test_check_related_refs.py` の
プロセス内呼び出しは `Fixture.check()` を通る。汚染下で落ちるテストがその 2 つを通るクラスに
集中し、subprocess 経由の `ExitCodes` は 1 件も落ちないことで裏付けた。

### 棚卸しの軸を間違えた

「`env=` を持たない `subprocess.run`」を軸に AST で全件を数えたが、この軸は偽陰性を返す。
`run-python-tests.py` は `env={**os.environ, ...}` を組み立てており、`env=` を持ちながら
`GIT_*` を素通しする。正しい軸は `env=` の有無ではなく **`os.environ` を継承しているか**。

同じ形が 4 つめのファイルにもある。`test_run_python_tests.py` が `runner.main()` をプロセス内で
呼び、その先の `run_one` が子を産む経路。fixture の合成テストが git を触らないので今は症状が
出ないが、性質は同じ。

## 手当て

### 読み取り側: module scope で `os.environ` を消毒する

`GIT_ENV` は `env=` を渡した subprocess にしか届かない。プロセス内呼び出しの先は `os.environ`
を読むので、そちらを `setUpModule` で `GIT_ENV` に揃える。

呼び出しごとの `with` にしないのは、書いた場所しか覆わないため。プロセス内呼び出しは将来も
増えるが、増やした人が隔離を書き忘れても症状は汚染下でしか出ないので、書き忘れに気づく経路が
無い。`setUpModule` は unittest がそのモジュールのテストを 1 件でも走らせる前に必ず呼ぶので、
クラス構成にも呼び方にも依存しない。

### 第 2 層: runner が子へ渡す環境からも落とす

`run-python-tests.py` の `child_env` が `GIT_*` を落とす。配布される skill のテストは runner の
無い環境へ配られるのでこちらだけでは配布物を守れないが、逆にこの層だけが、まだ書かれていない
テストファイルを取り付け無しで覆う。射程が違うので両方置く。

### 取り付けを外したら赤くなる形

3 種類の pin を置き、各変異が別々の pin を赤にすることを確かめた。件数ではなく失敗したテスト
ID の集合で判定している。

| 変異 | 赤になる pin |
| --- | --- |
| なし (対照) | 無し (59 件 / 93 件 / 37 件 / 20 件とも OK) |
| `setUpModule` を潰す | 状態 pin + 子プロセス再突入 pin |
| fixture の `env=GIT_ENV` を外す | 書き込み側 sentinel pin (3 ファイルとも) |
| `GIT_ENV` から `GIT_*` の追加を落とす | 状態 pin |
| runner の `GIT_*` 除去を戻す | 第 2 層 pin |
| runner のフィルタを特定の変数名へ狭める | 第 2 層 pin |

最後の 1 行は後から足した。第 2 層の pin が当初 `GIT_INDEX_FILE` だけを注入していたので、
フィルタを接頭辞から特定の変数名へ狭める変異を区別できなかった。子の側は接頭辞で広く見て
いたが、**検出力は注入側の狭さに引きずられる**。注入を 2 種類にして塞いだ。

pin の設計で 2 回踏み直している。

- 最初の probe を `--next` へ置いた。`--next` は `for-each-ref` と `ls-tree` で採番するので
  index を読まず、隔離を外しても緑のまま通った。probe は機構と同じ構造上の位置に置くこと
- 次の probe をテストの中の `patch.dict` で書いた。`setUpModule` は module 読み込み時に 1 回
  消毒するだけなので、テストの中で汚染を立て直すと隔離より後になる。実際の汚染は hook から
  継承した状態で最初から存在するため、同じ形を作るには子プロセス再突入が要る

状態 pin の非空虚性を先に見るのは、`GIT_ENV` から `GIT_*` の追加が落ちると両辺が空になり比較が
無条件に通るため。合格を意味する観測値と、機構が働かなかったときの観測値が同じになる形を、
この 1 行が分けている。

### 防御を 1 層に潰さない

`setUpModule` だけを入れると、子プロセスは `env=` を渡さなくても清浄な環境を継承するので、
書き込み側の層を外しても症状が出ない (実測: `env=GIT_ENV` を 3 箇所すべて外しても 56 件 OK・
指し先も不変)。sentinel pin はこの穴を塞ぐためにある。

## 受け入れ検査 (2026-08-30)

判定はテストの rc ではなく、コミットが実際に積まれたか (HEAD が進んだか) で行った。

| コミットの形 | 手当て前 | 手当て後 |
| --- | --- | --- |
| `git add` + `git commit` | 成立 | 成立 |
| `git commit -am` | **成立せず** | 成立 |
| `git commit -- <paths>` | **成立せず** | 成立 |

Python テストは 367 → 376 件。manifest の diff は追加 9 件のみで削除は無い。

## 限界

機構で閉じないものを 2 つ残す。

- **新規テストファイルの取り付け漏れ**。取り付けはファイル単位なので、新しいテストファイルが
  プロセス内呼び出しを持ちながら `setUpModule` を忘れても、このリポジトリでは第 2 層が覆う。
  配布物として書かれた場合は覆われない。レビューが防衛層になる
- **配布先での不実行**。`test_issue_id.py` は skill の初期化手順が消費側へ写す対象に入って
  いない。消費側は配った `issue-id.py` を hook から呼ぶが、その隣にテストが 1 つも無いので、
  隔離が効いているかを配布先で確かめる経路が無い。ここは ISSUE-32 の射程

## タスク

- [x] プロセス内呼び出しの環境をどう隔離するかを決める (呼び出しの周りで差し替えるか、
      CLI 経由へ揃えるか)
- [x] 決めた形を実装し、`git commit -am` と `git commit -- <paths>` が実際に通ることを
      確かめる。テストの rc を根拠にしないこと
- [x] 指し先 index の不変性を機構で pin できるかを判断する。できないなら限界としてどこへ
      書くかを決める
- [x] 同じ隔離漏れが他に無いかを、`os.environ` を継承して git を呼ぶ箇所という軸で棚卸しする

## 関連

- ISSUE-12: scripts の検査スクリプトが自分自身のテストを持たない。テストの構造を触る点が
  重なる
- ISSUE-32: in-repo Issue の検査を配布先で走る状態にする。配布先にテストが届かない件は
  そちらの射程
- ISSUE-41: クローズ同梱の判定を促す入口。本件はその実装中にコミットを分けようとして踏み、
  さらにレビュー中に実際の index 破壊まで起こしたもの。書き込み側の隔離は同 PR に同梱し、
  読み取り側を本 Issue へ残した
