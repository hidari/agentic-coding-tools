---
status: open
---

# perf: リモート往復数を減らす

## 背景

PR #3 のマージ前レビュー (効率観点) で挙がった。このツールのコストは SSH / scp / prlctl の
**プロセス起動と往復回数**が支配的で、ミリ秒単位の計算量ではない。

多重化 (`ControlMaster auto` / `ControlPersist 60s`) は既に `~/.ssh/config` に設定済み。
その上での実測は次のとおり。

| 操作 | 実測 |
|---|---|
| 多重化済み SSH 1 往復 (cmd 組み込みのみ) | 0.04〜0.05 秒 |
| 多重化済み SSH 1 往復 (外部 exe を起こす `where pwsh`) | 0.08〜0.14 秒 |
| `prlctl list -a -i -j` | 0.21 秒 / 9.7 KB |

現状の往復数 (コードから数えた値):

| サブコマンド | 往復 | 内訳 |
|---|---|---|
| `exec` / `health` | 4 | probe + scp + 実行 + 後始末 |
| `push` | 3 | mkdir + scp + サイズ照合 |
| `pull` | 2 | サイズ照合 + scp |
| `screenshot` | 2 | prlctl list + prlctl capture |

## 候補 (それぞれ独立に採否を決める)

### A. 後始末を pwsh から cmd の del へ

`pwsh_cleanup_command` はファイル 1 個を消すために .NET ホストの PowerShell 7 プロセスを
リモートで起こしている。SSH のログインシェルは既に cmd.exe なので `del /f /q` は組み込みで
プロセス生成ゼロ。

往復数は変わらないが**信頼性が上がる**: 後始末が存在する理由は「実行が失敗しても残骸を
消す」ことなのに、pwsh が起動できないケースでは後始末も同じ理由で失敗し `.ps1` が漏れる。
ISSUE-4 で実際にその状態に当たっている (VM 側は MSI 版の導入で解消済みだが、pwsh の可否に
後始末が乗っている構造は変わっていない)。cmd の `del` なら pwsh の可否と独立に消える。

**単独で正当性があるので最初に採るとよい。**

### B. probe を本実行に畳む

[ISSUE-4](../ISSUE-4_VM の pwsh probe が偽陽性で exec と health が実機で動かない/issue.md)
と結合する。probe の意味自体を直すのが先。

### C. exec / health を stdin 経由の 1 往復にする

`ssh host "pwsh -NoProfile -Command -"` にスクリプトを stdin で流せば 4 往復が 1 往復に
なる。`remote_ps1_path` の一意名も後始末も丸ごと不要になる。

トレードオフ: 「転送失敗」と「実行失敗」を切り分けられなくなる。`$PSCommandPath` /
`$MyInvocation` も使えなくなる。採用前に実機で exit code 伝搬の確認が要る。

### D. push の mkdir をハッピーパスから外す

親ディレクトリが存在する既定ケースでも mkdir の SSH を必ず 1 本張っている。先に scp を
試し、失敗したときだけ mkdir して 1 度だけ再試行する形にすると 3 往復が 2 往復になる。

トレードオフ: scp の失敗要因は親不在に限らないので、盲目的な再試行はエラー帰属を曖昧に
する。既存ディレクトリへ繰り返し投げる用途が主なら採る価値がある。

### E. cmd_run の削除 / mkdir をバッチ化

`parent_mkdir_commands` が 1 コマンド 1 往復にしている理由は「cmd の `if ... & if ...`
連結は最初の if が偽だと連鎖全体が実行されない」ことだった。PR #3 が追加した
`build_exec_powershell` + `pwsh -File` 経路はシェルを経由しないのでこの制約が無い。
`.ps1` の中なら `New-Item -Force` と `Remove-Item -Force` を何行でも並べられる。

20 ファイル / 8 ディレクトリ / 3 削除の変更で 34 往復 → 25 往復 (26% 減)。削除と mkdir が
件数に線形だったのが 2 往復固定になる。

診断情報は失わない: 現状の失敗メッセージは対象ファイル名を含んでいないので、まとめても
表に出ている情報は減らない。

第 2 段 (別判断): 20 回の per-file scp は `tar -cf - <files> | ssh host "tar -xf -"` で
1 往復に畳める (VM 側に `C:\Windows\System32\tar.exe` があることは実測済み)。25 → 6 往復。
ただしこちらは `error: scp 失敗: {f}` がファイル名を出しているので帰属を失う。

## 検討して棄却したもの

- `cmd_pull` の事前サイズ照会: 「不在の検出」と「照合の基準値取得」を兼ねており 2 往復が下限
- `cmd_screenshot` の `_load_vms` 事前確認: `--output` で絞ってもプロセス数は変わらず、
  実測 0.21 秒で支配的でない (実測して棄却)
- `push` / `pull` のサイズ照合そのもの: 途中で切れた転送を完了と報告しないために払っている

## タスク

- [ ] A を実装する (単独で正当性がある)
- [ ] ISSUE-4 の解決後に B を検討する (依存を宣言しているのは B だけ)
- [ ] C を検討する。ISSUE-4 とは独立に着手できるが、probe ごと畳む案なので B と範囲が重なる
- [ ] D の採否を用途から決める
- [ ] E を実装する (第 2 段の tar は別判断)
- [ ] 各変更の後に往復数を実測で確認する (数えられる形の根拠を残す)

## 関連

- `skills/devops/windows-vm-verification/winvm.py` — `pwsh_cleanup_command`、`cmd_push`、
  `cmd_run`、`parent_mkdir_commands`
- 併記: `winvm exec` には `--repo` が無く cd もしないが、SKILL.md は「クォート/パイプを
  含む複雑なコマンドは exec を使う」と誘導している。誘導どおり乗り換えると run の自動 cd を
  失うため、repo 内でパイプ付きコマンドを走らせる組み合わせがどのサブコマンドでも
  表現できない。実行系をサブコマンドではなくオプション (`run --shell pwsh`) にする案がある
