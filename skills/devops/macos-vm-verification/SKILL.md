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

## 検証 VM の扱い (スナップショットと停止)

検証 VM は検証のたびに原状へ戻し、止めてから報告する。起動したままの VM や、作業で取ったスナップショットを残したまま報告しない。macvm はこの扱いを持たないので `prlctl` を直接使う。

1. 作業前に `prlctl snapshot-list "<vm>" -H` で既存のスナップショットを確かめる。出力が空でなければ既存のものには触れず、作業に入る前にユーザーへ扱いを確かめる。`-H` を付けないと、スナップショットが 0 件でも見出しの行が 1 行出るので空にならない。`-H` は VM 名の後ろに置く。前に置くと VM 名がオプションとして読まれ、Unrecognized option で rc 255 になる (prlctl 27.0.2 で実測)。この手順で消してよいのは、この作業で取ったスナップショットだけである
2. 原状保全のスナップショットを取る (`prlctl snapshot "<vm>" --name <名前>`)。ID は `prlctl snapshot-list "<vm>"` で控える
3. 作業する。途中の状態へ戻る必要があれば、途中のスナップショットを取ってよい
4. 検証が済んだら `prlctl snapshot-switch "<vm>" --id <原状保全の ID>` で原状へ戻し、この作業で取ったスナップショットを `prlctl snapshot-delete "<vm>" --id <ID>` で全部消す
5. `prlctl list -a` で VM の状態を見て、`stopped` でなければ止める。原状保全を停止中に取ったなら、手順 4 で戻した時点で `stopped` になっている。`suspended` のときに取ったなら、戻した先も `suspended` になる (実測)
   - `suspended` か `paused` なら、先に `prlctl resume "<vm>"` で `running` へ戻す。一時停止の VM へ stop を打つと、`suspended` では `--kill` を付けても Failed を出しながら rc 0 を返して何も変わらず、`paused` では素の stop が rc 255 になる (どちらも実測)
   - `running` なら `prlctl stop "<vm>" --kill` で止める。素の `prlctl stop` は、起動中の macOS VM に対して Failed (Operation canceled) で rc 255 を返し、VM は起動したまま残る (Apple の仮想化で動く VM で実測)。手順 4 で原状へ戻した後なので、強制停止で失うものは無い
   - 停止中の VM へ stop を打つと、Failed を出しながら rc 0 を返すので打たない
   - 止めたら `prlctl list -a` で `stopped` になったことを確かめ、なっていなければこの手順をやり直す。`--kill` が rc 0 で「forcibly stopped」を返した直後に、VM が起動し直して `running` に戻ったことが 1 回あった (原因は未特定で、同じ順で 2 回試して再現しなかった)
6. 報告の前に、`prlctl snapshot-list "<vm>" -H` の出力が空 (手順 1 でユーザーが残すと決めたものがあるなら、それだけ) であることと、`prlctl list -a` で VM が `stopped` であることを確かめ、その出力を報告に添える

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

exit code は 1 だけではない。ゲストまで到達できなかった場合はリモートの値がそのまま返る
(ssh が到達できないときの 255 など)。呼び出し側が分岐するなら `rc == 1` ではなく
`rc != 0` を見ること。`rc == 1` だけを見ると、検証していないものを「欠けなし」として通す。

### `screenshot`

```
macvm screenshot --vm <名前 or UUID> --out <ホスト側の path.png>
```

`prlctl capture` でホスト側から VM の画面を PNG に撮る。**SSH 越しに `screencapture` を叩かない**。
SSH セッションは Aqua セッションと分離しており、対話ユーザーのデスクトップが見えない。

`prlctl capture` の rc 0 を成功と読み替えず、ファイルの実体とサイズを見てから成功を報告する。
保存先の親ディレクトリは自動作成する。

`--out` は**ホスト側**のパス。ホストもゲストも macOS で `/Users/<name>/...` が同じ形になるので、
ゲスト内のつもりで渡すとホスト側に黙ってディレクトリができる。ゲスト内のファイルが要るときは
`pull` を使う。

### `push` / `pull`

```
macvm push --host <alias> <local> <remote>
macvm pull --host <alias> <remote> <local>
```

scp で 1 ファイルを転送し、**転送後にサイズを照合する**。scp の rc 0 を「完了」と読み替えない。

`pull` はリモート不在を転送前に検出して止める。後段のサイズ照合の失敗に化けさせると、
「壊れた」のか「無かった」のかが読めなくなる。

- 転送先の親ディレクトリ (push は VM 側、pull はホスト側) は自動作成する。事前に
  `macvm exec -- 'mkdir -p ...'` を挟む必要は無い
- **リモートパスに `~` は使えない** (exit 2)。クォートするとゲスト側で展開されないため、
  macvm が組み立てるコマンドがリテラルな `~` ディレクトリを指す。ssh の作業ディレクトリは
  `$HOME` なので、`w/a.dmg` のような相対パスが等価な書き方になる。`--repo` も同じ
- **リモートパスはファイル名まで書く** (exit 2)。`w/` のような末尾 `/` や空文字は
  受け付けない。ディレクトリを渡すと scp とサイズ照合が同じものを見ないため、push では
  成功した転送を「途中で切れた」と誤報し、pull ではそもそも転送が成立しない
- リモートのサイズ問い合わせは `stat -L` で symlink を辿る。`[ -f ]` も scp も辿るので、
  3 者を揃えないと Homebrew の `bin/` のような symlink で「転送が途中で切れた」と誤報する
- 不在は ASCII の目印に固定してある (値は `macvm.py` の `REMOTE_MISSING_MARK` が canonical)

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
