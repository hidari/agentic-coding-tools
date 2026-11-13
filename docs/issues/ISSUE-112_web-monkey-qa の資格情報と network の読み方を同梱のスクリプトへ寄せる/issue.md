---
status: open
---

# refactor: web-monkey-qa の資格情報と network の読み方を同梱のスクリプトへ寄せる

## 背景

ISSUE-109 のマージ前のレビュー (Altitude の観点) から出た提案。ISSUE-109 では、資格情報を対象の origin にだけ付けることと、network の出力を 1 つの射影でだけ読むことを、explorer の手順書の散文で決めた。手順書に載ったコマンドの形は `scripts/package-tests/test_web_monkey_qa_commands.py` が pin している。

この形が守るのは手順書に書かれたコマンドの文字列で、agent が実行時に組み立てるコマンドは見ていない。手順書を読み違えた agent に対しては、散文の注意書き (出力を捨てる、`echo "$H"` を打たない、zsh の展開の差、`tr -d '\n'`、1 回の Bash 呼び出しで打つ) だけが防御になる。ISSUE-109 の live smoke では、2 section を並列にして別 origin への送信は 0 件、explorer の出力に資格情報は 0 件だった。今の形で足りないことは観測していない。

提案された形: plugin に小さなシェルスクリプトを 2〜3 本同梱し、explorer にはそれだけを呼ばせる。

- 環境変数の名前を 2 つ受け取り、ヘッダを組み立てて scope 付きの `open` を打ち、rc だけを返す
- セッション名を受け取り、network の射影を返す
- 必要なら検知器の一式。explorer が検知のスクリプトをその場で書かなくなれば、scratch を分ける理由 (ISSUE-109 の (3)) も見直せる

得られるもの: 上の注意書きがスクリプトの中へ移り、agent が守る規則ではなくなる。テストは手順書を正規表現で走査する形から、偽の `agent-browser` を PATH に置いてスクリプトを実行し出力を見る形へ替えられる。今のテストがフェンスの判定を `scripts/check-issue-closure.py` の `classify_lines` (Issue のクローズ検査の内部の関数) から借りている結合も要らなくなる。ISSUE-111 の zsh の単語分割も同じ層で起きなくなる。

コスト:

- `plugin.json` の description が書く「Markdown と schema だけで、自動で走るコードを持たない」という立場が変わる。自動実行ではなく明示的に呼ぶものだが、記述は直す
- agent の本文で `${CLAUDE_PLUGIN_ROOT}` が展開されるかは確かめていない (dispatcher の command では使っている)。展開されないなら dispatcher が絶対パスを入力として渡し、agent を直接 dispatch する経路には fallback が要る
- 強制にはならない。agent は Bash を持つので `agent-browser` を直接呼べる。「`set credentials` を使わない」「network は射影でだけ読む」の 1 行ずつは手順書に残る。強制するなら PreToolUse の hook だが、plugin が hook を持たない立場とぶつかる
- 偽の `agent-browser` を使うテストの基盤を新しく作る
- 今の形でもスクリプトにしても、`--headers` に渡す値は `agent-browser` の argv に載るので、同じマシンの `ps` からは見える。ローカルに限った露出で、スクリプトにしても消えない

## タスク

- [ ] agent の本文で `${CLAUDE_PLUGIN_ROOT}` が展開されるかを、apm で配布した先で確かめる
- [ ] 採るかを決める。採るなら、同梱するスクリプトの範囲 (資格情報と network だけか、検知器まで含めるか) を決める
- [ ] 採るなら、偽の `agent-browser` を使うテストで、資格情報がスクリプトの出力にも argv 以外にも出ないことを pin し、手順書の側の pin をそれに置き換える

## 関連

- ISSUE-109: この提案が出たレビューの対象。今の形の手当てと実測はここにある
- ISSUE-111: 手順書が決めていない判断。zsh の単語分割の件はこの提案で消える
- ISSUE-27: `${CLAUDE_PLUGIN_ROOT}` が解決する経路を整理した
