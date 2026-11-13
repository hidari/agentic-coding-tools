---
status: open
---

# docs: monkey-explorer-agent の手順が検知と探索の判断を決めきっていない

## 背景

ISSUE-109 の直しの live smoke (2026-11-13、agent-browser 0.39.1、ローカルの 2 origin に 2 section を並列) で、explorer 役の agent 2 体が手順書 `plugins/web-monkey-qa/agents/monkey-explorer-agent.md` のとおりに動けなかった箇所、または判断に迷った箇所を報告した。資格情報まわりと、ISSUE-109 の変更で生まれた箇所 (認証の `open` の後のバッファ) は ISSUE-109 で直した。broken-image の signal の定義と、スクリーンショットのパスの書き方の食い違いもそこで直した。ここに残すのは、手順書がもともと決めていなかった判断で、2 体の選び方が分かれたものを含む。

- 同じページで続けて操作するとき、バッファをいつ空にするか。手順は「ページを離れる前に」だけを書く。1 体は操作ごとに空にし、もう 1 体はページが変わるときだけ空にした。後者では、ページを読み込んだときのリクエストが操作のたびに数え直される
- クリックで別の URL へ移ったとき、その操作の検知の結果をどちらのページに付けるか
- 別 origin の資源の扱い。別 origin のリンクは踏まないと決めているが、別ホストの画像が壊れているときに broken-image に数えるかは決めていない。HTTP の検知は origin で絞っている
- dead-end の「DOM が変わらなかった」の比べ方。1 体は `outerHTML` の長さと `innerText`、もう 1 体は URL と `outerHTML` のハッシュで比べた。比べ方で判定が変わる
- render-error と dead-end の `eval` に、README の `## Tooling` の確定形が無い。手順書は「確定形を literal に使う」と書くので、指す先が無い
- 探索の終わり方。frontier が尽きて予算が残ったとき、`entry_urls` から選び直すか。`target="_blank"` のリンクを同じタブの `open` に替えたとき、それを操作の予算とページの予算のどちらで数えるか
- zsh で `S="agent-browser --session ..."` のように変数へコマンドを入れて回すと、単語分割されずに rc 127 になる (smoke の 1 体が踏んだ)。手順書は Bash ツールが zsh で動きうることを `${!VAR}` の項でしか書いていない

## タスク

- [ ] 上の判断をそれぞれ決め、手順書に書く。2 体の選び方が分かれたもの (バッファの空け方、dead-end の比べ方) を先に決める
- [ ] render-error と dead-end の `eval` を実物で確かめ、README の `## Tooling` の確定形に足す
- [ ] 決めたあとで、2 section 並列の smoke をもう一度回して、2 体の選び方が揃うことを確かめる

## 関連

- ISSUE-109: この smoke を回した直し。資格情報まわりはここで済ませた
- ISSUE-110: 同じ smoke の周りで切り出した、ブラウザの作り直しの件
