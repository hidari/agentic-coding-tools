"""呼び出し側のリポジトリの中のパスの正規形と、中身を LLM へ送るファイルの読み取り。

設定の検査 (criteria・files・issues_glob) と道具の path の検査が同じ正規形の規則を使い、criteria の読み取りと
道具の読み取りが同じ「symlink を辿らない」読み手を使う。規則が2か所に分かれると、片方だけが NUL や
symlink を通すずれが生まれるため。エラーの文面は呼び出し側 (設定の誤りか、LLM へ返す道具の誤りか) が決める。
"""

import os
import stat
from pathlib import Path

# canonical_parts が拒否した理由。呼び出し側はこの値から自分の文面を選ぶ
UNREPRESENTABLE = "unrepresentable"
OUTSIDE = "outside"
NON_CANONICAL = "non-canonical"
GIT = "git"


class PathShapeError(ValueError):
    """正規形の相対パスではない。reason は上の4つの値のどれか。"""

    def __init__(self, reason: str, value: str):
        super().__init__(f"{reason}: {value!r}")
        self.reason = reason


class UnreadableFile(ValueError):
    """symlink を含む経路・通常のファイルでないもの・開けないファイル。"""


def is_git_name(name: str) -> bool:
    """パスの1つの component が .git か。"""
    # 大小を区別しない FS (macOS の既定) では .GIT も .git と同じ実体を指すので、casefold して比べる
    return name.casefold() == ".git"


def canonical_parts(value: str) -> tuple[str, ...]:
    """リポジトリの root からの正規形の相対パス (glob を含んでよい) を component に分けて返す。"""
    # NUL と孤立したサロゲートは OS のパスへ変換できず、開く時点で ValueError になる。形の誤りとして先に拒否する
    if "\x00" in value:
        raise PathShapeError(UNREPRESENTABLE, value)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise PathShapeError(UNREPRESENTABLE, value) from None
    parts = value.split("/")
    if value.startswith("/") or ".." in parts:
        raise PathShapeError(OUTSIDE, value)
    # glob は正規形の相対パスに当てるので、. や空の segment を含む値は照合と実体がずれ、検索が黙って0件になる
    if any(p in ("", ".") for p in parts):
        raise PathShapeError(NON_CANONICAL, value)
    # .git/config には checkout の認証情報が残りうる (未確認) ので、名前で拒否する
    if any(is_git_name(p) for p in parts):
        raise PathShapeError(GIT, value)
    return tuple(parts)


def read_regular(root: Path, parts: tuple[str, ...], errors: str = "strict") -> str:
    """root から component を1つずつ lstat し、symlink を含む経路と通常のファイル以外を拒否して UTF-8 で読む。

    parts は canonical_parts が返した形。errors は str.decode の errors と同じ。
    """
    # macOS の一時ディレクトリのように root 自体が symlink の下にあることがあるので、root は先に解決し、
    # symlink の検査は root より下の component だけに行う
    base = Path(root).resolve()
    shown = "/".join(parts)
    current = base
    try:
        for part in parts:
            current = current / part
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise UnreadableFile(f"symlink は辿りません: {shown}")
        if not stat.S_ISREG(os.lstat(current).st_mode):
            raise UnreadableFile(f"通常のファイルではありません: {shown}")
        if not current.resolve().is_relative_to(base):
            raise UnreadableFile(f"リポジトリの外です: {shown}")
        # 検査と open のあいだに差し替えられても最後の component の symlink は辿らない
        fd = os.open(current, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with open(fd, encoding="utf-8", errors=errors) as f:
            return f.read()
    except OSError as e:
        raise UnreadableFile(f"読めません: {shown} ({e.strerror})") from None
