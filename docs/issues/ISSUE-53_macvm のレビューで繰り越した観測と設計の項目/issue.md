---
status: open
---

# improve(devops): macvm のレビューで繰り越した観測と設計の項目

## 背景

PR #43 で macvm の写経監査由来の欠陥を直したあと、差分を 5 観点で独立にレビューし、各指摘を
反証で検証した。確定した 10 件のうち 7 件は同 PR で直し、残りをここへ繰り越す。

繰り越したのは「設計判断を要するもの」と「境界ケースで影響が限定的なもの」。いずれも
レビュー時点で実測を伴っており、着手する人が再現から始めなくて済むよう観測を残してある。

### 1. health の `command -v` が sshd の非対話 PATH で走る

`ssh host 'sh /tmp/...'` は login shell を経由しないので、macOS では `/etc/zprofile` (login
shell 用と自称) にある `path_helper` が走らない。`/etc/zshenv` は存在しないので、PATH は sshd
既定 + `~/.zshenv` の追記分だけになる。Homebrew の `brew shellenv` も rustup も既定では
`.zprofile` へ書くので、導入済みのツールが `MISSING` になりうる。

ホスト (ゲストと同じ macOS) での実測:

```
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/zsh -c 'echo $PATH'
  -> /usr/bin:/bin:/usr/sbin:/sbin   (何も足されない)
zsh -lc 'echo $PATH'
  -> path_helper 展開後の PATH
```

同じマシンで `build_health_shell(["git", "cargo"], None)` の生成本文を 2 通りの PATH で実行:

| PATH | 結果 |
|---|---|
| `/usr/bin:/bin:/usr/sbin:/sbin` | `tool_cargo=MISSING` / exit 1 |
| `$HOME/.cargo/bin:/opt/homebrew/bin:...` | `tool_cargo=/Users/example/.cargo/bin/cargo` / exit 0 |

`cargo` は実在する。SKILL.md が挙げる例そのもの (`--check-tools "git, cargo"`) が該当する。

health は観測値を読ませるための機構なのに `PATH` を出していないので、この偽 MISSING と本当の
未導入を出力から区別できない。少なくとも `echo "path=$PATH"` を観測値に足すべき。PATH を
login shell 相当へ寄せるかどうか (`zsh -lc` を挟む / `path_helper` を明示的に読む) は別の判断で、
挟むとゲストの rc ファイルの副作用を拾うので慎重に決める。

### 2. 転送先が「既存ディレクトリと同じ名前」の場合が残っている

PR #43 で末尾 `/` と空文字は exit 2 で拒むようにしたが、`macvm push a.dmg w` で `w` がゲスト側に
ディレクトリとして既に在る場合は書き方から判別できない。scp は `w/a.dmg` を作り、サイズ照合は
`[ -f 'w' ]` を見て `MACVM_MISSING` を返すので「転送が途中で切れた可能性」で exit 1 になる。

塞ぐならゲストへ問い合わせる (`[ -d ]` を先に見る) しかなく、ssh の往復が 1 回増える。往復を
増やす価値があるか、それとも照合失敗時のメッセージに「転送先がディレクトリだった可能性」を
足すだけにするかは判断が要る。

### 3. VM 状態 (State) の既定値が捏造した `"unknown"` のまま

`macvm.py` は 3 箇所で `str(vm.get("State", "unknown"))` を使う。`GuestTools` の側は
`parse_tools` + `UNKNOWN` で「読めなかった」を tri-state にしたが、State は畳んだまま。
State キーを欠くレコードでは `[FAIL] VM 状態 : unknown` と出て、prlctl が本当に `unknown` を
返した場合と区別できず、hint は `prlctl start` を勧める。

`UNKNOWN` 定数のコメントが宣言した規則を同じファイルが破っている形なので、揃えるなら
`resolve-ip` と `screenshot` 側の「起動していない」という断定も同時に見直すことになる。
影響範囲が 3 経路に跨るので分けた。

### 4. 外部入力が `-` で始まるとオプションとして解釈される

`stat` / `mkdir` / `command -v` / `scp` の引数に `--` を置いていない。`macvm push ./-L -L` の
ように `-` 始まりの相対パスやツール名を渡すと、値ではなくオプションとして読まれる。
`shlex.quote` はクォートするだけでこの層を守らない。

### 5. `echo` がバックスラッシュを解釈する

macOS の `/bin/sh` (bash の sh モード) の `echo` は `\n` などを解釈するので、
`--repo '/Users/example/proj\new'` のようなバックスラッシュを含む合法な macOS パスでは
観測値が改行で割れる。行単位で読む呼び出し側が値を取り違える。`printf '%s\n'` へ寄せるのが素直。

### 6. ローカル一時ファイルの書き込みが失敗すると残骸が出る

`_run_remote_script` の `tempfile` への書き込み中に例外が出ると、`unlink` する `finally` へ
到達する前に抜ける。非 UTF-8 のバイト列を含むコマンドを渡すと traceback で終わり、
TMPDIR に 0 バイトの `.sh` が残る。

### 7. `exec` の argv 連結が語境界を落とす

`remote_command_from_args` は `" ".join(remote)` なので、`macvm exec --host vm -- open -a "Google
Chrome"` はゲスト上で `open -a Google Chrome` になる。1 つの文字列で渡す
(`-- 'open -a "Google Chrome"'`) 形が正しいが、SKILL.md はその制約を書いていない。
`shlex.join` へ寄せるか、散文で制約を明示するかの判断。

### 8. `find_vm` の「名前優先」が prlctl の実挙動と一致するか未確認

PR #43 で 2 パス (名前を全件 → UUID を全件) にし、「名前一致をリスト全体で優先する」と
docstring に書いた。ただし prlctl 自身が同じ優先順位かは測っていない。VM の名前が別の VM の
UUID 文字列と一致するという病的な状況でのみ差が出るが、skill は「macvm と prlctl を混ぜて
使っても指す VM がずれない」と謳っているので、測って docstring の根拠を実測へ差し替えるか、
「この状況は未定義」と明記するかを決める。

### 9. テストのハーネスが push / pull で 2 系統ある

`CmdPush` / `CmdPull` は引数を捨てる lambda の `_push` / `_pull` と、呼び出しを記録する
`TransferSpy` の `_spied` を並置している。`TransferSpy` が上位互換なので 1 系統へ寄せられる。
あわせて、包含関係にあるテスト (`test_absent_remote_is_refused_before_transfer` が
`test_an_absent_remote_stops_before_the_transfer` の真部分集合など) を畳む。

### 10. リモートへ一時スクリプトを置く設計そのものの代替案

`_run_remote_script` は本文を `scp` してから `sh` で実行する。`ssh host 'sh -s'` へ stdin で
流せば、`remote_script_path` / `remote_sh_command` / `remote_cleanup_command` / uuid / 後始末の
`finally` / 競合の議論がまとめて消える。代償はリモートコマンドへローカルの stdin を渡せなく
なること (`cat x | macvm exec -- 'wc -l'`)。SKILL.md はスクリプトファイル方式の理由を
「`ssh host "..."` が argv を空白連結してクォートを落とすため」としか書いておらず stdin は
謳っていないので、文書化された範囲では失うものは無い。

副次的に、現状は後始末の `rm -f` の rc を捨てているので、ssh が cleanup 前に切れると
`/tmp` に `macvm-exec-<uuid>.sh` が 1 つずつ残り、誰にも通知されない。

## タスク

- [ ] 1. health の観測値へ `PATH` を足す。login shell 相当へ寄せるかは別途判断する
- [ ] 2. 転送先が既存ディレクトリの場合をどう扱うか決める (問い合わせる / メッセージに足す)
- [ ] 3. State を tri-state にし、resolve-ip と screenshot の断定も揃える
- [ ] 4. 外部入力を渡す位置へ `--` を置く
- [ ] 5. `echo` を `printf '%s\n'` へ寄せる
- [ ] 6. 一時ファイルの生成を try で囲み、書き込み失敗でも残骸を出さない
- [ ] 7. `exec` の引数の扱いを決める (`shlex.join` / 散文で制約を明示)
- [ ] 8. `find_vm` の優先順位を prlctl で実測するか、未定義と明記する
- [ ] 9. push / pull のテストハーネスを 1 系統へ寄せ、包含関係にあるテストを畳む
- [ ] 10. `sh -s` 方式の採否を決める。採るなら stdin 経路の喪失を SKILL.md へ明記する
- [ ] 変更を入れたら実 VM で full chain smoke を通す (シェル層はランタイムでしか壊れない)

## 関連

ISSUE-52 は winvm 側に残る同型の欠陥を扱う。項目 4 と 7 は winvm にも同型がありうるので、
どちらかに着手するときはもう一方も見る。

ISSUE-5 は health と exec が同じプロトコルを二重実装している件を扱う。項目 10 の
`sh -s` 方式はその設計にも影響する。
