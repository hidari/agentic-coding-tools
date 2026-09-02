# macvm トラブルシューティング

最初に `macvm doctor --vm <名前> --host <alias>` を回す。どの層で止まっているかが観測値
つきで出るので、以下は doctor の行に対応させてある。

## `[FAIL] VM 登録`

名前は完全一致で照合する。部分一致はしない (prlctl と指す VM がずれるのを避けるため)。
doctor は登録済みの名前を列挙するので、そこから正しい綴りを取る。UUID でも指定できる。

## `[FAIL] VM 状態`

`prlctl start "<vm>"` で起動する。起動直後は次の IP がまだ付いていないことがあるので、
数秒待ってから doctor を回し直す。

## `[FAIL] Parallels Tools`

Tools が入っていないと IP の解決も `prlctl capture` も使えない。VM のメニューから
Parallels Tools をインストールする。

## `[FAIL] IP` — 未割当

VM は起動しているが DHCP がまだ応答していない。数秒待つ。待っても付かないなら VM の
ネットワークアダプタの設定を見る。

## `[FAIL] IP` — 169.254.x.x (APIPA)

DHCP が取れていない。Parallels の共有ネットワークが停止しているか、VM のネットワーク設定が
壊れている。なお `resolve-ip` はこの場合も **stdout に IP を出し exit 0 で終える**。
`ProxyCommand` の中で動くため、非 0 にすると `nc` が空文字を掴んで別のエラーに化けるからで、
警告は stderr へ出る (ssh の呼び出し元へ素通しされるので人の目には届く)。

## `[FAIL] SSH`

順に切り分ける。

1. ポートが開いているか

```
nc -z -w 3 <vm-ip> 22; echo $?
```

閉じているなら Remote Login が無効。`references/macos-bootstrap.md` の手順 1 を見る。
`systemsetup` は Full Disk Access を要求して失敗するので、`launchctl load -w` を使う。

2. 鍵が効いているか

```
ssh -i <key> -o BatchMode=yes -o ConnectTimeout=5 <user>@<vm-ip> whoami
```

`Permission denied (publickey,...)` なら、鍵の内容より先に**パーミッション**を疑う。
root で作った `.ssh` は 777 になり sshd の StrictModes に弾かれる。

```
ssh <alias> 'ls -ld ~/.ssh; ls -l ~/.ssh/authorized_keys'
# .ssh は 700、authorized_keys は 600、どちらも所有者はログインユーザー
```

3. ProxyCommand が動いているか

```
macvm resolve-ip --vm "<vm>"   # IP が出るか
which macvm                     # PATH にあるか
```

ssh config の `ProxyCommand` は PATH 上の `macvm` を呼ぶ。シンボリックリンクを張った
直後は実行ビットが無いことがある (`permission denied` で exit 126 になる)。

## `[FAIL] GUI セッション`

ログイン画面のままで Aqua セッションが無い。この状態では `open -a` が **rc 0 を返しつつ
何も表示しない**ため、アプリ側を調べても原因に辿り着けない。

VM のコンソールでログインするか、自動ログインを設定する
(`references/macos-bootstrap.md` の手順 3)。

## SSH が完全に死んでいるとき

`prlctl exec` が代替チャネルになる。ゲスト内で root として動くので、設定の確認と修正に
使える。ただし制約が強い。

- **引数が再分割される。** `sh -c '...'` に渡した文字列は分割され、コマンド本体を失ったまま
  実行される。`sh -c 'echo hello > f'` がファイルへ改行 1 バイトだけ書いた実測がある。
  エラーにならないので成功に見える
- **stdin パイプが通らない** (`PrlJob_GetRetCode: Invalid argument`)
- **特権パスへの書き込みが失敗する。** `/etc` への `mv` が `PrlJob_GetResult: Invalid argument`
  で落ちた

したがって使えるのは、トークン数が少なくリダイレクトを含まないコマンドに限る
(`systemsetup -getremotelogin` / `ls -la <path>` / `chmod 700 <path>` など)。
複雑な操作は SSH を復旧させてから行う。

出力が空だったとき「そういう結果だった」と読まない。対照として必ず出力があるコマンド
(`prlctl exec "<vm>" whoami` など) を並べて、経路自体が生きているかを先に確かめる。

## 転送したファイルが壊れているように見える

`push` / `pull` は転送後にサイズを照合するので、サイズが合っていれば途中切れではない。
`error: 転送後のサイズが一致しません` が出たら転送が切れている。ControlMaster の接続が
古くなっている可能性があるので、`~/.ssh/cm-*` を消してから再試行する。
