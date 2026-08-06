#!/usr/bin/env -S uv run --script
# /// script
# requires-python = "==3.12.*"
# dependencies = [
#     "markdown-it-py==4.0.0",
#     "mdit-py-plugins==0.5.0",
#     "linkify-it-py==2.1.0",
#     "pygments==2.20.0",
#     "weasyprint==68.1",
# ]
# ///
"""Markdown ファイルを整形して PDF に変換するスタンドアロンスクリプト。"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import sys
from pathlib import Path


def _ensure_macos_dyld_path() -> None:
    # macOS + Homebrew 環境で weasyprint が pango/cairo 等を dlopen できるように
    # DYLD_FALLBACK_LIBRARY_PATH を注入して自己再実行する（dyld は起動時のみ環境変数を読むため）
    if sys.platform != "darwin":
        return
    lib_dir = next(
        (p for p in ("/opt/homebrew/lib", "/usr/local/lib") if os.path.isdir(p)),
        None,
    )
    if lib_dir is None:
        return
    current = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if lib_dir in current.split(":"):
        return
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
        f"{lib_dir}:{current}" if current else lib_dir
    )
    os.execv(sys.executable, [sys.executable, *sys.argv])


_ensure_macos_dyld_path()

from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound
from weasyprint import HTML

PYGMENTS_STYLE = "monokai"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Markdown を PDF に変換する（日本語・表・シンタックスハイライト対応）",
    )
    p.add_argument("input", type=Path, help="入力 Markdown ファイル")
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="出力 PDF パス（省略時は入力ファイルの拡張子を .pdf に置換）",
    )
    p.add_argument("--title", default=None, help="PDF タイトル・ヘッダー右上文字列")
    p.add_argument("--author", default=None, help="PDF メタ情報の著者名")
    p.add_argument(
        "--date",
        default=None,
        help="フッター左に表示する日付（YYYY-MM-DD、省略時は今日）",
    )
    p.add_argument(
        "--css",
        type=Path,
        default=None,
        help="カスタム CSS のパス（省略時は同梱の style.css を使用）",
    )
    return p


def highlight_code(code: str, lang: str) -> str:
    try:
        lexer = get_lexer_by_name(lang, stripall=False)
    except ClassNotFound:
        # 未知言語はプレーンな pre として出力
        return f'<pre class="codeblock"><code>{html.escape(code)}</code></pre>'
    formatter = HtmlFormatter(nowrap=False, cssclass="highlight", style=PYGMENTS_STYLE)
    return highlight(code, lexer, formatter)


def make_markdown_parser() -> MarkdownIt:
    md = (
        MarkdownIt(
            "gfm-like",
            {"linkify": True, "typographer": False, "html": False, "breaks": False},
        )
        .use(tasklists_plugin, enabled=True)
        .use(footnote_plugin)
    )

    def render_fence(tokens, idx, options, env):
        token = tokens[idx]
        info = token.info.strip() if token.info else ""
        lang = info.split()[0] if info else ""
        return highlight_code(token.content, lang)

    md.renderer.rules["fence"] = render_fence

    # 脚注 caption からサブカウンタ表示 [N:M] を取り除き、常に [N] のみとする
    def render_footnote_caption(self, tokens, idx, options, env):
        return "[" + str(tokens[idx].meta["id"] + 1) + "]"

    # 脚注末尾のバックリンクを Hiragino にあるグリフ U+2191 に置き換える
    def render_footnote_anchor(self, tokens, idx, options, env):
        ident = self.rules["footnote_anchor_name"](tokens, idx, options, env)
        if tokens[idx].meta.get("subId", 0) > 0:
            ident += ":" + str(tokens[idx].meta["subId"])
        return f' <a href="#fnref{ident}" class="footnote-backref">↑</a>'

    md.add_render_rule("footnote_caption", render_footnote_caption)
    md.add_render_rule("footnote_anchor", render_footnote_anchor)

    return md


def render_md_to_html(md_text: str) -> str:
    return make_markdown_parser().render(md_text)


def css_string_escape(s: str) -> str:
    # CSS 文字列リテラル用のエスケープ（"\" と '"' をバックスラッシュで逃がす）
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_css(css_template: str, title: str, date: str) -> str:
    replaced = css_template.replace(
        "{{title}}", css_string_escape(title)
    ).replace("{{date}}", css_string_escape(date))
    pygments_css = HtmlFormatter(cssclass="highlight", style=PYGMENTS_STYLE).get_style_defs(
        ".highlight"
    )
    return f"{replaced}\n\n/* Pygments */\n{pygments_css}\n"


def build_html_document(
    body_html: str, css: str, title: str, author: str
) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<meta name="author" content="{html.escape(author)}">
<style>{css}</style>
</head>
<body>
<main class="doc">
{body_html}
</main>
</body>
</html>
"""


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    input_path = args.input.expanduser().resolve()
    if not input_path.is_file():
        sys.exit(f"入力ファイルが見つかりません: {input_path}")

    output_path = (
        args.output.expanduser().resolve()
        if args.output is not None
        else input_path.with_suffix(".pdf")
    )

    css_path = (
        args.css.expanduser().resolve()
        if args.css is not None
        else Path(__file__).parent / "style.css"
    )
    if not css_path.is_file():
        sys.exit(f"CSS ファイルが見つかりません: {css_path}")

    return input_path, output_path, css_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path, output_path, css_path = resolve_paths(args)

    title = args.title or input_path.stem
    author = args.author or ""
    date = args.date or dt.date.today().isoformat()

    md_text = input_path.read_text(encoding="utf-8")
    body_html = render_md_to_html(md_text)

    css_template = css_path.read_text(encoding="utf-8")
    css = build_css(css_template, title=title, date=date)

    html_doc = build_html_document(body_html, css, title=title, author=author)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = HTML(string=html_doc, base_url=str(input_path.parent)).render()
    pages = len(document.pages)
    document.write_pdf(str(output_path))

    size = output_path.stat().st_size
    print(
        f"[markdown-to-pdf] {input_path.name} -> {output_path} ({pages} pages, {size:,} bytes)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
