---
name: markdown-to-pdf
description: Use when Markdown ファイルを整形して PDF 化したいとき。日本語ビジネス文書・技術ドキュメント・契約書ドラフト・計画書などを uv 経由のスタンドアロン Python スクリプト (render.py) で PDF に変換する。表組み・シンタックスハイライト・ヘッダー/フッター・ページ番号を含む整形済み PDF が必要なケース全般で使用する。
---

# Markdown → PDF 変換

## いつ使うか
- 日本語ビジネス文書や技術ドキュメントを PDF で配布・アーカイブしたい
- GFM 相当の表・打ち消し・タスクリスト、コードブロックのハイライト、ページ番号・ヘッダー/フッターを含めたい
- pandoc + LaTeX を避けて純 Python スタック（markdown-it-py + weasyprint + Pygments）で完結させたい

## 使わないほうがよいケース
- 入稿品質のレイアウト（PDF/X-1a 等）が必要 → pandoc + xelatex 系を検討
- 数式（LaTeX）が大量に含まれる → MathJax レンダリング等を別途検討
- Mermaid 図のレンダリングが必要 → 事前に画像化しておく

## 前提環境（macOS）

weasyprint はネイティブライブラリ（pango / cairo / glib / harfbuzz 等）を dlopen で読み込むため、Homebrew での事前インストールが必要。

```
brew install pango
```

pango が入れば cairo / harfbuzz / glib などは依存として連鎖的に入る。`render.py` は macOS で `DYLD_FALLBACK_LIBRARY_PATH` を自動設定してから自己再実行するので、環境変数を手動で export する必要はない。Linux の場合は `libpangoft2-1.0-0` `libharfbuzz0b` `libpango-1.0-0` `libgobject-2.0-0` を apt/yum 等で入れる。

## コマンド

```
uv run ${CLAUDE_SKILL_DIR}/scripts/render.py <input.md> [OPTIONS]
```

`${CLAUDE_SKILL_DIR}` はこのファイルが読み込まれる時点で実際の skill ディレクトリの絶対パスへ置換される。配置先を固定しないため、書き換えずそのまま使うこと。

### オプション一覧

| オプション                                | 省略時                          | 説明                                              |
|--------------------------------------|------------------------------|-------------------------------------------------|
| `<input.md>`（位置引数、必須）                | —                            | 変換対象の Markdown ファイル。絶対パス / 相対パスどちらも可            |
| `-o <out.pdf>`, `--output <out.pdf>` | 入力と同じディレクトリに `<入力ファイル名>.pdf` | 出力 PDF パス                                       |
| `--title <タイトル>`                     | 入力ファイル名（拡張子抜き）               | ヘッダー右上に表示・PDF タイトルに使う文字列                        |
| `--author <著者>`                      | 空                            | PDF メタ情報の著者名                                    |
| `--date YYYY-MM-DD`                  | 実行日                          | フッター左下に表示する日付                                   |
| `--css <css_path>`                   | 同梱の `style.css`              | カスタム CSS パス（`{{title}}` `{{date}}` のテンプレ変数を使える） |
| `--allow-html`                       | 指定なし（生 HTML は文字列としてエスケープ表示）    | 入力 Markdown 内の生 HTML をそのまま解釈する。信頼できる入力にのみ指定すること（`weasyprint` は `<img>` や `<link rel="stylesheet">` を描画時に実際に取得するため、既定を開けると外部への送信経路になる） |

パス解決: 入力ファイルのパスは呼び出し時の cwd に依存せず、絶対/相対どちらで渡しても正しく解決される。画像や相対リンクの `base_url` は**入力 MD の親ディレクトリ**が基準になる。

上書き挙動: 出力先に同名 PDF が既にある場合は**確認なしに上書き**する。失敗したくない場合は呼び出し側で事前にリネーム・バックアップするか、`-o` で別パスを指定すること。

## 最小例

```
uv run ${CLAUDE_SKILL_DIR}/scripts/render.py docs/コスト試算.md
```

## できること一覧

| 機能          | 対応  | 備考                                      |
|-------------|-----|-----------------------------------------|
| 見出し H1〜H6   | 対応  | H1/H2 は下線、page-break-after: avoid       |
| GFM 表       | 対応  | border-collapse + ゼブラ、`thead` がページ跨ぎで再掲 |
| タスクリスト      | 対応  | mdit-py-plugins（`- [x]` / `- [ ]`）      |
| 打ち消し        | 対応  | `~~text~~`                              |
| 脚注          | 対応  | `[^1]` 記法、末尾に `section.footnotes`       |
| コード（インライン）  | 対応  | `` `code` ``                            |
| コードブロック     | 対応  | Pygments でハイライト（言語タグ必須、未知言語はプレーン表示）     |
| リンク自動化      | 対応  | linkify で生 URL 自動リンク                    |
| 画像・相対リンク    | 対応  | 入力 MD の親ディレクトリを `base_url` に設定          |
| 生 HTML       | 既定非対応 | 既定は文字列としてエスケープ表示。`--allow-html` 指定時のみ解釈（信頼できる入力限定） |
| ページ番号       | 対応  | `counter(page) / counter(pages)`（中央下）   |
| ヘッダー右上      | 対応  | タイトル（`--title` または入力ファイル名）              |
| フッター左下      | 対応  | 日付（`--date` または実行日）                     |
| 目次 TOC 自動生成 | 非対応 | スコープ外                                   |
| 数式（LaTeX）   | 非対応 | MathJax 未統合                             |
| Mermaid 図   | 非対応 | 事前に画像化すること                              |

## よくある落とし穴

- 日本語が豆腐化する → macOS は Hiragino Sans 標準搭載で問題なし。Linux で動かす場合は Noto Sans CJK JP 等の CJK フォントを事前に入れる
- `OSError: cannot load library 'libgobject-2.0-0'` → macOS で Homebrew の pango が未導入。`brew install pango` で解決
- 画像が表示されない → `--input` で渡した MD のあるディレクトリが `base_url`。画像は MD から見た相対パスか絶対パスで置く
- コードブロックに色が付かない → ` ```python ` のように言語タグを付ける。タグ無しの ``` ``` ``` だけだとプレーンの `<pre>` になる
- タイトルに `"` や `\` を含めると CSS が壊れる → `render.py` 側で CSS 文字列エスケープ済みなので、呼び出し時はそのまま渡して良い
- `uv run render.py` で依存解決が毎回遅く感じる → 初回のみ。uv キャッシュ（`~/.cache/uv/`）に入れば 2 回目以降は瞬時
- 表セル内の `<br>` がそのまま文字列で印字される → 既定 (`html: False`) では生 HTML はエスケープされる仕様。GFM の表はセル内改行を書けないため記入欄の高さを `<br>` で作りたい場合は `--allow-html` を指定する（信頼できる入力のみ）

## 検証済み環境

| 対象               | バージョン                                                        |
|------------------|--------------------------------------------------------------|
| uv               | 0.9.28                                                       |
| Python           | 3.12.x（`requires-python == 3.12.*` で uv が自動解決。初回検証時 3.12.12） |
| markdown-it-py   | 4.0.0                                                        |
| mdit-py-plugins  | 0.5.0                                                        |
| linkify-it-py    | 2.1.0                                                        |
| pygments         | 2.20.0                                                       |
| weasyprint       | 68.1                                                         |
| pango (Homebrew) | 1.57.1                                                       |
| cairo (Homebrew) | 1.18.4                                                       |

## 依存更新の流儀

- `render.py` の PEP 723 `# /// script` ブロック内のライブラリは全て `==X.Y.Z` でパッチ版まで固定
- Python は `requires-python == 3.12.*` でマイナー版固定（パッチ版はuv 自動解決）
- uv 本体のバージョンを上げる場合は `uv self update <ver>` で明示指定し、本ファイルの「検証済み環境」表を合わせて更新
- ライブラリ更新は 3 種の MD（軽量・大型・表多め）で実走確認してから `==` を差し替える。半端な部分更新はしない

## スタイル (style.css)

既定テーマは `scripts/style.css`（日本語フォント / `@page` / 表ゼブラ）。`style.css` 内の `{{title}}` と `{{date}}` はテンプレ変数で、`render.py` が読み込み時に差し替える。カスタム CSS を `--css` で渡す場合も同じ変数を使える。使えるテンプレ変数はこの 2 つのみで、`{{author}}` など他の変数は未対応（`--author` は PDF メタ情報にのみ反映される）。

### カスタム CSS の作り方

`--css <path>` で渡した CSS は既定 `style.css` を**完全置換**する（マージはされない）。既定テーマをベースに差分カスタマイズしたい場合は、同梱の `scripts/style.css` をコピーしてから編集する：

```
cp ${CLAUDE_SKILL_DIR}/scripts/style.css ~/my-theme.css
# ~/my-theme.css を編集
uv run ${CLAUDE_SKILL_DIR}/scripts/render.py input.md --css ~/my-theme.css
```

ヘッダー・フッター・ページ番号は weasyprint の CSS Paged Media (`@page` 内の margin box) で実装されている。既定 `style.css` では次のセレクタを使っている：

| 表示位置   | セレクタ                               | 既定の `content`                        |
|--------|------------------------------------|--------------------------------------|
| ヘッダー右上 | `@page { @top-right { ... } }`     | `"{{title}}"`                        |
| フッター左下 | `@page { @bottom-left { ... } }`   | `"{{date}}"`                         |
| フッター中央 | `@page { @bottom-center { ... } }` | `counter(page) " / " counter(pages)` |

既定ではこの 3 箇所のみ使っているが、weasyprint は CSS Paged Media 仕様に沿って他の margin box（`@top-left` / `@top-center` / `@bottom-right` / `@left-top` 等）もサポートしているので、カスタム CSS で自由に追加できる。カスタム CSS で `content` に固定文字や `{{title}}` を並べれば、表示文字列をドキュメントごとに調整できる（例: `content: "{{title}} — 社外秘";`）。
