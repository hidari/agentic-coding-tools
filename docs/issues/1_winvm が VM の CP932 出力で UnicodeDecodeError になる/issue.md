---
status: open
---

# fix: winvm が VM の CP932 出力で UnicodeDecodeError になる

## 背景

relay の Windows 検証 VM (ja-JP の Windows 11 ARM) を winvm で操作している最中に実際に踏んだ。

Windows PowerShell 5.1 は非対話セッションの出力を OEM コードページで書く。ja-JP 環境では
CP932 になる。winvm はそれを UTF-8 として decode するため、日本語を含むエラーが出た瞬間に
winvm 自身が例外で落ちる。

VM 側が失敗を報告しようとしたときに限って落ちるので、いちばん情報が要る場面で
いちばん壊れる。

## 現状

`skills/devops/windows-vm-verification/winvm.py:51`

```python
def run_capture(argv: list[str]) -> tuple[int, str, str]:
    """argv を実行して (returncode, stdout, stderr) を返す。実行不能も戻り値で表す。"""
    try:
        p = subprocess.run(argv, capture_output=True, text=True)
    except OSError as e:
        return 127, "", f"{argv[0]} を実行できません: {e}"
    return p.returncode, p.stdout, p.stderr
```

`text=True` は `locale.getpreferredencoding()`(macOS では UTF-8) で decode し、
`errors` を指定していないので既定の `strict` になる。CP932 のバイト列が来ると
`UnicodeDecodeError` を送出する。

`except OSError` はプロセスを起動できない場合しか捕まえないので、この例外は
そのまま上へ抜ける。

### 影響を受ける経路

VM の出力を捕捉するものだけが該当する。

| 関数 | 行 | 影響 |
| --- | --- | --- |
| `ssh_capture` | 452 | **該当**。VM の stdout/stderr をそのまま decode する |
| `_ssh_reachable` | 258 | **該当**。出力を捨てるが decode は先に走る |
| `git_local` | 448 | 該当しない (Mac 側の git、UTF-8) |
| `cmd_resolve_ip` / `cmd_doctor` | 191 / 356 | prlctl 経由。ホスト側の出力なので通常は安全 |
| `run_ssh` / `scp` | 458 / 462 | **該当しない**。捕捉せず端末へ流すので decode しない |

`run_ssh` が安全なのは偶然ではなく、456 行のコメントにあるとおり「進捗を端末へ流す」
ために意図的に捕捉していないため。捕捉する側だけが危ない。

## 対応

同じファイルの 350 行が既に `errors="replace"` を使っており、先例がある。

```python
text = (Path(home) / "config.pvs").read_text(encoding="utf-8", errors="replace")
```

案は 2 つ。

### 案 A: `errors="replace"` を足す (最小)

```python
p = subprocess.run(argv, capture_output=True, text=True, errors="replace")
```

CP932 の日本語は化けるが、winvm は落ちない。winvm が VM 出力から読み取るのは
基本的に ASCII の目印 (コミット SHA、パス、`RELAY_REPO_EXISTS` 系のマーカー) なので、
判定には影響しない。化けるのは人間向けのエラー文だけ。

### 案 B: bytes で受けて decode を段階化する

`capture_output=True` のまま `text` を外し、`utf-8` → `cp932` → `replace` の順で試す。
日本語のエラー文が読める形で残る。実装は増える。

relay の Rust 側は同種の問題に案 A 相当 (`String::from_utf8_lossy`) で対処し、
「目印は ASCII に保つ」という前提を明示している。同じ前提を winvm でも取るなら案 A で足りる。

## タスク

- [ ] 案 A / 案 B を決める (目印が ASCII に保たれている前提が成り立つか確認してから)
- [ ] `run_capture` を修正する
- [ ] CP932 のバイト列を stdout / stderr に出すダミーコマンドで、例外にならないことをテストする
- [ ] 変異注入で確認する: `errors` の指定を外すとそのテストが赤くなること
- [ ] `_ssh_reachable` のように出力を捨てる経路でも落ちないことをテストに含める
- [ ] 実機 (ja-JP の Windows VM) で、日本語のエラーを出すコマンドを `winvm run` 経由で踏む

## 関連

- `skills/devops/windows-vm-verification/winvm.py` — `run_capture` (51 行)、`ssh_capture` (452 行)
- 同ファイル 350 行 — 既に `errors="replace"` を使っている先例
- relay PR [#588](https://github.com/HermitianHQ/relay/pull/588) — Rust 側で同種の問題に
  `from_utf8_lossy` で対処し、目印を ASCII に保つ前提を明示している
