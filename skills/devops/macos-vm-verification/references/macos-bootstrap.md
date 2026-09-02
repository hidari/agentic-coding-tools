# macOS 検証 VM の初回セットアップ

Parallels Desktop に作った macOS VM を macvm から使えるようにする手順。実測 (Parallels
Desktop 27.0.0-58628 / macOS 26.6.2 ゲスト) に基づく。

前提として、ホストから `prlctl` が使えて VM が起動していること。`prlctl exec` はゲスト内で
**root として**動くので、SSH がまだ無い段階の踏み台に使える。ただし後述の制約がある。

## 1. Remote Login を有効にする

素直な方法は失敗する。

```
prlctl exec "<vm>" systemsetup -f -setremotelogin on
# -> setremotelogin: Turning Remote Login on or off requires Full Disk Access privileges.
```

`systemsetup` は TCC (Full Disk Access) を要求する。GUI で許可を与えない限り通らない。
launchd を直接叩く経路は TCC を要求せず通る。

```
prlctl exec "<vm>" launchctl load -w /System/Library/LaunchDaemons/ssh.plist
```

**無出力を成功の根拠にしない。** 実際にポートを見る。

```
nc -z -w 3 <vm-ip> 22; echo $?          # 0 なら開いている
prlctl exec "<vm>" systemsetup -getremotelogin   # Remote Login: On
```

## 2. 公開鍵を配置する

`prlctl exec` は root で動くので、一見すると `authorized_keys` を直接書けそうに見える。
だが**書けない**。この 3 つを実測した。

| やり方 | 結果 |
|---|---|
| `prlctl exec "<vm>" sh -c '... >> ~/.ssh/authorized_keys'` | 引数が再分割され、コマンド本体を失う。`sh -c 'echo hello > f'` がファイルへ**改行 1 バイトだけ**書いた |
| `cat key.pub \| prlctl exec "<vm>" tee -a ...` | `PrlJob_GetRetCode: Invalid argument` |
| `prlctl exec "<vm>" mv /tmp/x /etc/x` | `PrlJob_GetResult: Invalid argument` |

1 つ目が最も危険で、**エラーにならず部分的に実行される**。ファイルはできるので成功に見える。

引数が単純なコマンド (`mkdir -p <path>` / `chown -R <user>:<group> <path>` / `chmod 700 <path>`)
は通る。トークン数が少なくリダイレクトを含まないものだけが安全と考えてよい。

したがって鍵の配置は SSH 側から行う。VM のログインパスワードが要る。

```
ASKPASS=$(mktemp)
printf '#!/bin/sh\nprintf "%%s\\n" "$VM_PW"\n' > "$ASKPASS"
chmod 700 "$ASKPASS"
VM_PW="<password>" SSH_ASKPASS="$ASKPASS" SSH_ASKPASS_REQUIRE=force \
  ssh-copy-id -i <pubkey> -o StrictHostKeyChecking=accept-new <user>@<vm-ip>
rm -f "$ASKPASS"
```

`SSH_ASKPASS_REQUIRE=force` は OpenSSH 8.4 以降。パスワードは環境変数だけを通り、
askpass スクリプト本体にも argv にも残らない。パスワードマネージャから読むなら
コマンド置換で環境変数へ入れる。

### root で `.ssh` を作ってはいけない

`prlctl exec` で `mkdir -p ~<user>/.ssh` を先に作ると、そのディレクトリは **root 所有の 777**
になる。sshd の StrictModes はこれを拒否するので、鍵を正しく配置しても
`Permission denied (publickey,...)` が返る。鍵の内容を疑って時間を溶かす典型的な罠である。

作ってしまったら直す。

```
prlctl exec "<vm>" chown -R <user>:staff /Users/<user>/.ssh
prlctl exec "<vm>" chmod 700 /Users/<user>/.ssh
prlctl exec "<vm>" chmod 600 /Users/<user>/.ssh/authorized_keys
```

配置できたら **BatchMode で** 確認する。パスワード認証にフォールバックすると、鍵が効いて
いなくても繋がってしまい検証にならない。

```
ssh -i <key> -o BatchMode=yes -o ConnectTimeout=5 <user>@<vm-ip> whoami
```

## 3. GUI (Aqua) セッションを用意する

ログイン画面のままだと GUI アプリを起動できない。`open -a <App>` は **rc 0 を返しつつ何も
表示しない**ので、アプリ側を調べても原因に辿り着けない。

現在の状態はこれで分かる。

```
ssh <alias> 'stat -f %Su /dev/console'
# root  -> ログイン画面 (Aqua セッション無し)
# <user> -> ログイン済み
```

`macvm doctor --vm <名前> --host <alias>` は同じ観測を「GUI セッション」の行として出す。

手で解決するなら VM のコンソールでログインすればよい。エージェントに自走させるなら
自動ログインを設定する。

### 自動ログインの設定

**セキュリティ上のトレードオフがある。** macOS の自動ログインはパスワードを
`/etc/kcpassword` に置く。これは暗号化ではなく固定キーの XOR による難読化なので、
このファイルを読める者はパスワードを復元できる。検証専用で実データを置かない VM に限って
選ぶ判断であり、実データを扱う環境では採らない。

kcpassword は「パスワードを 12 バイト境界までゼロ埋めし、11 バイトの固定キー
(`7D 89 52 23 D2 BC DD EA A3 B9 1F`) を繰り返して XOR したもの」である。長さが既に 12 の
倍数でも 12 バイト足す (終端を示すゼロが要る)。

生成したファイルを VM へ送り、root 権限で配置する。`prlctl exec` は特権パスへの `mv` に
失敗するので、SSH + sudo を使う。パスワードは stdin で渡してコマンド文字列に載せない。

```
scp -i <key> kcpassword <user>@<vm-ip>:/tmp/kcpassword
printf '%s\n' "<password>" | ssh <alias> 'sudo -S -p "" sh -c "
  mv /tmp/kcpassword /etc/kcpassword;
  chown root:wheel /etc/kcpassword;
  chmod 600 /etc/kcpassword;
  defaults write /Library/Preferences/com.apple.loginwindow autoLoginUser <user>"'
```

再起動して確認する。ここでも「設定を書いた」ことを成功の根拠にせず、実際に console の
所有者が変わったかを見る。

```
prlctl restart "<vm>"
# SSH が復帰するまで待ってから
ssh <alias> 'stat -f %Su /dev/console'   # <user> になっていれば成功
```

## 4. ssh config と PATH を配線する

`references/ssh-config.template` を写して `~/.ssh/config` に置く。`ProxyCommand` が
`macvm resolve-ip` を呼ぶので、`macvm` が PATH にある必要がある。

配線できたら全体を通して確認する。

```
macvm doctor --vm "<vm>" --host <alias>
```

全項目が `[ OK ]` になれば、以降は `exec` でアプリを起動し `screenshot` で画面を撮れる。
