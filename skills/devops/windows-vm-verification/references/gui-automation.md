# Windows VM 検証 — GUI の自動操作の落とし穴

UI Automation (UIA) で VM の GUI を操作したときに踏んだもの。どれもエラーにならず、操作が効いたように見えるか、黙って止まる。

## 日付入力は年・月・日の別々の Spinner になる

Chromium 系のブラウザの `<input type="date">` は、UIA では年・月・日が別々の `Spinner` 要素として見える。入力欄の Edit にフォーカスしても、文字列を Unicode として注入しても値は入らない。

- 各 Spinner に `SetFocus` してから、仮想キーで数字を打つ
- ValuePattern は古い値を返し続けるので、入ったかどうかの判定に使わない。スクリーンショット (`winvm screenshot`) か、保存された結果で確かめる

## 画面遷移の直後に引いた要素が null になる

遷移の直後に 1 回だけ引いた要素 (戻るリンクなど) が null になり、操作のスクリプトが途中で止まった。

- 遷移のあとはウィンドウをフォアグラウンドへ出してから要素を引き直す。回数の上限を決めて再試行する
- 1 回の null を「要素が無い」と読まない

## インストーラがサービスの開始の種類を戻し、スナップショットへ焼き込まれる

MSI などのインストーラを実行した直後に、無効にしておいたサービス (観測したのは Windows Update の `wuauserv`) の開始の種類が「手動」へ戻された。System ログにイベント ID 7040 (サービスの開始の種類の変更) が残る。インストーラの実行後に取ったスナップショットはこの状態を持つので、そこへ巻き戻すたびに止め直すことになる。

- 原状保全のスナップショットはインストーラを実行する前に取る (SKILL.md の「検証 VM の扱い」)
- インストーラの実行後と、スナップショットへ巻き戻したあとに、開始の種類を確かめる (`winvm exec --host <alias> -- "Get-Service wuauserv | Select-Object Name, StartType, Status"`)
- 変更の記録は `Get-WinEvent -FilterHashtable @{LogName='System'; Id=7040} -MaxEvents 5` で引ける
