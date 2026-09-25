"""jev-lint 厳選ラッパの入口。

このモジュールは上流 (jev-lint) の版と、厳選した rule の一覧を持つ唯一の場所である。
`jevlint_tree` / `jevlint_host` / `jevlint_result` / `jevlint_compat` へはここから引数で
値を渡す。逆方向の import はしない (循環と、pin の二重管理を防ぐため)。

本 docstring は終了コードの意味の canonical で、SKILL.md はここを指すだけで数値を再掲しない。

check / review の終了コードは、上流の終了コード自体を使わず (上流は 0/1/3 以外を返すことが
ある)、以下の優先順で最初に当たったものを返す (上から順に判定し、どれにも当たらなければ 0)。

    2: 引数・事前検査・host の用意・コミットの展開のいずれかのエラー、上流が 0/1/3 以外を
       返した、stdout が JSON として読めない、必須キーが欠けている、記録が無いか読めない、
       といった「上流の出力を判定に使えない」場合すべて
    3: 答えの無い subject があるか request のエラーがある
       (finding の有無より先に判定する。答えが揃っていない結果を「finding 無し」と区別する)
    1: finding が 1 件以上ある
    0: それ以外 (対象にした subject すべてに答えがあり、finding も無い)

compat の終了コードは次のとおり (これも上から順に判定する)。

    2: 版の文字列が不正、host の用意に失敗、`rules --json` の起動または出力の解釈に失敗、
       のいずれかで検査そのものができなかった
    1: 厳選した rule が新しい版に無い、kind が変わった、生成した設定で上流が異常終了した、
       手元の node が engines を満たさない、のいずれか
    0: それ以外 (報告のみ。cutoff の変化、rule.yml の変化、増えた rule があっても失敗ではない)
"""

from __future__ import annotations

import argparse
import re
from typing import NoReturn

# 上流 (jev-lint) の pin はこの 1 箇所だけに置く。他のモジュールは呼び出し側から
# 版の値を受け取り、自分では持たない。
UPSTREAM_VERSION: str = "0.7.0"

# 厳選した rule の一覧。根拠は spec (`ISSUE-65-spec.md` の「厳選する rule」節、0.7.0 時点の
# 実測) が持つ。ここに件数のコメントは書かない — この一覧自体が唯一の真実で、件数を測るのは
# テスト側の役目にする (別の場所に数を書くと spec を直したときに drift する)。
CURATED: tuple[str, ...] = (
    "typescript/pure-name-is-pure",
    "typescript/doc-errors-match-body",
    "typescript/module-name-describes-contents",
    "typescript/tests-cover-failure-paths",
    "typescript/var-name-describes-value",
    "typescript/comment-describes-declaration",
    "typescript/test-name-describes-code",
    # rust には pure-name-is-pure と tests-cover-failure-paths が無い (前提: 上流の
    # ソースツリーに rule.yml 自体が存在しない)。
    "rust/doc-errors-match-body",
    "rust/module-name-describes-contents",
    "rust/var-name-describes-value",
    "rust/comment-describes-declaration",
    "rust/test-name-describes-code",
    "python/pure-name-is-pure",
    "python/doc-errors-match-body",
    "python/module-name-describes-contents",
    "python/tests-cover-failure-paths",
    "python/var-name-describes-value",
    "python/comment-describes-declaration",
    "python/test-name-describes-code",
    "moonbit/pure-name-is-pure",
    "moonbit/doc-errors-match-body",
    "moonbit/module-name-describes-contents",
    "moonbit/tests-cover-failure-paths",
    "moonbit/var-name-describes-value",
    "moonbit/comment-describes-declaration",
    "moonbit/test-name-describes-code",
    "javascript/comment-describes-declaration",
)


class UsageError(Exception):
    """引数の誤り。呼び出し側はこれを終了コード 2 に落とす。"""


def build_config(thresholds: dict[str, float]) -> dict:
    """上流に渡す設定を組み立てる。

    `rules` だけを持つ dict を返す。`baseUrl` / `apiKeyEnv` / `languages` / `cache` /
    `model` / `files` はどの階層にも足さない — 足さないこと自体でこれらを上流の既定 (または
    起動の argv で明示した値) に委ねる。値を上書きしたい項目だけ `thresholds` に入れる。
    """
    rules: dict[str, object] = {}
    for key in CURATED:
        if key in thresholds:
            rules[key] = {"threshold": thresholds[key]}
        else:
            rules[key] = "on"
    return {"rules": rules}


def parse_threshold(text: str) -> tuple[str, float]:
    """`<languageDir>/<id>=<値>` を分ける。厳選の外や範囲外の値は `UsageError`。"""
    key, sep, value_text = text.partition("=")
    if not sep:
        raise UsageError(f"--threshold は <lang>/<id>=<値> の形にすること: {text!r}")
    if key not in CURATED:
        raise UsageError(f"--threshold の対象は厳選した rule のみ: {key!r}")
    try:
        value = float(value_text)
    except ValueError:
        raise UsageError(f"--threshold の値が数値でない: {value_text!r}") from None
    # NaN は 0 < value < 1 のどちらの比較も False になるため、この範囲チェックだけで
    # 0 以下・1 以上・NaN のすべてを拒否できる (追加の isnan 分岐は要らない)。
    if not (0 < value < 1):
        raise UsageError(f"--threshold の値は 0 と 1 の間にすること: {value_text!r}")
    return key, value


_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def parse_version(text: str) -> str:
    """`X.Y.Z` (ASCII の数字のみ) の形だけを受け付ける。`jev-lint@<版>` に埋め込む形。

    `str.isdigit()` は上付き数字 (`²`) や全角数字 (`０`) にも真を返す (実測)。この
    値はそのまま `jev-lint@<版>` というパッケージ指定に埋め込むため、`[0-9]` に絞った
    正規表現の `fullmatch` を使う (`$` は行末の改行にもマッチするため使わない)。
    """
    if _VERSION_RE.fullmatch(text) is None:
        raise UsageError(f"版は X.Y.Z の数字の形にすること: {text!r}")
    return text


class _StrictArgumentParser(argparse.ArgumentParser):
    """`error()` を `sys.exit` ではなく `UsageError` にする。

    argparse は既定で不正な引数を `SystemExit(2)` にするが、呼び出し側 (main) が
    終了コードを自分で決められるよう例外に変える。`add_subparsers` に渡す
    `parser_class` にも同じクラスを使うので、サブコマンド側のエラー
    (`review` の `--base` 必須違反など) も同じ経路で `UsageError` になる。
    """

    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def _add_common_options(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--commit")
    sub.add_argument("--dry-run", action="store_true")
    sub.add_argument("--exclude", action="append", default=[])
    sub.add_argument("--threshold", action="append", default=[])
    sub.add_argument("--json-out")


def _build_parser() -> argparse.ArgumentParser:
    parser = _StrictArgumentParser(prog="jevlint", allow_abbrev=False)
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=_StrictArgumentParser
    )

    check = subparsers.add_parser("check", allow_abbrev=False)
    check.add_argument("paths", nargs="+")
    _add_common_options(check)

    review = subparsers.add_parser("review", allow_abbrev=False)
    review.add_argument("--base", required=True)
    review.add_argument("paths", nargs="*")
    _add_common_options(review)

    compat = subparsers.add_parser("compat", allow_abbrev=False)
    compat.add_argument("version")

    return parser


def _reject_dash_positionals(args: argparse.Namespace) -> None:
    # argparse は `--` の後ろに来た値をオプションとして解釈しないため、`-x` のような
    # 値がそのまま位置引数に入ってしまう。上流や git への引き渡しでオプションと
    # 誤認されないよう、ここで明示的に拒否する (`--` の有無を問わない一律の検査)。
    for name in ("paths", "version"):
        value = getattr(args, name, None)
        if value is None:
            continue
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if candidate.startswith("-"):
                raise UsageError(f"'-' で始まる位置引数は受け付けない: {candidate!r}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    """コマンドライン引数を検査する。誤りはすべて `UsageError` にする。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _reject_dash_positionals(args)
    return args
