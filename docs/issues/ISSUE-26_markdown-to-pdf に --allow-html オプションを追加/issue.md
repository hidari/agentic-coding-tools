---
status: open
---

# feat: markdown-to-pdf に --allow-html オプションを追加

## 背景

`skills/tooling/markdown-to-pdf/scripts/render.py` の `make_markdown_parser()` は
markdown-it-py を `{"linkify": True, "typographer": False, "html": False, "breaks": False}`
で構成している。`html: False` のため、入力 Markdown 内の生 HTML はエスケープされて
文字列としてそのまま PDF に印字される。

実測で確認した具体的な症状: GFM の表はセル内に複数行を書けないため、記入用紙の記入欄の
高さを `| <br><br><br> |` のような生 HTML で作っている文書がある。これを PDF 化すると、
記入欄が `<br><br><br>` という文字列になり配布物として使えない。単独行の `<br>` も同様に
文字列として出る。

無条件に `html: True` へ変える案は採らない。この skill は PUBLIC リポジトリから apm で
配布され、信頼できない Markdown を PDF 化する場面でも使われる。weasyprint は JavaScript を
実行しないが `<img>` や `<link rel="stylesheet">` は描画時に実際に外部を取得するため、
既定を開けると外部への送信経路になる。既定は閉じたままにし、入力を信頼できる呼び出し側だけが
明示的に開ける形にする。

## タスク

- [x] `build_parser()` に `--allow-html`（`action="store_true"`、既定 False、日本語 help に
      信頼できる入力に限る旨を明記）を追加する
- [x] `make_markdown_parser()` が `allow_html: bool = False` を受け取り、markdown-it-py の
      `"html"` オプションへ渡す（既定引数 False で既存呼び出しを壊さない）
- [x] `render_md_to_html()` / `main()` に `allow_html` を配線する
- [x] `SKILL.md` のオプション一覧表に `--allow-html` を追記する
- [x] `--allow-html` 無し / 有りの両方で実際に PDF を生成し、`pdftotext` で `<br>` の扱いが
      変わる（無し: 文字列として印字される / 有り: HTML として解釈され消える）ことを確認する

## 関連

`skills/tooling/markdown-to-pdf/scripts/render.py`
`skills/tooling/markdown-to-pdf/SKILL.md`
