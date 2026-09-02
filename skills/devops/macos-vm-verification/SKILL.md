---
name: macos-vm-verification
description: Parallels Desktop 上の macOS 検証 VM を繋ぐ/調べる/検証する generic CLI (macvm)。SSH 越しの健全性確認 (OS / arch / ディスク / GUI セッション / 任意ツールの有無)、ホスト側からの画面キャプチャ (screenshot)、prlctl からの IP 解決、繋がらないときのホスト側診断 (doctor)、任意ファイルの転送 (push/pull)、クォート/パイプ安全な任意コマンド実行 (exec) を扱う。GUI アプリをホストの作業を止めずに起動して目視したい時や、Parallels Desktop の macOS VM を操作・検証する時に使う。
---

# macOS VM 検証スキル (macvm)

## いつ使うか

- GUI アプリを起動して画面を目視したいが、ホストで動いているインスタンスを落としたくない
- 検証の目視をエージェントに任せたい (`screenshot` が撮った PNG は読める)
- SSH 経由で VM の OS バージョン・アーキテクチャ・ディスク・開発ツールを確認したい
- VM に繋がらず、原因がホスト側 (VM 未起動 / IP 未割当) かゲスト側 (Remote Login / 鍵) かを切り分けたい
- IP が変わって SSH 接続先が不明になった
- ビルド成果物などの任意ファイルを VM と往復させたい (`push` / `pull`)
- パイプやクォートを含む任意のコマンドを VM で実行したい (`exec`)

Windows VM に対する同じ役割は `windows-vm-verification` (winvm) が持つ。ホスト側 (prlctl) の
扱いは意図的に同じ形にしてあるので、片方を知っていればもう片方も読める。

## macvm CLI 概要

`macvm.py` は uv で実行する単一ファイル CLI。設定は **環境変数**でも **引数**でも渡せ、引数が優先する。

| 環境変数 | 対応引数 | 意味 |
|---|---|---|
| `MACVM_VM` | `--vm` | Parallels の VM 名または UUID (`prlctl list -a` で確認) |
| `MACVM_HOST` | `--host` | SSH ホスト名 (ssh config alias) |
| `MACVM_REPO` | `--repo` | `health` で存在を確認するリポジトリパス |

VM の指定は名前でも UUID でも通る。`prlctl` が受け付ける識別子と同じ集合に揃えてあるので、
`macvm` と `prlctl` を混ぜて使っても指す VM がずれない。名前は完全一致で、部分一致はしない。

## サブコマンド

### `resolve-ip`

```
macvm resolve-ip --vm <名前 or UUID>
```

`prlctl list -a -i -j` の JSON から該当 VM の `Network.ipAddresses` を読み、`type` が `ipv4` の
エントリを標準出力に出す。解決できなければ非 0 終了。

APIPA (169.254.0.0/16) だったときは stderr に警告を出すが、**stdout と exit code は変えない**。
このコマンドは ssh config の `ProxyCommand` の中で動くので、stdout はそのまま `nc` の接続先に
なる。exit code を非 0 にすると `nc` が空文字を掴む。

### `doctor`

```
macvm doctor --vm <名前 or UUID> [--host <alias>]
```

VM が使える状態かをホスト側から観測する。各項目は判定だけでなく**観測値**を出す。

- VM 登録 / VM 状態 / Parallels Tools / IP を prlctl から見る
- `--host` を渡すと SSH 到達性と **GUI (Aqua) セッションの有無**も見る

GUI セッションの確認が要るのは、ログイン画面のままだと `open` が rc 0 を返しつつ何も表示
しないためである。この状態は「アプリが起動しない」ではなく「起動先が無い」なので、
アプリ側をいくら調べても分からない。

`[ -- ]` は「確認できなかった」で、OK でも NG でもない。exit code は NG が 1 つでもあれば 1。

### `health`

```
macvm health --host <alias> [--repo <path>] [--check-tools "git, cargo"]
```

SSH 越しに VM の健全性を観測する。OS バージョン・アーキテクチャ・ディスク空き・console の
所有者を必ず出し、`--check-tools` を渡すとコマンドの有無を、`--repo` を渡すとディレクトリの
有無を確認する。欠けがあれば exit 1 だが、**途中で止めずに全項目を出してから終える**
(最初の失敗で打ち切ると、残りが健全かどうかが分からないまま報告になる)。

### `screenshot`

```
macvm screenshot --vm <名前 or UUID> --out <path.png>
```

`prlctl capture` でホスト側から VM の画面を PNG に撮る。**SSH 越しに `screencapture` を叩かない**。
SSH セッションは Aqua セッションと分離しており、対話ユーザーのデスクトップが見えない。

`prlctl capture` の rc 0 を成功と読み替えず、ファイルの実体とサイズを見てから成功を報告する。

### `push` / `pull`

```
macvm push --host <alias> <local> <remote>
macvm pull --host <alias> <remote> <local>
```

scp で 1 ファイルを転送し、**転送後にサイズを照合する**。scp の rc 0 を「完了」と読み替えない。

`pull` はリモート不在を転送前に検出して止める。後段のサイズ照合の失敗に化けさせると、
「壊れた」のか「無かった」のかが読めなくなる。

### `exec`

```
macvm exec --host <alias> -- '<command>'
```

任意コマンドを `.sh` に書いて scp し、`sh` で実行して後始末する。**リモートの exit code を
そのまま macvm の exit code にする**。

スクリプトファイルに書くのは、`ssh host "..."` が argv を空白連結してクォートを落とすため。
ファイル経由ならパイプもリダイレクトもクォートも意図どおり解釈される。

GUI アプリの起動もこれで行う。macOS では `open -a` が SSH セッションから Aqua セッションの
アプリを起動できる (Windows の session 0 分離に相当する壁が、起動については無い)。

```
macvm exec --host <alias> -- 'open -a <App>'
macvm screenshot --vm <名前> --out shot.png
```

## 接続セットアップ

初回だけ VM 側の準備が要る。手順と、その過程で踏む落とし穴は
`references/macos-bootstrap.md` が持つ。ssh config は `references/ssh-config.template` を
写して使う。

要点だけ挙げると次の 3 つで、どれも欠けると症状が「繋がらない」ではなく別の形で出る。

- Remote Login が無効だと SSH ポートが開かない
- root で作ったホームの `.ssh` は sshd の StrictModes に弾かれる (鍵は配置済みなのに Permission denied)
- GUI セッションが無いと `open` は成功したように見えて何も表示しない

## トラブルシューティング

繋がらないときは `macvm doctor --vm <名前> --host <alias>` を最初に回す。どの層で止まって
いるかが観測値つきで出る。切り分けの詳細は `references/troubleshooting.md` を参照する。
