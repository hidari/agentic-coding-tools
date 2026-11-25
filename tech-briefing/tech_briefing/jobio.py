"""入口のスクリプトが job の外と交わす読み書き (artifact の JSON、GITHUB_OUTPUT、step summary、呼び出し側のファイル)。

判定は持たない。読めないファイルをどう扱うかは呼び出し側 (本体の関数) が決められるよう、読めなければ
None を返すか ValueError / OSError を上げる。ファイルは組み込みの open で開く (どのパスを開いたかを
テストが open の差し替えで記録するため)。
"""

import json
import sys
from pathlib import Path

from tech_briefing.config import is_git_name
from tech_briefing.protocols import YamlLoader

_WORKFLOWS_DIR = ".github/workflows/"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # LLM の応答の JSON は \ud800 のようなエスケープで孤立したサロゲートを作れる。UTF-8 にできない文字は
    # \uXXXX のエスケープとして書けば、JSON として同じ値に読み戻せる
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with open(path, "wb") as f:
        f.write(text.encode("utf-8", "backslashreplace"))


def read_json(path: Path) -> object:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_json_or_none(path: Path) -> object | None:
    """無いファイルと JSON でないファイルは None。artifact が無いことは本体の関数が internal-error にする。"""
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def write_output(env, key: str, value: str) -> None:
    # 1行の key=value の形だけを使う。改行を含む値は後ろの行が別の出力として読まれる
    if "\n" in value or "\r" in value:
        raise ValueError(f"GITHUB_OUTPUT の値に改行は置けません: {key}")
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def append_summary(env, text: str) -> None:
    with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n")


def _resolve_under(root: Path, rel: str) -> tuple[Path, Path]:
    """(解決した root, 解決した実体のパス)。絶対パス・root の外へ出る .. や symlink は ValueError。"""
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        raise ValueError(f"root からの相対パスではありません: {rel!r}")
    base = Path(root).resolve()
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"root の外を指しています: {rel!r}")
    return base, target


def _read(target: Path) -> str:
    with open(target, encoding="utf-8") as f:
        return f.read()


def read_under(root: Path, rel: str) -> str:
    """root の下のファイルを読む。絶対パス・root の外へ出る .. や symlink は ValueError。"""
    return _read(_resolve_under(root, rel)[1])


def read_criteria(root: Path, rel: str) -> str:
    """基準の文書を読む。read_under の制限に加え、実体が .git の中なら ValueError。

    中身は LLM へ送られる。設定の検査はパスの文字列しか見ないので、リポジトリの中の symlink が
    .git/config (checkout の認証情報が残りうる) を指す形は、symlink を解決した後の実体の位置で拒否する。
    """
    base, target = _resolve_under(root, rel)
    if any(is_git_name(p) for p in target.relative_to(base).parts):
        raise ValueError(f".git の中を指しています: {rel!r}")
    return _read(target)


def read_criteria_or_none(root: Path, rel: str) -> str | None:
    """read_criteria と同じ。読めなければ理由を内部のログに出して None を返す。

    None を本体の関数へ渡すと、設定の誤りとして扱う (config-error)。
    """
    try:
        return read_criteria(root, rel)
    except (OSError, ValueError) as e:
        # UTF-8 として読めないファイルの UnicodeDecodeError も ValueError
        print(f"{rel} を読めませんでした: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def load_yaml_under(root: Path, rel: str | None, loader: YamlLoader) -> object | None:
    """root の下の YAML を loader で読む。読めなければ理由を内部のログに出して None を返す。

    None を本体の関数へ渡すと、設定と呼び出し側の検査が誤りとして扱う (config-error)。
    """
    if rel is None:
        print("読むファイルを特定できませんでした", file=sys.stderr)
        return None
    try:
        return loader(read_under(root, rel))
    except (OSError, ValueError) as e:
        print(f"{rel} を読めませんでした: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def caller_workflow_path(workflow_ref: str, repository: str) -> str | None:
    """github.workflow_ref (`owner/repo/.github/workflows/x.yml@ref`) から、リポジトリの中のパスを返す。

    呼び出し側のリポジトリの workflow でなければ None。
    """
    ref, sep, _ = workflow_ref.partition("@")
    prefix = repository + "/"
    if not sep or not ref.startswith(prefix):
        return None
    path = ref[len(prefix):]
    if not path.startswith(_WORKFLOWS_DIR) or ".." in path.split("/"):
        return None
    return path
