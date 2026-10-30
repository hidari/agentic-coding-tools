---
status: open
---

# feat: windows-vm-verification にネットワークを切る区間の扱いが無い

## 背景

出所は dotfiles の ISSUE-132 (受け箱に届いた skill の改善提案) の windows-vm-verification の「ネットワークを切る区間」節。どれも別のリポジトリのセッションの申告で、本リポジトリでも dotfiles でも再現していない。

`winvm` は ssh を土台に動き、`references/ssh-config.template` は多重化 (ControlMaster) を前提にしている。ゲストのネットワークを一時的に切る検証については、skill も CLI も何も言っていない。

## 報告

- 報告 (実測): 検証 VM でアダプタを無効にする操作を 1 本のスクリプトにまとめて finally で必ず戻し、別に SYSTEM の一回きりのスケジュールタスクで、15 分後に全アダプタを有効に戻す見張りを置いた
- 報告 (実測): ゲストのアダプタを切っている間にホストから ssh を打つと、ControlMaster の多重化の接続が壊れたまま残り、戻した後の ssh (それを使うラッパも) が 10 分固まった。新しい ssh では Broken pipe が出て張り直された。回避は、区間の前後で `ssh -O exit <host>` するか、その区間だけ `-o ControlMaster=no` にする (推測)。報告者の提案: CLI (winvm) にこの区間を扱う入口を足す
- 報告 (実測): ネットワークを切っている間は ssh で同期できないので、`prlctl exec` で base64 を書き込み、ハッシュを照合してから使った

## タスク

- [ ] winvm にネットワークを切る区間を扱う入口を足すか、手順として reference に書くだけにするかを決める
- [ ] 入口を足すなら、区間の前後で多重化の接続を閉じることと、戻す見張りを置くことを含める。ssh / prlctl の full chain を VM で一度通してから完了とする
- [ ] 多重化の接続が壊れたまま残る挙動と、`ssh -O exit` で避けられるか (推測の部分) を VM で確かめる

## 関連

- ISSUE-7: リモート往復数を減らす。ControlMaster の多重化を前提にした往復数の実測を持つ
- ISSUE-100: windows-vm-verification にディスクの容量と UIA の操作の知見が無い。同じ dotfiles の ISSUE-132 から同時に渡された
