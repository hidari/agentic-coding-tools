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
    1: 厳選した rule が新しい版か pin した版に無い、kind が変わった、生成した設定で上流が
       異常終了した、手元の node が `engines` を満たさないか `engines` が無いか `>=N` の形で
       ない、のいずれか
    0: それ以外 (報告のみ。cutoff の変化、rule.yml の変化、増えた rule があっても失敗ではない)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Mapping, NoReturn

import jevlint_compat
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


def _prepare_host(version: str, environ: Mapping[str, str], run_pnpm: "Callable | None") -> Path:
    # 既定の runner は `prepare_host` 自身のもの (pnpm の stdout を stderr へ流す) に任せる。
    # `subprocess.run` をそのまま渡すと、初回の取得で pnpm の進捗が要約の stdout に混ざる
    if run_pnpm is None:
        return jevlint_host.prepare_host(version, environ)
    return jevlint_host.prepare_host(version, environ, run=run_pnpm)


def _local_node(host: Path, env: dict, run_upstream: Callable, which: Callable) -> tuple:
    """起動に使う node を 1 度だけ解決し、(絶対パス, `node --version` の版) を返す。

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
    return node, jevlint_host.parse_node_version(proc.stdout)


def _checked_node(host: Path, env: dict, run_upstream: Callable, which: Callable) -> str:
    """`_local_node` の node が、host の jev-lint の `engines.node` を満たすか見る。"""
    node, version = _local_node(host, env, run_upstream, which)
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


def _check_out_path(path: Path, shown: str, expanded: jevlint_tree.Expanded) -> None:
    """保存先 1 つを、symlink を解決した先で検査する。`shown` は利用者が書いた形のパス。

    展開の worktree と scratch の中は、抜けるときに消えるので拒否する。包含は inode で
    見る。大文字小文字を区別しないファイルシステムでは、文字列の比較は `.../Tree` と
    `.../tree` の包含を見落とす (`jevlint_tree._temp_base` と同じ理由)。
    """
    resolved = path.resolve()
    for ancestor in (resolved, *resolved.parents):
        for inside in (expanded.tree, expanded.scratch):
            try:
                same = os.path.samefile(ancestor, inside)
            except OSError:
                # まだ無いパス。同じ inode を指しようがない
                continue
            if same:
                raise UsageError(f"--json-out の保存先が展開した一時ディレクトリの中にある: {shown!r}")
    if not resolved.parent.is_dir():
        raise UsageError(f"--json-out の保存先の親ディレクトリが無い: {shown!r}")
    if resolved.is_dir():
        raise UsageError(f"--json-out の保存先がディレクトリ: {shown!r}")


def _json_out_targets(
    text: str, cwd: Path, expanded: jevlint_tree.Expanded, *, record: bool
) -> "tuple[Path, Path | None]":
    """`--json-out` の保存先 (JSON と、`record` のときは記録) を、上流を起動する前に検査する。

    `<path>` は main の `cwd` からの相対で解釈する。記録は `<path>.record.json` で、名前は
    利用者が書いたパスから作る (`<path>` が symlink でも、その解決先の名前からは作らない)。
    上流の起動は課金されるので、書けない保存先で起動の後に失敗しないよう、どちらの保存先も
    親ディレクトリがあることとディレクトリでないことをここで確かめる (権限までは見ない)。
    `--dry-run` は記録を書かないので、記録の保存先は見ない。
    """
    given = Path(text)
    if not given.is_absolute():
        given = cwd / given
    _check_out_path(given, text, expanded)
    if not record:
        return given, None
    record_path = Path(f"{given}.record.json")
    _check_out_path(record_path, f"{text}.record.json", expanded)
    return given, record_path


def _read_record(record: "Path | None") -> "str | None":
    """`--record` の記録。無いか読めなければ None にし、判定 (classify) で 2 にさせる。"""
    if record is None:
        return None
    try:
        return record.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _save_json_out(
    targets: "tuple[Path, Path | None]", stdout: str, record_text: "str | None"
) -> None:
    json_path, record_path = targets
    try:
        json_path.write_text(stdout, encoding="utf-8")
        if record_path is not None:
            record_path.write_text(record_text, encoding="utf-8")
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
    if args.command == "compat":
        return _run_compat(args.version, environ, run_pnpm, run_upstream, which)
    thresholds = dict(parse_threshold(text) for text in args.threshold)

    # ラッパ自身の git と `node --version` の env。キーを含まない
    keyless_env = jevlint_host.build_env(environ, with_key=False)
    root = jevlint_tree.repo_root(cwd, keyless_env)
    sha = jevlint_tree.resolve_commit(root, args.commit or "HEAD", keyless_env)
    base = None
    if args.command == "review":
        base = jevlint_tree.resolve_commit(root, args.base, keyless_env)
    paths = _committed_paths(root, sha, args.paths, keyless_env)

    host = _prepare_host(UPSTREAM_VERSION, environ, run_pnpm)
    node = _checked_node(host, keyless_env, run_upstream, which)
    if not args.dry_run:
        _require_key(environ)

    with jevlint_tree.signals_as_exceptions():
        with jevlint_tree.expanded_commit(root, sha, keyless_env) as expanded:
            json_out = None
            if args.json_out is not None:
                json_out = _json_out_targets(
                    args.json_out, cwd, expanded, record=not args.dry_run
                )
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


# compat が 2 通り目の設定で 1 項目に持たせる threshold。確かめたいのは上流が `threshold` と
# いう項目を読めるかなので、0 と 1 の間ならどの値でもよい
_COMPAT_THRESHOLD = 0.5


def _compat_scratch(env: dict) -> "tempfile.TemporaryDirectory":
    """compat の一時ディレクトリ。置き場は `env` の `TMPDIR` (絶対パスのときだけ) で決める。

    `jevlint_tree._temp_base` と同じく、上流に渡す env と同じ値で置き場が決まるようにし、
    テストが置き場を差し替えられるようにする。相対の値をそのまま渡すと cwd の下に作られ、3.9
    では返るパスも相対になる (`_temp_base` の実測)。compat はリポジトリを使わないので、置き場が
    リポジトリの中かどうかは見ない。
    """
    candidate = env.get("TMPDIR", "")
    base = candidate if candidate and os.path.isabs(candidate) else None
    try:
        return tempfile.TemporaryDirectory(prefix="jevlint-compat-", dir=base)
    except OSError as error:
        raise jevlint_host.HostError(f"一時ディレクトリを作れない: {error}") from None


def _rules_table(
    node: str, host: Path, version: str, cwd: Path, env: dict, run_upstream: Callable
) -> dict:
    """host の jev-lint の `rules --json --no-config` を表にする。

    argv は `upstream_argv` で組まない。あちらは `--config` を含む固定の末尾を必ず足し、
    `--no-config` と `--config` は後勝ちなので (spec の前提 5)、表が設定の影響を受ける。cwd は
    空の一時ディレクトリで、利用者の `.jev-lint/rules/` を拾わない。終了コードが 0 でなければ
    表を使わない: 上流は rule の読み込みにエラーがあると JSON を出しつつ 2 を返す
    (`src/cli/cmd-rules.ts`)。欠けた表で比べると、読めなかった rule を「消えた」と取り違える。
    """
    argv = [node, str(host / "node_modules/jev-lint/dist/cli.js"), "rules", "--json", "--no-config"]
    try:
        proc = run_upstream(
            argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE, text=True, encoding="utf-8"
        )
    except OSError as error:
        raise jevlint_host.HostError(
            f"jev-lint {version} の rules --json を起動できない: {error}"
        ) from None
    if proc.returncode != 0:
        raise jevlint_host.HostError(
            f"jev-lint {version} の rules --json が終了コード {proc.returncode} で終わった"
        )
    try:
        return jevlint_compat.parse_rules(proc.stdout)
    except ValueError as error:
        raise jevlint_host.HostError(f"jev-lint {version} の {error}") from None


def _read_rule_yml(source: str) -> bytes:
    try:
        return Path(source).read_bytes()
    except OSError as error:
        raise jevlint_host.HostError(f"rule.yml を読めない: {source}: {error}") from None


def _load_configs(
    node: str,
    host: Path,
    version: str,
    tree: Path,
    scratch: Path,
    env: dict,
    run_upstream: Callable,
) -> list:
    """生成した設定を `version` の上流に `--dry-run` で読ませ、(見出し, 終了コード) の列を返す。

    既定の設定と、1 項目だけ `threshold` を持つ設定の 2 通り (`threshold` は 0.7.0 で `at` から
    改名され、0.6.7 以前は知らないフィールドとして拒否する)。argv は本番と同じ `upstream_argv`
    で組む。cwd は最小のファイルだけを置いた `tree` で、設定はその外の `scratch` に書く (上流の
    baseDir は設定のディレクトリになる)。上流の stderr は捕まえずに流し、判定には終了コード
    だけを使う (stderr の文面は上流の版で変わる)。stdout (dry-run の JSON) は捨てる。
    """
    key = CURATED[0]
    cases = (
        ("既定", {}),
        (f"{key} に threshold {_COMPAT_THRESHOLD}", {key: _COMPAT_THRESHOLD}),
    )
    results = []
    for index, (label, thresholds) in enumerate(cases):
        config = scratch / f"config-{index}.json"
        config.write_text(json.dumps(build_config(thresholds)), encoding="utf-8")
        argv = jevlint_host.upstream_argv(
            node,
            host,
            "check",
            base=None,
            excludes=[],
            paths=["."],
            dry_run=True,
            record=None,
            config=config,
        )
        # 上流の stderr がこの後に続くので、どちらの設定の起動かを先に告げる
        print(f"jevlint: 設定 ({label}) を jev-lint {version} に読ませる", file=sys.stderr)
        try:
            proc = run_upstream(argv, cwd=str(tree), env=env, stdout=subprocess.DEVNULL)
        except OSError as error:
            raise jevlint_host.HostError(f"jev-lint {version} を起動できない: {error}") from None
        results.append((label, proc.returncode))
    return results


def _config_line(label: str, returncode: int) -> str:
    if returncode == 0:
        return f"設定の読み込み ({label}): 通った"
    return f"設定の読み込み ({label}): 終了コード {returncode} で失敗 (理由は上流の stderr)"


def _engines_line(host: Path, node_version: tuple) -> "tuple[bool, str]":
    """引数の版の `engines.node` と手元の node の版を比べ、(満たすか, 報告の行) を返す。

    check と review は engines が読めないか `>=N` の形でないと 2 にするが、compat ではそれを
    「その版に上げると check と review が動かない」という結果として失敗 (1) にする。
    """
    shown = ".".join(str(part) for part in node_version)
    try:
        engines = jevlint_host.read_engines(host)
        satisfied = jevlint_host.engines_ok(engines, node_version)
    except jevlint_host.HostError as error:
        return False, f"engines.node を判定できない、手元の node {shown}: {error}"
    verdict = "満たす" if satisfied else "満たさない"
    return satisfied, f"engines.node {engines}、手元の node {shown}: {verdict}"


def _run_compat(
    version_text: str,
    environ: Mapping[str, str],
    run_pnpm: "Callable | None",
    run_upstream: Callable,
    which: Callable,
) -> int:
    """pin した版と `version_text` の版を比べ、報告を stdout に書いて終了コードを返す。

    段の順序: 版の検査 → 両方の版の host の用意 → node の解決 → 両方の版の `rules --json` と
    比較 → 引数の版に設定を読ませる → 引数の版の engines。どの子プロセスの env もキーを含まない
    (pnpm は `install_env`、node と上流は `build_env(with_key=False)`)。検査不能 (例外) の
    ときは stdout に何も書かない。報告は最後にまとめて書く。
    """
    version = parse_version(version_text)
    env = jevlint_host.build_env(environ, with_key=False)
    pinned_host = _prepare_host(UPSTREAM_VERSION, environ, run_pnpm)
    host = _prepare_host(version, environ, run_pnpm)
    node, node_version = _local_node(pinned_host, env, run_upstream, which)
    with _compat_scratch(env) as scratch_name:
        scratch = Path(scratch_name)
        empty, tree = scratch / "empty", scratch / "tree"
        empty.mkdir()
        tree.mkdir()
        old = _rules_table(node, pinned_host, UPSTREAM_VERSION, empty, env, run_upstream)
        new = _rules_table(node, host, version, empty, env, run_upstream)
        report = jevlint_compat.compare(
            old, new, CURATED, _read_rule_yml, labels=(UPSTREAM_VERSION, version)
        )
        for file_name, text in jevlint_compat.SAMPLES.items():
            (tree / file_name).write_text(text, encoding="utf-8")
        configs = _load_configs(node, host, version, tree, scratch, env, run_upstream)
    satisfied, engines_line = _engines_line(host, node_version)
    lines = [
        f"jev-lint {UPSTREAM_VERSION} (pin、rule {len(old)} 件) と {version} (rule {len(new)} 件)"
        f" を、厳選の {len(CURATED)} 項目で比べた",
        *jevlint_compat.describe(report),
        *(_config_line(label, returncode) for label, returncode in configs),
        engines_line,
    ]
    print("\n".join(lines))
    return jevlint_compat.compat_exit(report, [rc for _, rc in configs], satisfied)


def main(
    argv: list[str],
    *,
    environ: "Mapping[str, str] | None" = None,
    cwd: "Path | None" = None,
    run_pnpm: "Callable | None" = None,
    run_upstream: Callable = subprocess.run,
    which: Callable = shutil.which,
) -> int:
    """check / review / compat を実行し、終了コード (意味はこのモジュールの docstring) を返す。

    compat の段は `_run_compat` が持つ。以下は check / review の段。

    段の順序は仕様である: 引数 → ref とパス (本体のリポジトリの git) → host の用意 (キー
    無し) → node と engines → キー → コミットの展開 → 上流の起動 → 判定と要約。host の用意を
    展開より先に置くのは、pnpm が走る時点で worktree (コミットされた `.npmrc` がありうる) を
    まだ存在させないため。上流へ渡す env だけが、`--dry-run` でないときにキーを持つ。

    stdout には要約だけを書く。エラーは stderr に 1 行で書き、2 を返す。判定が 2 のときは
    要約も `--json-out` の保存もしない (stdout が JSON でないこともあるため)。例外は種類を
    問わず 2 にする。捕まらない例外で Python が返す 1 は「finding あり」と衝突するため。

    SIGTERM / SIGHUP は展開の間だけ `SignalInterrupt` に変わり、展開の後始末を済ませてから
    ここで 128 + シグナル番号 (シェルの慣例) を返す。素通しにできないのは、捕まえない
    KeyboardInterrupt の派生で Python 3.9.6 は 1 で終わるため (素の KeyboardInterrupt は
    SIGINT で終わる。3.14.7 はどちらも SIGINT。実測)。Ctrl-C の KeyboardInterrupt は捕まえず、
    後始末の後に Python の既定 (SIGINT で終わる) に任せる。

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
    except jevlint_tree.SignalInterrupt as interrupt:
        name = signal.Signals(interrupt.signum).name
        print(f"jevlint: {name} を受けて中断した", file=sys.stderr)
        return 128 + interrupt.signum
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
