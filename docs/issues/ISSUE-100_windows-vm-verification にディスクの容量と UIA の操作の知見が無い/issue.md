---
status: open
---

# docs: windows-vm-verification にディスクの容量と UIA の操作の知見が無い

## 背景

出所は dotfiles の ISSUE-132 (受け箱に届いた skill の改善提案) の windows-vm-verification の「ディスクの容量」節と「GUI 自動化 (UIA)」節。どれも別のリポジトリのセッションの申告で、本リポジトリでも dotfiles でも再現していない。

`references/gui-automation.md` に既にある 2 件 (日付入力が年・月・日の別々の Spinner になること、インストーラの後に wuauserv の開始の種類が手動へ戻ること) は除いた。同じ報告のうちその 2 件にまたがる項目は、既にある部分を除いて残した。

## 報告: ディスクの容量

- 報告 (実測): ゲストでディスクへ書いているプロセスは、`Win32_PerfFormattedData_PerfProc_Process` を `IOWriteBytesPerSec` で並べると 1 回の exec で特定でき、ホスト側の差分ファイルの増加と突き合わせて原因を Windows Update と確定した
- 報告 (実測): スナップショット直後の起動で、無効にしたはずの Windows Update が動き、ホスト側の差分ファイルが 27 分で約 24GB 増えた。同じセッションで 2 回観測した。スナップショットの下ではゲストの上書きもホストでは追記になる。報告者の提案のうち gui-automation.md に無い部分: 起動前にホストの空きを確かめる。開始の種類を確かめる対象に wuauserv だけでなく UsoSvc も入れる
- 報告 (実測): VM の縮小は順序が効く。ゲストで不要物の削除と `Dism /StartComponentCleanup /ResetBase` → `Optimize-Volume -ReTrim` → 停止 → スナップショットの削除 (差分の統合) → `prl_disk_tool compact` の順で、統合だけで 87GB から 69GB、compact で 69GB から 53GB になった。`compact -i` で事前に見積もれ、compact はスナップショットが 0 件のときだけ効く
- 報告 (実測): ゲストの中のディレクトリのサイズを測るとき、Parallels の共有フォルダとジャンクションを除かないと、ホストのファイルや二重計上で数十 GB 多く見える

## 報告: GUI 自動化 (UIA)

- 報告 (実測): パスワード欄への入力は、「空のまま一度送信して必須エラーを出す → 貼る → エラーが消える」を対照にすると、欄の値が読めなくても入ったことを確かめられる
- 報告 (実測): UIA の `IsOffscreen=False` は、作業領域の中で見えていることを意味しない。表示倍率 125% の小さい画面で、画面下部の要素がタスクバーの裏に入っていたが offscreen は False だった。要素の BoundingRectangle と `Screen.WorkingArea` を比べ、スクリーンショットとの食い違いで確定した
- 報告 (実測): UIA の `WindowPattern.SetWindowVisualState(Maximized)` はそのウィンドウでは効かず、Win32 の `ShowWindow(SW_MAXIMIZE)` では効いた
- 報告 (実測): ログオン中のユーザーのセッションへ cmd 経由で payload を起動するとコンソールの窓が前面に出ることがあり、直後の仮想キーがアプリではなくコンソールに届いた。閉じる操作は UIA でボタンを直接 Invoke する方が確実
- 報告 (実測): 文言を名前の完全一致で引いたら、リンクの名前に装飾の記号まで入っていて外れた。コードの文字列は前方一致で使うか、UIA のダンプで実名を確かめてから使う
- 報告 (実測): 外部の JSON に後から追記した UI 文字列のキーが欠けたまま payload を流すと、PowerShell の `-like ("*" + $null + "*")` が任意の名前に当たり、意図しないリンクを押した。キーの欠落は payload の冒頭で検査すべきだった
- 報告 (実測): WebView2 を UIA で操作するときの罠。`FindAll(Descendants)` は時々タイムアウトを投げる、最大化は `SW_MAXIMIZE` ではなく作業領域に合わせた `MoveWindow` を使う、`IsOffscreen` はスクロールの外を意味することもある、印刷プレビューは Esc ではなく UIA のキャンセルで閉じる。報告者の提案: 上の `ShowWindow(SW_MAXIMIZE)` と `IsOffscreen` の項とは別の画面での結果なので、載せるときは並べて書く

## タスク

- [ ] ディスクの容量の 4 項目を、どの reference に載せるか (gui-automation.md、troubleshooting.md、新しいファイル) を決めて書く
- [ ] UIA の 7 項目を gui-automation.md に書く。最大化と `IsOffscreen` は 2 つの画面の結果を並べて書く
- [ ] 縮小の手順を winvm のサブコマンドにするかを決める (書くだけにするなら不要)

## 関連

- ISSUE-101: windows-vm-verification にネットワークを切る区間の扱いが無い。同じ dotfiles の ISSUE-132 から同時に渡された
