---
status: open
---

# refactor: health と exec が同じ ps1 プロトコルを二重実装している

## 背景

PR #3 のマージ前レビューで、独立した 3 つのレビュー観点 (再利用 / 簡素化 / 実装の深さ) が
それぞれ独立にこれを最優先で挙げた。

`cmd_health` と `cmd_exec` は、次の 6 段の手順を丸ごと別々に実装している。

1. pwsh probe
2. ローカルに一時 `.ps1` を書く
3. `remote_ps1_path` で転送先を決める
4. scp
5. `pwsh -File` で実行
6. リモートの後始末 + ローカルの unlink

共有されているのは文字列ビルダ (`pwsh_file_command` / `pwsh_cleanup_command` /
`remote_ps1_path` / `PWSH_PROBE_ERROR`) だけで、手順そのものは共有されていない。

### drift が既に 2 回起きている

これは仮定ではなく観測できる事実である。

- `8d5a1b9` の時点で内側の `try/finally` (実行が例外で抜けてもリモートの `.ps1` を消す)
  は exec にしか無く、health は例外時に残していた。PR #3 のレビューで health へ後追いした
- `copy=` の注入点も exec が先に持ち、health は `scp` 直呼びで scp 失敗経路がテスト不能
  だった。これも同じレビューで後追いした

同じ修正が 2 回とも「片方 → 遅れてもう片方」の順で入っている。3 回目も同じ形になる。

### 派生: `run=` に 3 つの非互換な契約が乗っている

引数名は全部 `run` だが、契約が 3 通りある。

| 使用箇所 | 既定 | 契約 |
|---|---|---|
| `cmd_resolve_ip` / `cmd_doctor` / `cmd_screenshot` | `run_capture` | `argv -> (rc, out, err)` |
| `cmd_health` / `cmd_push` | `run_ssh` | `(host, cmd) -> bool` |
| `cmd_exec` | `run_ssh_code` | `(host, cmd) -> int` |

bool と int の分岐は「health は成否だけ欲しい / exec は exit code が欲しい」という上位の
都合が、下位のトランスポート層の型に漏れたもの。

**静かな事故経路がある**: `run_capture` 形のスパイを誤って `cmd_health` に注入すると、
返る tuple が常に truthy なので `if not run(host, pwsh_probe_command())` を素通りする。
probe が効いていないのにテストは緑で通る。型が違うのに例外にならず「検査を通過した」
という形で返るので、出力を見ても気づけない。

## タスク

- [ ] `run_ps1_on_vm(host, ps_text, kind, *, run, copy) -> int` を作り、probe・一時ファイル・
      scp・実行・後始末・unlink を所有させる
- [ ] `cmd_health` / `cmd_exec` を新ヘルパの呼び出し 1 行にする
- [ ] `run=` の命名をトランスポートで割る (`prlctl=` / `ssh=` / `copy=` / `capture=`)。
      `.ps1` 経路は int 一本に揃え、bool 版 `run_ssh` は cmd.exe ワンライナー用に残す
- [ ] テストのスパイを 1 つに統合する (`CmdHealth._spy` と `CmdExec._exec` は同型で、
      前者の docstring が自分でそう書いている)
- [ ] 重複しているテストを畳む (`test_two_invocations_do_not_share_a_remote_path` は
      2 箇所に同一内容で存在する)
- [ ] 統合後に `winvm health` と `winvm exec` の full chain を live smoke する
      (かつては Issue [#4](../4_VM の pwsh probe が偽陽性で exec と health が実機で動かない/issue.md)
      の解決待ちだったが、VM へ MSI 版 pwsh を入れた時点で smoke は可能になっている。
      #4 に残っているのは probe の判定を直す作業で、live smoke を妨げない)
- [ ] 変異注入で確認する

## 関連

- `skills/devops/windows-vm-verification/winvm.py` — `cmd_health`、`cmd_exec`、
  `run_ssh` / `run_ssh_code`
- 本 Issue は PR #3 に同梱しなかった。トランスポートの契約変更は live smoke なしに
  入れるべきでなく、当時はその live smoke が Issue #4 に阻まれていたため (現状はタスク欄のとおり)
