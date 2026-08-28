---
status: open
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

## この PR で入れた手当てと、残っているもの

入れたのは書き込み側の隔離だけ。3 ファイル (`scripts/test_check_issue_closure.py` /
`scripts/test_check_related_refs.py` /
`plugins/dev-workflow/skills/in-repo-issue/scripts/test_issue_id.py`) の `GIT_ENV` から
`GIT_*` を落とした。これで指し先の index は書かれなくなる。

残っているのは読み取り側。`Fixture.check()` は `crr.check(self.dir, ...)` をプロセス内で呼び、
その中の `git ls-files` が `os.environ` を読む。`GIT_ENV` は subprocess にしか渡らないので
効かない。汚染下では読み取りが呼び出し元の index を見て、テストが赤くなる。

production 側で環境を消毒するのは採れない。`check-related-refs.py` は pre-commit hook として
走るので、一時 index を見るのはむしろ正しい挙動になる。手当てはテスト側の構造に入る。

## タスク

- [ ] プロセス内呼び出しの環境をどう隔離するかを決める (呼び出しの周りで差し替えるか、
      CLI 経由へ揃えるか)
- [ ] 決めた形を実装し、`git commit -am` と `git commit -- <paths>` が実際に通ることを
      確かめる。テストの rc を根拠にしないこと
- [ ] 指し先 index の不変性を機構で pin できるかを判断する。できないなら限界としてどこへ
      書くかを決める
- [ ] 同じ隔離漏れが他に無いかを、`os.environ` を継承して git を呼ぶ箇所という軸で棚卸しする

## 関連

- ISSUE-12: scripts の検査スクリプトが自分自身のテストを持たない。テストの構造を触る点が
  重なる
- ISSUE-41: クローズ同梱の判定を促す入口。本件はその実装中にコミットを分けようとして踏み、
  さらにレビュー中に実際の index 破壊まで起こしたもの。書き込み側の隔離は同 PR に同梱し、
  読み取り側を本 Issue へ残した
