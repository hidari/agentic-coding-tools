---
status: closed
---

# feat: Parallels 上の macOS VM を検証に使う skill を追加する

## 背景

GUI を持つ macOS アプリを開発していると、ビルドのたびにホストで実際にアプリを起動して
目視する必要がある。これには 2 つの問題がある。

- 作業中のインスタンスを落とさないと新しいビルドを確認できない (`open` は既存インスタンスを
  activate するだけ)。開発者が使っているアプリを検証のたびに終了させることになる
- エージェントが自走できない。画面を見る手段が無いので、目視は必ず人間の手番になる

Parallels 上の macOS VM を使えばどちらも解ける。VM 側でアプリを起動すればホストの
インスタンスに触れず、`prlctl capture` で撮った画面はエージェントが読める。

`windows-vm-verification` (winvm) が Windows VM に対して同じ役割を果たしているので、
対称な skill として macOS 版を置く。

### 実測で確認した経路

ホストと VM の間で、次の鎖が通ることを確認済みである。

```
prlctl start -> IP 解決 -> SSH (鍵認証) -> open -a でアプリ起動 -> prlctl capture -> 画像を読む
```

その過程で、winvm の知見がそのまま当てはまる面と、macOS 固有の差が両方見つかった。

| 項目 | 実測 |
|---|---|
| `prlctl capture` | macOS ゲストでも動く。SSH セッションからの画面取得は Windows と同じ理由で不可なので、ホスト側から撮る設計は共通 |
| GUI アプリの起動 | Windows と違い `open -a` が SSH セッションから Aqua セッションのアプリを起動できる。`launchctl asuser` は不要 |
| Aqua セッションの前提 | ログイン画面のままだと `open` は rc 0 を返しつつ何も表示しない。自動ログインか手動ログインで Aqua セッションが要る |
| Remote Login の有効化 | `systemsetup -setremotelogin` は Full Disk Access を要求して失敗する。`launchctl load -w /System/Library/LaunchDaemons/ssh.plist` は要求せずに通る |
| `prlctl exec` の引数 | root で動くが引数が再分割される。`sh -c 'echo hello > f'` がファイルへ改行 1 バイトだけ書いた (コマンド本体が失われても成功扱いになる) |
| `prlctl exec` の stdin | パイプが通らない (`PrlJob_GetRetCode: Invalid argument`) |
| `prlctl exec` の書き込み先 | 一部のパスへの `mv` が同じエラーで失敗する。特権が要る操作は SSH + sudo へ回すのが確実 |
| root で作ったホームの `.ssh` | 所有者が root のままだと sshd の StrictModes が鍵を拒否する。所有者とパーミッションの修正が必須 |

`prlctl exec` の壊れ方が Windows 側 (ISSUE-10 が記録している「空出力 + exit 2」) と違う点は
重要である。macOS ゲストでは**エラーにならず部分的に実行される**ため、成功したように見える。

## タスク

- [x] `macvm.py` を実装する (`resolve-ip` / `doctor` / `screenshot` / `push` / `pull` / `exec` / `health`)
- [x] `test_macvm.py` を書き、`scripts/python-tests-manifest.txt` を更新する
- [x] `SKILL.md` を書く
- [x] `references/ssh-config.template` を置く (ProxyCommand + ControlMaster)
- [x] `references/macos-bootstrap.md` に VM 側の準備手順を書く (Remote Login・鍵配置・自動ログイン)
- [x] `references/troubleshooting.md` に繋がらないときの切り分けを書く
- [x] full chain を実 VM で live smoke 実行する (純粋ロジックの緑だけで完了にしない)
- [x] `python3 scripts/gen-readme.py` で README を再生成する
- [x] ISSUE-10 へ macOS ゲストでの `prlctl exec` の壊れ方を追記する

## 関連

- ISSUE-10 — `prlctl exec` を直接叩くときの注意。本 Issue の実測は同じ根を macOS ゲストで
  踏んだもので、壊れ方の形が違うため向こうへ追記する
- ISSUE-1 — 外部コマンド出力の decode。macOS ゲストは UTF-8 なので CP932 の問題は出ないが、
  `errors="replace"` を外す理由にはならない (判定に使う目印を ASCII に保つ方針は共通)
