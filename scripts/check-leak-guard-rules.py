#!/usr/bin/env python3
"""漏洩ガードの custom ルールを、検出側と許可側の両方の対照で検証する。

`.gitleaks.toml` の user-path ルールが「捕まえるべきものを捕まえ、通すべきものを通す」
ことを確かめる。regex は読むだけでは正しさを判定できず、取りこぼしと誤検出の両方を
実際に埋め込んだ実績がある (経緯は `.gitleaks.toml` のコメントが持つ)。どちらも例外では
なく静かな取りこぼし / もっともらしい検出として出るので、検出側だけ、あるいは許可側だけの
対照では気づけない。両方を並べて初めて判定できる。

ケースは 1 つの一時ディレクトリへ書き出し、gitleaks を 1 回だけ呼ぶ。判定は exit code
ではなくレポートの内容で行う (gitleaks の exit code は「検出があったか」であって
「期待どおりか」ではない)。

終了コードは 0 (期待どおり) / 1 (不一致あり) / 2 (検査できなかった)。2 を 1 と分けるのは、
ルールの誤りと「gitleaks を走らせられなかった」を混ぜないため。後者は検査の結果ではなく、
同じ赤にすると config が壊れている状態がルールの誤りに見える。
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".gitleaks.toml"

# 検証対象のルール。canonical は `.gitleaks.toml` の [[rules]] id で、ここはその参照。
# 名前がずれると全ケース取りこぼしの赤になるので、ずれたまま緑にはならない。
RULE_ID = "user-path"

# 検出側のユーザー名は変数で埋める。ここへ literal で書くと このファイル自身が
# 漏洩ガードに捕まり pre-commit が通らなくなる (実際に踏んだ。13 件検出された)。
# `/Users/{NAME}` はソース上 `{` が続くのでルールの文字クラスに掛からず、実行時には
# 正しいケース文字列が組み上がる。allowlist へこのファイルを足して黙らせる手もあるが、
# それだとこのファイルに本物の漏洩が入っても素通りする。
NAME = "alice"

# `.gitleaks.toml` が押さえると宣言している形に、実際に掛かる変形を足したもの。
# 掛かる形の canonical はこのリストで、toml 側のコメントは意図の説明を持つ。
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

# allowlist が持つ名前は 1 つずつ全部並べる。覆っていない名前は allowlist から消しても
# 緑のままで、免除が縮んだことに気づけない。こちらは検出されない前提なので literal で
# 書いてよく、書けること自体が確認になる。
SHOULD_ALLOW = [
    ("placeholder-example", "/Users/example/project"),
    ("placeholder-user", "/Users/user/dev"),
    ("placeholder-username", "/Users/username/dev"),
    ("placeholder-angle-macos", "/Users/<name>/dev"),
    ("placeholder-angle-windows", r"C:\Users\<name>\AppData"),
    ("ci-runner", "/Users/runner/work/repo"),
    ("shared", "/Users/shared/data"),
    ("windows-public", r"C:\Users\Public\Documents"),
    ("windows-public-slash", "C:/Users/Public"),
    ("windows-public-trailing-period", r"C:\Users\Public."),
    ("windows-default", r"C:\Users\Default\NTUSER.DAT"),
    ("windows-default-app-pool", r"C:\Users\DefaultAppPool\AppData"),
    ("windows-all-users", r"C:\Users\All Users"),
    ("rest-api-users", "/api/users/123"),
]


class ProbeError(RuntimeError):
    """gitleaks を走らせられなかった。検出 0 件と区別するために送出する。"""


def detected_ids(workdir: Path) -> set[str]:
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
        raise ProbeError(f"gitleaks の実行に失敗した: {detail}")
    if not report.exists():
        raise ProbeError("gitleaks がレポートを出力しなかった")
    findings = json.loads(report.read_text(encoding="utf-8") or "[]")
    # RuleID で絞る。絞らないと、別のルールが同じケース文字列を拾ったときに
    # user-path が壊れていても「検出された」ことになって緑で隠れる。
    return {Path(f["File"]).stem for f in findings if f.get("RuleID") == RULE_ID}


def main() -> int:
    if shutil.which("gitleaks") is None:
        print("[x] gitleaks が見つからない (brew install gitleaks)")
        return 2

    cases = SHOULD_DETECT + SHOULD_ALLOW
    ids = [case_id for case_id, _ in cases]
    # ID が重複するとケースのファイルが上書きされ、片方が一度も検査されないまま
    # 両リストが同じ結果集合で判定される。失敗形が「1 件減った緑」なので先に弾く。
    if len(ids) != len(set(ids)):
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        print(f"[x] ケース ID が重複している: {', '.join(duplicated)}")
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        case_dir = workdir / "cases"
        case_dir.mkdir()
        for case_id, text in cases:
            (case_dir / f"{case_id}.txt").write_text(text + "\n", encoding="utf-8")
        try:
            found = detected_ids(workdir)
        except ProbeError as e:
            print(f"[x] {e}")
            return 2

    missed = [c for c, _ in SHOULD_DETECT if c not in found]
    false_positives = [c for c, _ in SHOULD_ALLOW if c in found]

    print(
        f"検査したケース: {len(cases)} 件 "
        f"(検出されるべき {len(SHOULD_DETECT)} / 許可されるべき {len(SHOULD_ALLOW)})"
    )
    for case_id in missed:
        print(f"  [x] 取りこぼし: {case_id} が検出されなかった")
    for case_id in false_positives:
        print(f"  [x] 誤検出: {case_id} を検出した")

    if missed or false_positives:
        print(f"[x] 不一致 {len(missed) + len(false_positives)} 件")
        return 1
    print("[+] 不一致なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
