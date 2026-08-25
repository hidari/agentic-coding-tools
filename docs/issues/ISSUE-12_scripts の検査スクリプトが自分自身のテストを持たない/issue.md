---
status: open
---

# test: scripts/ の検査スクリプトが自分自身のテストを持たない

## 背景

ISSUE-8 で `scripts/run-python-tests.py` の収集がディレクトリ列挙から ROOT 全体走査へ
変わり、`scripts/` 配下にテストを置けるようになった。実際に
`scripts/test_run_python_tests.py` が置かれ、runner 自身は変異注入で検証された状態にある。

ただし機構が作ったのは「置けること」までで、「置くこと」は強制していない。残る 3 本は
無検査のままである。

| スクリプト | 状態 |
|---|---|
| `check-package-shape.py` | 無検査。加えて `__main__` ガードが無く import 安全でない |
| `gen-readme.py` | 無検査 |
| `check-leak-guard-rules.py` | 無検査 |

これらはいずれも「破っても静かに壊れる規約」を担保している検査で、壊れたことは
その検査が守っていたものが壊れたときにしか分からない。検査自身が壊れた状態は
どの経路からも赤にならない。

### `check-package-shape.py` には前段の refactor が要る

`scripts/` の Python のうち、`__main__` ガードを持たないのはこれだけ (実測)。
`importlib` で読み込んだ瞬間に検査本体が走るため、テストから import して個々の
検査関数を呼ぶ形が取れない。テストを書く前に import 安全化が要る。

`scripts/test_run_python_tests.py` がハイフン名のスクリプトを `importlib` で読む定型を
持っているので、そこは転用できる。

### import 安全化は `SKIP_DIRS` の二重化も解く

`check-package-shape.py` と `run-python-tests.py` は走査除外の集合をそれぞれ literal で
持っていて、同期の根拠は「`check-package-shape.py` の集合に `.cache` を足したもの」という
コメントの散文しかない。片方に新しい除外が足された瞬間、この主張は CI が捕捉できない形で
偽になる。共有できないのは import すると検査本体が走るからで、原因は上と同じである。

### `check-leak-guard-rules.py` は外部コマンドに依存する

このスクリプトは `gitleaks` を実際に起動して対照を検証する。テストを書くときに、
`gitleaks` が無い環境でどう振る舞うべきか (検査不能として落とすのか、別の形にするのか) を
決める必要がある。runner は skip されたテストを一律で赤にするため、skip による回避は
選べない。

## タスク

- [ ] `check-package-shape.py` を import 安全化する (`__main__` ガードを足し、
      モジュールレベルで走っている検査本体を関数へ移す)
- [ ] `SKIP_DIRS` の二重化を解く。基底集合を共有するか、共有しないと決めたなら
      「同じ集合である」と主張しているコメントの方を落とす (検証されない同期主張を残さない)
- [ ] `check-package-shape.py` のテストを書く
- [ ] `gen-readme.py` のテストを書く
- [ ] `check-leak-guard-rules.py` のテストを書く。`gitleaks` 不在時の扱いを先に決めること
      (runner が skip を一律赤にするため、skip での回避はできない)
- [ ] 各テストが生きた pin であることを変異注入で確認する

## 関連

- [ISSUE-8 (closed): run-python-tests.py の件数ガードが実質 1 件で機能していない](../closed/ISSUE-8_run-python-tests.py%20の件数ガードが実質%201%20件で機能していない/issue.md) —
  この Issue の前提を作った。収集の仕組みと限界の canonical は
  `scripts/run-python-tests.py` の docstring
- `scripts/test_run_python_tests.py` — ハイフン名スクリプトを `importlib` で読む定型
- [ISSUE-13: 両取り付けの同時撤去を機構で検出できない](../ISSUE-13_両取り付けの同時撤去を機構で検出できない/issue.md) —
  `check-package-shape.py` へ規則を足す案が出ている。触る対象が重なるので、
  この Issue の import 安全化が先に入るなら合わせて考える
- ISSUE-30: component の定義が README 生成器と形の検査に分裂している。`check-package-shape.py` の import 安全化を前段として共有する。この Issue はテストの不在を、あちらは定義の所在を扱う
