"""jev-lint 厳選ラッパの入口。

このモジュールは上流 (jev-lint) の版と、厳選した rule の一覧を持つ唯一の場所である。
`jevlint_tree` / `jevlint_host` / `jevlint_result` / `jevlint_compat` へはここから引数で
値を渡す。逆方向の import はしない (循環と、pin の二重管理を防ぐため)。

本 docstring は終了コードの意味の canonical で、SKILL.md はここを指すだけで数値を再掲しない。

check / review の終了コードは、上流の終了コード自体を使わず (上流は 0/1/3 以外を返すことが
ある)、以下の優先順で最初に当たったものを返す (上から順に判定し、どれにも当たらなければ 0)。
`--dry-run` のときは判定がここで止まり 0 か 2 にしかならない。dry-run の応答は
`findings` や `stats.missing` を持たない別の形の文書なので、3・1 の判定は行われない。

    2: 引数・事前検査・host の用意・コミットの展開のいずれかのエラー、上流が 0/1/3 以外を
       返した、stdout が JSON として読めない、必須キーが欠けている、記録が無いか読めない、
       といった「上流の出力を判定に使えない」場合すべて。`--dry-run` のときは `dryRun` が
       真で subject の件数を持たない場合もここに含める
    3: 答えの無い subject があるか request のエラーがある
       (finding の有無より先に判定する。答えが揃っていない結果を「finding 無し」と区別する)
    1: finding が 1 件以上ある
    0: それ以外 (対象にした subject すべてに答えがあり、finding も無い)。`--dry-run` の
       ときは `dryRun` が真で subject の件数を持てば足り、subject に答えがあることは
       意味しない (dry-run は応答を持たない)

compat の終了コードは次のとおり (これも上から順に判定する)。

    2: 版の文字列が不正、host の用意に失敗、`rules --json` の起動または出力の解釈に失敗、
       のいずれかで検査そのものができなかった
    1: 厳選した rule が新しい版に無い、kind が変わった、生成した設定で上流が異常終了した、
       手元の node が `engines` を満たさないか `engines` が `>=N` の形でない、のいずれか
    0: それ以外 (報告のみ。cutoff の変化、rule.yml の変化、増えた rule があっても失敗ではない)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, NoReturn

import jevlint_host
import jevlint_result
import jevlint_tree

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


def _committed_paths(root: Path, sha: str, texts: list, env: dict) -> list:
    """位置引数のパスを root 相対に正規化し、`sha` のコミットに無いものを拒否する。

    起動したディレクトリは解釈に使わない (サブディレクトリから起動しても `sub/a.py` は
    root の `sub/a.py`)。未追跡や未コミットのファイルもここで落ちる。
    """
    paths = []
    for text in texts:
        path = jevlint_tree.normalize_path(text)
        if not jevlint_tree.path_in_commit(root, sha, path, env):
            raise jevlint_tree.TreeError(
                f"コミット {sha} に無いパス: {text!r} (上流へ送るのはコミット済みの中身だけ)"
            )
        paths.append(path)
    return paths


def _prepare_host(environ: Mapping[str, str], run_pnpm: "Callable | None") -> Path:
    # 既定の runner は `prepare_host` 自身のもの (pnpm の stdout を stderr へ流す) に任せる。
    # `subprocess.run` をそのまま渡すと、初回の取得で pnpm の進捗が要約の stdout に混ざる
    if run_pnpm is None:
        return jevlint_host.prepare_host(UPSTREAM_VERSION, environ)
    return jevlint_host.prepare_host(UPSTREAM_VERSION, environ, run=run_pnpm)


def _checked_node(host: Path, env: dict, run_upstream: Callable, which: Callable) -> str:
    """起動に使う node を 1 度だけ解決し、host の jev-lint の `engines.node` を満たすか見る。

    PATH は `env` (`build_env` が相対と空の要素を落としたもの) から取る。利用者の生の PATH
    で解決すると、相対の要素がプロセスの cwd (消費側のリポジトリ) で解決され、そこに
    コミットされた `node` を拾う。`path=None` は `os.environ` の PATH に戻るので、PATH が
    無いときは空文字列を渡す (`shutil.which` は空の path で None を返す。実測: 3.9.6 と
    3.14.7)。`node --version` は上流と同じ runner で起動し、cwd はラッパ所有の host にする
    (消費側のリポジトリを子プロセスの cwd にする経路を増やさない)。
    """
    node = which("node", path=env.get("PATH", ""))
    if not node:
        raise jevlint_host.HostError("node が PATH の絶対パスの要素に見つからない")
    try:
        proc = run_upstream(
            [node, "--version"],
            cwd=str(host),
            env=env,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except OSError as error:
        raise jevlint_host.HostError(f"node を起動できない: {node}: {error}") from None
    if proc.returncode != 0:
        raise jevlint_host.HostError(
            f"node --version が失敗した (終了コード {proc.returncode}): {node}"
        )
    version = jevlint_host.parse_node_version(proc.stdout)
    engines = jevlint_host.read_engines(host)
    if not jevlint_host.engines_ok(engines, version):
        shown = ".".join(str(part) for part in version)
        raise jevlint_host.HostError(
            f"node {shown} は jev-lint {UPSTREAM_VERSION} の engines.node ({engines}) を満たさない"
        )
    return node


def _require_key(environ: Mapping[str, str]) -> None:
    """キーの有無だけを見る。値はどのメッセージにも入れない。"""
    status = jevlint_host.key_status(environ)
    if status == "legacy-only":
        raise jevlint_host.HostError(
            "TYPESAFEAI_API_KEY だけがある。上流へ渡すのは TYPESAFE_API_KEY だけなので、"
            "キーは TYPESAFE_API_KEY に入れること"
        )
    if status != "ok":
        raise jevlint_host.HostError(
            "TYPESAFE_API_KEY が無いか空白だけ。--dry-run 以外はキーが要る"
        )


def _json_out_target(text: str, cwd: Path, expanded: jevlint_tree.Expanded) -> Path:
    """`--json-out` の保存先。main の `cwd` からの相対で解釈し、上流を起動する前に検査する。

    上流の起動は課金されるので、書けない保存先で起動の後に失敗しないよう、親ディレクトリが
    あることと保存先がディレクトリでないこともここで確かめる (権限までは見ない)。展開の
    worktree と scratch の中は、抜けるときに消えるので
    拒否する。包含は inode で見る。大文字小文字を区別しないファイルシステムでは、文字列の
    比較は `.../Tree` と `.../tree` の包含を見落とす (`jevlint_tree._temp_base` と同じ理由)。
    """
    target = Path(text)
    if not target.is_absolute():
        target = cwd / target
    target = target.resolve()
    for ancestor in (target, *target.parents):
        for inside in (expanded.tree, expanded.scratch):
            try:
                same = os.path.samefile(ancestor, inside)
            except OSError:
                # まだ無いパス。同じ inode を指しようがない
                continue
            if same:
                raise UsageError(f"--json-out が展開した一時ディレクトリの中を指している: {text!r}")
    if not target.parent.is_dir():
        raise UsageError(f"--json-out の親ディレクトリが無い: {text!r}")
    if target.is_dir():
        raise UsageError(f"--json-out がディレクトリを指している: {text!r}")
    return target


def _read_record(record: "Path | None") -> "str | None":
    """`--record` の記録。無いか読めなければ None にし、判定 (classify) で 2 にさせる。"""
    if record is None:
        return None
    try:
        return record.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _save_json_out(target: Path, stdout: str, record_text: "str | None") -> None:
    try:
        target.write_text(stdout, encoding="utf-8")
        if record_text is not None:
            Path(f"{target}.record.json").write_text(record_text, encoding="utf-8")
    except OSError as error:
        raise UsageError(f"--json-out に書けない: {error}") from None


def _run(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    cwd: Path,
    run_pnpm: "Callable | None",
    run_upstream: Callable,
    which: Callable,
) -> int:
    if args.command not in ("check", "review"):
        raise UsageError(f"{args.command} はまだ使えない")
    thresholds = dict(parse_threshold(text) for text in args.threshold)

    # ラッパ自身の git と `node --version` の env。キーを含まない
    keyless_env = jevlint_host.build_env(environ, with_key=False)
    root = jevlint_tree.repo_root(cwd, keyless_env)
    sha = jevlint_tree.resolve_commit(root, args.commit or "HEAD", keyless_env)
    base = None
    if args.command == "review":
        base = jevlint_tree.resolve_commit(root, args.base, keyless_env)
    paths = _committed_paths(root, sha, args.paths, keyless_env)

    host = _prepare_host(environ, run_pnpm)
    node = _checked_node(host, keyless_env, run_upstream, which)
    if not args.dry_run:
        _require_key(environ)

    with jevlint_tree.signals_as_exceptions():
        with jevlint_tree.expanded_commit(root, sha, keyless_env) as expanded:
            json_out = None
            if args.json_out is not None:
                json_out = _json_out_target(args.json_out, cwd, expanded)
            config = expanded.scratch / "config.json"
            config.write_text(json.dumps(build_config(thresholds)), encoding="utf-8")
            record = None if args.dry_run else expanded.scratch / "record.json"
            argv = jevlint_host.upstream_argv(
                node,
                host,
                args.command,
                base=base,
                excludes=args.exclude,
                # `.` は normalize_path で "" になる。上流へは空の引数ではなく `.` で渡す
                paths=[path or "." for path in paths],
                dry_run=args.dry_run,
                record=record,
                config=config,
            )
            # stderr は捕まえずに流す (上流の設定のエラーを利用者が読めるように)。stdout は
            # 判定に使う JSON。node の出力は locale によらず UTF-8 なので明示して読む
            proc = run_upstream(
                argv,
                cwd=str(expanded.tree),
                env=jevlint_host.build_env(environ, with_key=not args.dry_run),
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            record_text = _read_record(record)
            outcome = jevlint_result.classify(
                proc.returncode, proc.stdout, record_text, args.dry_run
            )
            if outcome.code == 2:
                print(f"jevlint: 上流の結果を判定に使えない: {outcome.reason}", file=sys.stderr)
                return 2
            mbt_count = jevlint_tree.count_suffix(expanded.tree, paths, ".mbt")
            print(
                jevlint_result.summarize(
                    outcome,
                    sha=sha,
                    version=UPSTREAM_VERSION,
                    curated=CURATED,
                    mbt_count=mbt_count,
                    dry_run=args.dry_run,
                )
            )
            if json_out is not None:
                _save_json_out(json_out, proc.stdout, record_text)
            return outcome.code


def main(
    argv: list[str],
    *,
    environ: "Mapping[str, str] | None" = None,
    cwd: "Path | None" = None,
    run_pnpm: "Callable | None" = None,
    run_upstream: Callable = subprocess.run,
    which: Callable = shutil.which,
) -> int:
    """check / review を実行し、終了コード (意味はこのモジュールの docstring) を返す。

    段の順序は仕様である: 引数 → ref とパス (本体のリポジトリの git) → host の用意 (キー
    無し) → node と engines → キー → コミットの展開 → 上流の起動 → 判定と要約。host の用意を
    展開より先に置くのは、pnpm が走る時点で worktree (コミットされた `.npmrc` がありうる) を
    まだ存在させないため。上流へ渡す env だけが、`--dry-run` でないときにキーを持つ。

    stdout には要約だけを書く。エラーは stderr に 1 行で書き、2 を返す。判定が 2 のときは
    要約も `--json-out` の保存もしない (stdout が JSON でないこともあるため)。例外は種類を
    問わず 2 にする。捕まらない例外で Python が返す 1 は「finding あり」と衝突するため。
    KeyboardInterrupt と SIGTERM / SIGHUP (`SignalInterrupt`) は Exception の外なので、
    展開の後始末を済ませてからそのまま抜ける。

    既知の限界: `review` の上流は worktree の中で `git diff <base>...HEAD` を呼び、その git は
    利用者の git 設定で走る。コミットされた `.gitattributes` が選ぶ textconv の driver
    (利用者の設定にあるコマンド) は、キーを含む上流の env で起動しうる。上流が
    `--no-textconv` を付けない限りラッパの側では塞げない。

    `environ` と `cwd` の None は、呼び出しの時点の `os.environ` と `Path.cwd()` を読む。
    `run_pnpm` の None は `prepare_host` の既定の runner を使う。
    """
    try:
        return _run(
            parse_args(argv),
            os.environ if environ is None else environ,
            Path.cwd() if cwd is None else cwd,
            run_pnpm,
            run_upstream,
            which,
        )
    except (UsageError, jevlint_tree.TreeError, jevlint_host.HostError) as error:
        print(f"jevlint: {error}", file=sys.stderr)
    except Exception as error:
        print(
            f"jevlint: 想定外のエラーで判定できない: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
