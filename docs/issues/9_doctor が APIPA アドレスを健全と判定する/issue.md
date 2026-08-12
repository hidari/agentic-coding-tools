---
status: open
---

# fix: doctor が APIPA アドレスを健全と判定する

## 背景

Windows VM で staging QA を回そうとして実際に踏んだ。VM は起動しているのにネットワークが無く、
`winvm doctor` は次を返した。

```
[ OK ] status          : running
[ OK ] IP              : 169.254.x.x
[ OK ] Parallels Tools : installed
[ OK ] host isolation  : off
[ OK ] prlctl exec     : Microsoft Windows [Version ...]
[FAIL] ssh <alias>     : 未到達
       -> sshd の稼働とファイアウォールの許可範囲を確認する
```

`169.254.0.0/16` は DHCP に失敗したときに OS が自分で振る APIPA アドレスで、
「ネットワークが無い」ことを意味する。にもかかわらず IP 行が OK なので、
唯一の FAIL である ssh の hint (sshd / ファイアウォール) に従って調べ始めてしまう。
実際にそうして sshd の稼働確認とファイアウォール規則の確認に時間を使ったが、
どちらも正常で、真因は上流の DHCP だった。

SKILL.md は doctor の設計意図を「判定だけでなく観測値を出す」「`[ -- ]` は確認できなかった、
で OK でも NG でもない。読めなかったことを健全に読み替えない」と書いている。
IP チェックはこの意図から外れており、読めた値の意味を見ずに「値が取れたか」だけで
OK を出している。

## 現状

`skills/devops/windows-vm-verification/winvm.py:310`

```python
Check(
    "IP",
    ip or UNKNOWN,
    ip is not None,
    hint="VM を起動し Parallels Tools が動いているか確認する",
),
```

`pick_ipv4` は `ipaddress.IPv4Address` で妥当性を検証済みだが、APIPA も妥当な IPv4 なので
そのまま通る。判定式の `ip is not None` は「値が取れた」以上のことを見ていない。

現在の hint (「VM を起動し Parallels Tools が動いているか確認する」) も APIPA には合わない。
IP が取れている時点で Tools は動いており、確認先として誤誘導になる。

## 実際の真因 (参考)

このとき DHCP が応答しなかった理由は、Parallels の NAT/DHCP デーモン `prl_naptd` が
**ソケットを 1 つも持たないまま生き続けていた**ため。`sudo lsof -nP -p <pid>` の
TCP/UDP 行が空で、UDP:67 を誰も listen していなかった。

`prl_naptd` には watchdog プロセス (`watchdog start 60 20 ... prl_naptd start`) が付くが、
watchdog が見るのはプロセスの生死だけでソケットの有無は見ない。そのため壊れた状態が
watchdog によって安定して維持され続ける (実測で 2 日間)。

`sudo kill <pid>` で落とすと watchdog が 60 秒以内に起動し直し、DHCP が復旧した。

診断が難しいのは他の観測点が全部「正常」を返すため:

- `prlsrvctl net info Shared` は正常。`NAT server:` 行が空なのは正常時もそうで、
  サービス停止の根拠にならない (ここを誤読した)
- ホスト側のブリッジは UP で Parallels adapter の IP を保持し、メンバーに VM の tap もある
- `ps` に `prl_naptd` が見える (生きてはいる)
- アダプタを `--device-disconnect` / `--device-connect` すると
  bridge の address cache にゲスト MAC が載る (フレームは届いている)

## タスク

- [ ] `test_winvm.py` に「IP が `169.254.x.x` のとき doctor の IP チェックが FAIL になる」テストを追加する
- [ ] 追加したテストが現行実装で赤くなることを確認する (先に赤を見てから直す)
- [ ] `winvm.py` の IP チェックを `ipaddress.IPv4Address(ip).is_link_local` で FAIL 側へ倒す
- [ ] APIPA のときだけ hint を DHCP 側の診断へ向ける (通常の未取得時の hint とは分ける)
- [ ] 変異注入で確認する: `is_link_local` の判定を外すと追加したテストが赤くなること
- [ ] `references/troubleshooting.md` に「VM が APIPA になる (DHCP 応答なし)」節を追加し、
      上記「実際の真因」の診断手順 (`sudo lsof` を対照付きで引く / `kill` で watchdog に
      再起動させる) を書く。hint からこの節へ辿れるようにする

## 関連

- 同種の欠陥: [Issue #4: VM の pwsh probe が偽陽性で exec と health が実機で動かない](../4_VM%20の%20pwsh%20probe%20が偽陽性で%20exec%20と%20health%20が実機で動かない/issue.md)。
  あちらは「probe が通るのに本実行が失敗する」、こちらは「IP が取れているのにネットワークが無い」で、
  どちらも検査が通ることを機能の証拠として扱ってしまう類型。対象のコードは別 (pwsh probe と IP チェック)。
- `sudo lsof` を引くときは対照を並べること。`sudo lsof -nP -iUDP:5353` が `mDNSResponder` を
  返すことで引き方の正しさを示さないと、UDP:67 の空を「壊れている」とも「そもそも見えていない」とも
  決められない (sudo 無しでは他ユーザのソケットが見えず必ず空になる)。
- 一時退避として bridged (`prlctl set <vm> --device-set net0 --type bridged --iface <host if>`) も
  使えるが、SSH 側の後始末が 2 つ付いてくる。(1) Windows が新ネットワークを「パブリック」に分類し
  受信 SSH を塞ぐ (2) sshd のファイアウォール規則が Parallels 共有サブネットにスコープされている。
  真因を直す方が早い。
