#!/usr/bin/env python3
"""漏洩ガードの custom ルールを、検出側と許可側の両方の対照で検証する。

`.gitleaks.toml` の `user-path` ルールが「捕まえるべきものを捕まえ、通すべきものを通す」
ことを確かめる。ルールの regex は読むだけでは正しさが判定できず、実際に誤りを 2 種類とも
埋め込んだ実績がある。

  - 取りこぼし: OS 別に 2 ルールへ割った版で、UNC・ドライブ省略・エスケープ済みの
    3 形式がどちらの網からも落ちた。実際に履歴へ入った
  - 誤検出: 大小無視の `(?i)` をパターン先頭へ置いた版が、`C:/Users/Public` (winvm が
    一時ファイルの置き場に使う) と REST API の `/api/users/<id>` を拾った

どちらも「エラー」ではなく静かな取りこぼし / もっともらしい検出として出るので、
検出側だけ、あるいは許可側だけの対照では気づけない。両方を並べて初めて判定できる。

ケースは 1 つの一時ディレクトリへ書き出し、gitleaks を 1 回だけ呼ぶ。判定は exit code
ではなく JSON レポートの内容で行う (gitleaks の exit code は「検出があったか」であって
「期待どおりか」ではない)。

終了コードは 0 (期待どおり) / 1 (不一致あり) / 2 (gitleaks を走らせられなかった)。
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".gitleaks.toml"

# 検出側のユーザー名は変数で埋める。ここへ literal で書くと **このファイル自身が
# 漏洩ガードに捕まり** pre-commit が通らなくなる (実際に踏んだ。13 件検出された)。
# `/Users/{NAME}` はソース上 `{` が続くのでルールの文字クラスに掛からず、実行時には
# 正しいケース文字列が組み上がる。allowlist へこのファイルを足して黙らせる手もあるが、
# それだとこのファイルに本物の漏洩が入っても素通りする。
NAME = "alice"

# 検出されるべき書かれ方。`.gitleaks.toml` のコメントが挙げる形を網羅する。
SHOULD_DETECT = [
    ("macos", f"/Users/{NAME}/dev/project"),
    ("windows-backslash", rf"C:\Users\{NAME}\dev"),
    ("windows-lowercase-drive", rf"c:\Users\{NAME}"),
    ("windows-slash", f"C:/Users/{NAME}"),
    ("escaped-double-backslash", rf"C:\\Users\\{NAME}"),
    ("json-value", rf'{{"home": "C:\\Users\\{NAME}"}}'),
    ("unc-parallels-share", rf"\\Mac\AllFiles\Users\{NAME}"),
    ("unc-server", rf"\\server\share\Users\{NAME}"),
    ("drive-omitted", rf"\Users\{NAME}"),
    ("wsl", f"/mnt/c/Users/{NAME}"),
    # 名前の変形は必ず波括弧の内側で行い、パス区切りの直後に literal 文字を置かないこと。
    # アンダースコアを外へ出すとルールの文字クラス先頭に掛かり、そこまでで match が
    # 成立してこのファイル自身が捕まる (実際に踏んだ。この注意書き自体でも 1 度踏んだ)。
    ("uppercase-name", f"/Users/{NAME.capitalize()}"),
    ("dotted-name", f"/Users/{NAME}.b"),
    ("underscore-name", f"/Users/{'_' + NAME}"),
]

# 許可されるべき書かれ方。placeholder・CI・Windows 自身のディレクトリ・別文脈の users。
# こちらは検出されない前提なので literal で書いてよい (書けることの確認も兼ねる)。
#
# allowlist が持つ名前は 1 つずつ全部並べる。覆っていない名前は allowlist から
# 消しても緑のままで、免除が縮んだことに気づけない。
SHOULD_ALLOW = [
    ("placeholder-example", "/Users/example/project"),
    ("placeholder-user", "/Users/user/dev"),
    ("placeholder-username", "/Users/username/dev"),
    ("placeholder-angle-macos", "/Users/<name>/dev"),
    ("placeholder-angle-windows", r"C:\Users\<name>\AppData"),
    ("ci-runner", "/Users/runner/work/repo"),
    ("shared", "/Users/shared/data"),
    ("windows-default-app-pool", r"C:\Users\DefaultAppPool\AppData"),
    ("windows-public", r"C:\Users\Public\Documents"),
    ("windows-public-slash", "C:/Users/Public"),
    ("windows-public-trailing-period", r"C:\Users\Public."),
    ("windows-default", r"C:\Users\Default\NTUSER.DAT"),
    ("windows-all-users", r"C:\Users\All Users"),
    ("rest-api-users", "/api/users/123"),
]


class ProbeError(RuntimeError):
    """gitleaks を走らせられなかった。検出 0 件と区別するために送出する。"""


def detected_ids(workdir: Path) -> set[str]:
    """gitleaks を 1 回走らせ、検出されたケース ID の集合を返す。"""
    report = workdir / "report.json"
    proc = subprocess.run(
        [
            "gitleaks",
            "dir",
            str(workdir / "cases"),
            "-c",
            str(CONFIG),
            "--report-format",
            "json",
            "--report-path",
            str(report),
            "--no-banner",
            "--redact",
            # 検出があっても 0 で返させる。判定はレポートの内容で行う。
            "--exit-code",
            "0",
        ],
        capture_output=True,
        check=False,
    )
    # `--exit-code 0` を渡しているので、非 0 は「漏洩を検出した」ではなく
    # 「gitleaks を走らせられなかった」を意味する。config の構文エラーはこちらに来る。
    # ここを握り潰すと、設定が壊れている状態が「検出 0 件」に化けて全ケース取りこぼしに
    # 見える (実際に allowlist を空にする変異で踏んだ)。
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip() or f"rc={proc.returncode}"
        raise ProbeError(f"gitleaks の実行に失敗しました: {detail}")
    if not report.exists():
        raise ProbeError("gitleaks がレポートを出力しませんでした")
    findings = json.loads(report.read_text(encoding="utf-8") or "[]")
    return {Path(f["File"]).stem for f in findings}


def main() -> int:
    if shutil.which("gitleaks") is None:
        print("error: gitleaks が見つかりません (brew install gitleaks)", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        cases = workdir / "cases"
        cases.mkdir()
        for case_id, text in SHOULD_DETECT + SHOULD_ALLOW:
            (cases / f"{case_id}.txt").write_text(text + "\n", encoding="utf-8")
        try:
            found = detected_ids(workdir)
        except ProbeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2

    missed = [c for c, _ in SHOULD_DETECT if c not in found]
    false_positives = [c for c, _ in SHOULD_ALLOW if c in found]

    total = len(SHOULD_DETECT) + len(SHOULD_ALLOW)
    print(
        f"検査したケース: {total} 件 "
        f"(検出されるべき {len(SHOULD_DETECT)} / 許可されるべき {len(SHOULD_ALLOW)})"
    )
    for case_id in missed:
        print(f"  取りこぼし: {case_id} が検出されなかった")
    for case_id in false_positives:
        print(f"  誤検出: {case_id} を検出した")

    if missed or false_positives:
        print(f"不一致: {len(missed) + len(false_positives)} 件")
        return 1
    print("不一致なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
