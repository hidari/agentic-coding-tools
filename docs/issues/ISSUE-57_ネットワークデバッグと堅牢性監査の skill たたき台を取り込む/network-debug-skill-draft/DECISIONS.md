# 構成の決定と根拠

## 決定

- 内容の割り方: 目的別 (レイヤー選択の判断を skill 本文が持つ)
- 割り方を運ぶ仕組み: reference file。component skill は使わない
- 配布の形: `skills/tooling/` の単体 skill。plugin 化は後から追加

## 内容の割り方: 目的別

コマンド別 (`tcpdump` / `tshark` / `mitmproxy` を別 skill) が成立しない理由。

skill 選択時にエージェントのコンテキストに載るのは `name` と `description` だけで、
本文は選択後に読み込まれる。コマンド別に割ると「どのツールを使うべきか」を
決めた後でしか skill を選べないが、ツール選定こそ知識が要る部分である。
つまり一番価値のある判断を skill の外へ追い出すことになる。
加えて 3 つの description が「ネットワークをキャプチャする」と似通うため、
選択が曖昧になり、最悪 3 つ全部読み込んでコンテキストを無駄に食う。

目的別なら症状から入れる。「HTTP を見たい」「パケットを見たい」の分岐判断自体を
skill の中身にできる。

目的別をさらに「HTTP デバッグ」「パケットデバッグ」へ割るのも避ける。実際のデバッグは
mitmdump で見たが TLS の手前で切れている → パケットレイヤへ降りる、のように
レイヤーを往復するため、境界を切ると skill の外で迷子になる。

## chrome-devtools-debugger との境界

除外の基準は「ブラウザかどうか」ではなく「**CDP で繋げるかどうか**」とする。

`chrome-devtools-debugger` は chrome-devtools-mcp 経由なので、CDP で接続した
Chrome しか見られない。ここを「ブラウザは対象外」と書くと、Safari / Firefox /
Arc / Electron アプリが**どちらの skill の担当でもない**状態になる。
network-debug が自分から除外しているのに、chrome-devtools-debugger は
技術的に見られない、という穴が空く。

Electron アプリ (Slack / Discord / VS Code) は Chromium だが CDP で繋いでいない
ため、この基準では network-debug 側に入る。「Chromium かどうか」で切っても
同じ穴が空くので、接続手段で切る。

## 対象の種別が HTTP レイヤの手段を決める

macOS の GUI アプリは `HTTPS_PROXY` を読まない (CFNetwork 経由でシステムの
ネットワーク設定を見る)。したがって「regular モードを第一候補にする」という
規則は CLI 限定でしか成立しない。GUI アプリでは `--mode local` が第一候補になる。

この分岐を `SKILL.md` のレイヤー選択の直後に置いた。reference まで降りてから
気づく形にすると、エージェントが環境変数で空振りしてから戻ることになる。
空振りはエラーではなく「トラフィック 0 件」として現れるため、
承認漏れとも証明書の不備とも区別がつかない。

local モードは初回に人間の承認が要る。**GUI アプリを対象にすると決まった時点で
依頼する**のが最短で、キャプチャを試した後ではない。

Firefox は証明書ストアが独立しているため、GUI 分岐に入れた上でさらに個別の
手当てが必要になる。システムキーチェーンに CA を入れても効かず、
症状が「証明書を入れたのに TLS エラー」になるので誤診しやすい。

## 割り方を運ぶ仕組み: reference file

component skill にすると、その `description` が skill 一覧として常時コンテキストへ
載る。`dev-workflow` のように component がそれぞれ独立した作業単位なら正しい形だが、
network-debug に同じ形を使うと「HTTP を見る」「パケットを見る」が並んで description が
互いに競合し、「内容の割り方」で退けたコマンド別と同じ選択の曖昧さが戻ってくる。

HTTP レイヤ / パケットレイヤは独立して起動する作業ではない。人は `/packet-capture` と
打たず「このリクエストが失敗する」と言う。よって component skill ではなく
`references/*.md` に置く。reference file は loader から見えず、本文が読みに行くまで
コストがゼロである。

## 配布の形: 単体 skill

`scripts/check-package-shape.py` の docstring が canonical な規約。
`plugins/` に居るのは agents / commands / hooks / MCP を持つパッケージだけ
(`dev-workflow`, `security-blue-red-team`, `web-monkey-qa`)。
network-debug はどれも持たないので `skills/tooling/` 側。隣が
`chrome-devtools-debugger` でカテゴリもドメインも隣接する。

片道ドアではない。同 docstring の規約 1 が「plugin パッケージの root に SKILL.md が
ある」を要求しているため、後から `.claude-plugin/plugin.json` を足すだけで昇格でき、
既存ファイルの移動は要らない。`snippets/plugin-promotion.md` を参照。

## PUBLIC repo 由来の書き方の制約

`agentic-coding-tools` は PUBLIC で、CLAUDE.md が絶対パス
(`/Users/<name>/...`) を禁止し gitleaks が検査する。よって開発機固有の状態
(chmodbpf を入れたか、証明書の実パス) を事実として書けない。

これは制約への妥協ではなく品質改善として扱う。状態は実行時に検出させる:
`ls -l /dev/bpf0` が読めるかで sudo の要否を判定し、証明書は
`~/.mitmproxy/` の存在確認から入る。別マシンでも正しく動く。

## frontmatter の可搬性

`allowed-tools` は Agent Skills の可搬フィールド集合
(`name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`)
に含まれるので strict な validator でも落ちない。Claude Code では invoke した
ターンだけ Bash パターンを事前承認する。

`sudo` は `allowed-tools` に入れない。キャプチャの sudo 要件は chmodbpf で
外すのが正しく、事前承認で回避すべきものではない。

## コマンドをスクリプトへ切り出さない (初版)

散文 + snippet で持ち、`.sh` / `.py` を持たない。

スクリプト化の利益は「決定的に実行される」ことだが、この skill が文書化した失敗
(local モードの承認漏れ、GUI アプリへの環境変数、`-Q` の黙殺、pktap の権限) は
どれもコマンドの書き間違いではなく環境の差異である。包んでも直らない。
中で環境を検査するなら直るので、切り出す候補は検査だけになる。

採取と解析の側は、パラメータ (インターフェース / フィルタ / プロセス名) が内容の
全部であり、かつ出力を見てから次を決める。スクリプトに包むと tcpdump という
文書化された方言の上に文書化されていない方言を足すことになり、エージェントが
出力を見てフィルタを変える動きも封じる。reference にフラグを直書きする利点は、
それを読んで未知の組み合わせを作れることである。

唯一の候補は「前提の確認」で、`agentic-coding-tools` の既存スクリプト
(`render.py` / `issue-id.py`) が満たす 3 条件 — スクリプトが canonical を持つ /
決定的である / 人間や CI も走らせる — を満たす。ただし実質 2 行なので、
ファイルを 1 つ増やすコストに見合わない。

切り出す条件、コスト、preflight のたたき台は `snippets/script-extraction.md` に置いた。
