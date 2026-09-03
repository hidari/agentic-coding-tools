---
status: open
---

# fix(devops): winvm に残る同型の欠陥 (macvm の監査で判明)

## 背景

macvm は winvm からの写経で作られた。写経で落ちたガードを突合監査で洗い出して macvm 側を直した
ところ、その過程で **winvm 側にだけ残っている穴**と、**macvm の方が堅かった箇所**が見つかった。
macvm の修正 (PR #43) はスコープを macvm に限ったので、winvm 側はここへ切り出す。

以下はすべて自分で再現した観測。PowerShell は macOS 上で実行できないので、1 は生成本文の観測
までで、ゲスト上での実行は未確認。

### 1. `build_health_powershell` が値を単一引用符リテラルへ生で埋める

`winvm.py:649` が `", ".join(f"'{t}'" for t in check_tools)`、`winvm.py:671` が
`Test-Path '{repo}'`。PowerShell の単一引用符リテラルは `''` でしかエスケープできないので、値に
`'` が入るとリテラルの外へ出る。

実測 (`--check-tools "node,a'; whoami; '"` と `--repo "C:/repo'; whoami; '"` で生成):

```
foreach ($t in @('node', 'a'; whoami; '')) { ... }
if (Test-Path 'C:/repo'; whoami; '') { Push-Location 'C:/repo'; whoami; ''; ... }
```

`whoami` がリテラルの外にある。macvm 側の同型の穴は値をシェル変数へ束縛して直したが、
PowerShell には `shlex.quote` 相当が無いので手当ては別物になる。`t.replace("'", "''")` を
ヘルパへ切り出して 2 箇所から呼ぶ形が素直。

セキュリティ面の影響は限定的 (`exec` サブコマンドで同じ操作者が既に任意実行できる)。正当性の
欠陥として扱う。`Ken's repo` のような合法なパスで health が壊れる。

### 2. `pick_ipv4` が壊れた prlctl 出力で AttributeError になる

`winvm.py:117` の `(network or {}).get("ipAddresses")` は `network` が dict 以外だと落ちる。

実測:

| 入力 | winvm | macvm |
|---|---|---|
| `"not-a-dict"` | `AttributeError: 'str' object has no attribute 'get'` | `None` |
| `["a"]` | `AttributeError: 'list' object has no attribute 'get'` | `None` |
| `42` | `AttributeError: 'int' object has no attribute 'get'` | `None` |

`winvm.py:75-76` の `parse_vm_list` docstring は「壊れた出力で例外を投げず該当なしに倒す」と
方針を宣言しており、それと一貫しない。`resolve-ip` は ssh config の `ProxyCommand` の中で動く
ので、そこで traceback が出るのは設計されたエラーメッセージより悪い失敗の仕方になる。

macvm 側の `pick_ipv4` は `isinstance` ガードを持つ。逆輸入する。

### 3. `find_vm` が `{}` で ID キーを欠くレコードに誤ヒットする

`_normalize_uuid("{}")` は `""` を返し、`ID` キーを欠くレコードの `str(v.get("ID", ""))` も
`""` なので一致する。

実測 (`vms=[{"Name": "No ID Record"}, {"Name": "Guest", "ID": "{ABC-123}"}]`):

| 識別子 | winvm | macvm |
|---|---|---|
| `"{}"` | `{'Name': 'No ID Record'}` | `None` |
| `" {} "` | `{'Name': 'No ID Record'}` | `None` |
| `"{ }"` | `None` | `None` |

macvm は `if not want: return None` で塞いだ。同じ 1 行で塞がる。

### 4. `find_vm` の strip がテストで pin されていない

`winvm.py:97` の `ident = (identifier or "").strip()` を消しても `test_winvm.py` は緑のまま
(監査の変異注入で SURVIVED)。実測でも `find_vm` へ前後空白付きの識別子を渡す行は 0 件で、
`FindVm` クラスのテスト 7 本はいずれも空白を含まない識別子を渡している。

macvm 側には `test_surrounding_whitespace_is_absorbed_for_names_too` を足してある。

### 5. `%~zI` と symlink (未検証・調査項目)

macvm では `stat -f %z` が symlink を辿らず、`[ -f ]` と scp は辿るという非対称があった。
実 VM で `stat -f %z` = 33 (リンク先パスの文字列長) / `stat -L -f %z` = 4096 を観測して
確定させ、`-L` を足して直した。

winvm の `winvm.py:774` は `for %I ... %~zI` を使う。Windows の symlink / ジャンクションに
対して `%~zI` が何を返すか、scp が何を転送するかは検証していない。同型の欠陥である可能性が
残っている。Windows VM で確かめてから、直すか「非対称は無い」と記録するかを決める。

## タスク

- [ ] `build_health_powershell` のクォートを直す (`''` エスケープをヘルパへ切り出し、2 箇所から呼ぶ)
- [ ] 生成本文を実際に pwsh へ通すテストを足す (現行の部分文字列テストでは原理的に検出できない)
- [ ] `pick_ipv4` へ `isinstance` ガードを逆輸入し、非 dict 入力のテストを足す
- [ ] `find_vm` へ `if not want: return None` を足し、`{}` のテストを足す
- [ ] `find_vm` の strip を pin するテストを足す (前後空白付きの名前が引けること)
- [ ] Windows VM で `%~zI` と symlink の挙動を実測し、非対称があれば直す。無ければ docstring に記録する
- [ ] 変異注入で各テストに歯があることを確かめる (判定は落ちたテストの ID 集合と実行件数で行う)
- [ ] `winvm health` を実 VM で通す (シェル/CLI のセマンティクスはランタイムでしか壊れない)

## 関連

ISSUE-3 は `find_vm` の呼び出し側の重複を扱う。この Issue は `find_vm` の中身を直すので、
先にこちらを入れてから ISSUE-3 の抽出を行う方が差分が小さい。

ISSUE-4 は pwsh probe の偽陽性を扱う。項目 1 と同じ `build_health_powershell` 周辺に触れるが、
probe の有無とクォートは独立している。

ISSUE-51 で追加した macvm skill の監査から派生した。macvm 側の対応は PR #43 に入っている。
