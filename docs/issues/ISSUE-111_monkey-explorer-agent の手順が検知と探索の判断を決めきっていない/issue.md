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
- URL のクエリが記録に残る。ISSUE-109 で network の射影と broken-image の値からはクエリを落としたが、console-error の text (CORS の失敗の文面は対象の URL をクエリごと含むことが多い。未実測) はそのまま signal として findings.json に書かれる。finding の `url` と action log の url も「正規化しない」と決めているのでクエリ付きのまま残る。presigned URL の署名がファイルへ残りうる
- 2 回目の smoke (ISSUE-109 の手直しの後) で足された件
  - `snapshot -i` は href と target を出さない。DENYLIST_URLS の照合、別 origin の判定、`target="_blank"` の判定に要る値を取る確定形が無く、2 体とも `eval` を自分で書いた
  - action log は `{url, action, target_name, result}` のオブジェクトだが、findings.schema.json の `repro_steps` は文字列の配列で、直列化の仕方が決まっていない
  - 「挙動で破壊的かを判断する」手段が決まっていない。1 体は `onclick` の中身を `eval` で読んで判断したが、ページ由来の未信頼のデータを判断に使ってよいかは書かれていない。もう 1 体はラベルだけで判断した
  - finding が 0 件の section は screenshots のディレクトリを作らないが、Completion handoff はそのパスを出せと書く。ディレクトリは section の間で共有なので、在ることは自分が書いたことを意味しない
  - BASE_URL は資格情報の送り先を決める値だが、agent は引数のまま使い、profile の `base_url_env` が指す値と突き合わせない。agent を直接 dispatch する経路では誤った BASE_URL がそのまま送り先になる (ISSUE-34 の allowlist と同じ材料)
- zsh で `S="agent-browser --session ..."` のように変数へコマンドを入れて回すと、単語分割されずに rc 127 になる (smoke の 1 体が踏んだ)。手順書は Bash ツールが zsh で動きうることを `${!VAR}` の項でしか書いていない

## タスク

- [ ] 上の判断をそれぞれ決め、手順書に書く。2 体の選び方が分かれたもの (バッファの空け方、dead-end の比べ方) を先に決める
- [ ] 記録に残す URL と console の text からクエリを落とすかを決め、落とすなら signal と fingerprint への影響を合わせて手順書に書く
- [ ] render-error と dead-end の `eval` を実物で確かめ、README の `## Tooling` の確定形に足す
- [ ] 決めたあとで、2 section 並列の smoke をもう一度回して、2 体の選び方が揃うことを確かめる

## 関連

- ISSUE-109: この smoke を回した直し。資格情報まわりはここで済ませた
- ISSUE-110: 同じ smoke の周りで切り出した、ブラウザの作り直しの件
