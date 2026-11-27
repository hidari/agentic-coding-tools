"""入口のスクリプトが job の外と交わす読み書き (artifact の JSON、GITHUB_OUTPUT、step summary、呼び出し側のファイル)。

判定は持たない。読めないファイルをどう扱うかは呼び出し側 (本体の関数) が決められるよう、読めなければ
None を返すか ValueError / OSError を上げる。ファイルは組み込みの open で開く (どのパスを開いたかを
テストが open の差し替えで記録するため)。中身を LLM へ送る criteria だけは、道具と同じく repo_paths の
symlink を辿らない読み手で読む。
"""

import json
import sys
from pathlib import Path
from typing import Callable, TypeVar

from tech_briefing import repo_paths
from tech_briefing.protocols import YamlLoader

_T = TypeVar("_T")

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


def read_under(root: Path, rel: str) -> str:
    """root の下のファイルを読む。正規形でないパス (repo_paths.canonical_parts の規則)・root の外を指す symlink・
    解決した先が .git の中のものは ValueError。

    root の中のふつうのファイルを指す symlink は辿る。読むのは設定と呼び出しの workflow の YAML で、中身は LLM へ
    送らない。.git を拒否するのは、.git/config を YAML として読ませると、読み手の誤りの文やログに checkout の
    設定の断片が出うるため。
    """
    if not isinstance(rel, str):
        raise ValueError(f"root からの相対パスではありません: {rel!r}")
    repo_paths.canonical_parts(rel)
    base = Path(root).resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"root の外を指しています: {rel!r}")
    # 文字列の検査は symlink の先を見ないので、解決した先の経路にも同じ規則を当てる
    repo_paths.canonical_parts(target.relative_to(base).as_posix())
    with open(target, encoding="utf-8") as f:
        return f.read()


def read_criteria(root: Path, rel: str) -> str:
    """基準の文書を読む。正規形でないパスと、経路のどこかが symlink のものは ValueError。

    中身は LLM へ送られる。設定の検査はパスの文字列しか見ないので、symlink は指す先を問わず辿らない
    (指す先で .git/config を拒否する形は、指す先の判定を誤ると認証情報を送る)。UTF-8 として読めない
    ファイルは置き換えずに UnicodeDecodeError (ValueError) にする。
    """
    return repo_paths.read_regular(root, rel)


def _none_on_error(rel: str, read: Callable[[], _T]) -> _T | None:
    """read() の OSError と ValueError を、理由を内部のログに出して None にする。

    None を本体の関数へ渡すと、設定の誤りとして扱う (config-error)。UTF-8 として読めないファイルの
    UnicodeDecodeError と、repo_paths の拒否はどちらも ValueError。
    """
    try:
        return read()
    except (OSError, ValueError) as e:
        print(f"{rel} を読めませんでした: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def read_criteria_or_none(root: Path, rel: str) -> str | None:
    """read_criteria と同じ。読めなければ理由を内部のログに出して None を返す。"""
    return _none_on_error(rel, lambda: read_criteria(root, rel))


def load_yaml_under(root: Path, rel: str | None, loader: YamlLoader) -> object | None:
    """root の下の YAML を loader で読む。読めなければ理由を内部のログに出して None を返す。

    loader の誤りは型だけを出す。YAML のパーサの例外の文は該当の行の断片を含み、Actions のログに出るため。
    loader が約束 (ValueError) の外の例外を上げても同じに扱う。PyYAML の constructor は YAMLError ではない
    KeyError などを値を含む文で上げ、上へ逃がすと traceback がその文をログに出す。
    """
    if rel is None:
        print("読むファイルを特定できませんでした", file=sys.stderr)
        return None
    text = _none_on_error(rel, lambda: read_under(root, rel))
    if text is None:
        return None
    try:
        return loader(text)
    except Exception as e:
        print(f"{rel} を YAML として読めませんでした: {type(e).__name__}", file=sys.stderr)
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
    if not path.startswith(_WORKFLOWS_DIR):
        return None
    try:
        # 道具や criteria と同じ正規形の規則で見る
        repo_paths.canonical_parts(path)
    except repo_paths.PathShapeError:
        return None
    return path
