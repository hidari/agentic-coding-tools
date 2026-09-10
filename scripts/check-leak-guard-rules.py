#!/usr/bin/env python3
"""漏洩ガードの custom ルールを、検出側と許可側の両方の対照で検証する。

`.gitleaks.toml` の custom ルールが「捕まえるべきものを捕まえ、通すべきものを通す」
ことを確かめる。対象は RULE_IDS が持つ。regex は読むだけでは正しさを判定できず、
取りこぼしと誤検出の両方を
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
# 存在を先に確かめて、名前のずれ (検査不能) をルールの誤り (不一致) と分ける。
# 分けないと rename が「全ケース取りこぼし」に化けて、regex が壊れた形と同じ赤になる。
RULE_IDS = ("user-path", "vm-uuid", "email-address")

# 検出側のユーザー名は変数で埋める。ここへ literal で書くと このファイル自身が
# 漏洩ガードに捕まり pre-commit が通らなくなる (実際に踏んだ。13 件検出された)。
# `/Users/{NAME}` はソース上 `{` が続くのでルールの文字クラスに掛からず、実行時には
# 正しいケース文字列が組み上がる。allowlist へこのファイルを足して黙らせる手もあるが、
# それだとこのファイルに本物の漏洩が入っても素通りする。
NAME = "alice"

# UUID とメールアドレスも同じ理由で組み立てる。literal で書くと vm-uuid / email-address が
# このファイルを捕まえ、漏洩ガードを検証するファイルが漏洩として報告される。
# 連結の切れ目はソース上だけのもので、実行時には検査対象と同じ 1 本の文字列になる。
_UUID_PARTS = ("1234abcd", "5678", "4901", "8abc", "def012345678")
UUID_LOWER = "-".join(_UUID_PARTS)
UUID_UPPER = UUID_LOWER.upper()
MAIL = f"{NAME}@" + "mail.internal" + ".corp"

# `.gitleaks.toml` が押さえると宣言している形に、実際に掛かる変形を足したもの。
# 掛かる形の canonical はこのリストで、toml 側のコメントは意図の説明を持つ。
#
# 各ケースは「どのルールが捕まえるべきか」を持つ。別のルールが同じ文字列を拾ったときに
# 対象ルールが壊れていても緑になる形を避けるため、検出側は対象ルールで絞って判定する。
SHOULD_DETECT = [
    ("user-path", "macos", f"/Users/{NAME}/dev/project"),
    ("user-path", "windows-backslash", rf"C:\Users\{NAME}\dev"),
    ("user-path", "windows-lowercase-drive", rf"c:\Users\{NAME}"),
    ("user-path", "windows-slash", f"C:/Users/{NAME}"),
    ("user-path", "escaped-double-backslash", rf"C:\\Users\\{NAME}"),
    ("user-path", "json-value", rf'{{"home": "C:\\Users\\{NAME}"}}'),
    ("user-path", "unc-parallels-share", rf"\\Mac\AllFiles\Users\{NAME}"),
    ("user-path", "unc-server", rf"\\server\share\Users\{NAME}"),
    ("user-path", "drive-omitted", rf"\Users\{NAME}"),
    ("user-path", "wsl", f"/mnt/c/Users/{NAME}"),
    # 名前の変形は必ず波括弧の内側で行い、パス区切りの直後に literal 文字を置かないこと。
    # アンダースコアを外へ出すとルールの文字クラス先頭に掛かり、そこまでで match が
    # 成立してこのファイル自身が捕まる (実際に踏んだ。この注意書き自体でも 1 度踏んだ)。
    ("user-path", "uppercase-name", f"/Users/{NAME.capitalize()}"),
    ("user-path", "dotted-name", f"/Users/{NAME}.b"),
    ("user-path", "underscore-name", f"/Users/{'_' + NAME}"),
    # 大小の両方を置くのは、ISSUE-15 が小文字だけで数えて 3 ファイルを落とし、その教訓を
    # 書いている最中に大文字の UUID を取りこぼした実績があるため。
    ("vm-uuid", "uuid-lowercase", UUID_LOWER),
    ("vm-uuid", "uuid-uppercase", UUID_UPPER),
    # 実際の露出は prlctl の出力を貼る経路で起きたので、引用符に囲まれた形も置く。
    ("vm-uuid", "uuid-in-json", f'{{"ID": "{{{UUID_LOWER}}}"}}'),
    ("email-address", "mail-plain", MAIL),
    ("email-address", "mail-in-prose", f"連絡先は {MAIL} まで"),
]

# allowlist が持つ名前は 1 つずつ全部並べる。覆っていない名前は allowlist から消しても
# 緑のままで、免除が縮んだことに気づけない。こちらは検出されない前提なので literal で
# 書いてよく、書けること自体が確認になる。
#
# 許可側は「どのルールにも掛からないこと」で判定する。利用者から見ればどのルールが撃っても
# コミットは止まるので、対象ルールだけを見ると別ルールの誤検出を素通りさせる。
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
    # 追跡下の test_winvm.py / test_macvm.py が使っている合成 fixture。実測で 16 出現あり、
    # 免除しないと既存ツリーが赤くなる。パスで免除しない理由は `.gitleaks.toml` が持つ。
    ("uuid-fixture-a", "aaaa1111-bbbb-4ccc-8ddd-eeee22223333"),
    ("uuid-fixture-b", "ffff4444-aaaa-4bbb-8ccc-dddd55556666"),
    ("uuid-fixture-b-upper", "FFFF4444-AAAA-4BBB-8CCC-DDDD55556666"),
    ("uuid-nil", "00000000-0000-0000-0000-000000000000"),
    # RFC 2606 と RFC 6761 の予約。追跡下の 8 出現はすべて .invalid だった (実測)。
    ("mail-example-com", "user@example.com"),
    ("mail-example-org", "user@example.org"),
    ("mail-example-net", "user@example.net"),
    ("mail-invalid-tld", "probe@example.invalid"),
    ("mail-test-tld", "noreply@service.test"),
    ("mail-localhost-tld", "root@box.localhost"),
]


class ProbeError(RuntimeError):
    """gitleaks を走らせられなかった。検出 0 件と区別するために送出する。"""


def detections(workdir: Path) -> list[tuple[str, str]]:
    """(ルール ID, ケース ID) の組を返す。1 ケースが複数ルールに掛かる形も落とさない。"""
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
    # RuleID を捨てずに返す。捨てて集合にすると、別のルールが同じケース文字列を拾った
    # ときに対象ルールが壊れていても「検出された」ことになって緑で隠れる。
    return [(f.get("RuleID", ""), Path(f["File"]).stem) for f in findings]


def missing_rules() -> list[str]:
    """`.gitleaks.toml` に定義が無いルール ID を返す。

    実行前に確かめるのは、rename を「全ケース取りこぼし」ではなく検査不能として
    報告するため。取りこぼしと同じ赤にすると、regex が壊れた形と区別できない。
    """
    text = CONFIG.read_text(encoding="utf-8")
    return [r for r in RULE_IDS if f'id = "{r}"' not in text]


def main() -> int:
    if shutil.which("gitleaks") is None:
        print("[x] gitleaks が見つからない (brew install gitleaks)")
        return 2

    if not CONFIG.exists():
        print(f"[x] 設定ファイルが無い: {CONFIG}")
        return 2
    absent = missing_rules()
    if absent:
        print(f"[x] `.gitleaks.toml` に定義が無いルール: {', '.join(absent)}")
        return 2

    cases = [(cid, text) for _, cid, text in SHOULD_DETECT] + SHOULD_ALLOW
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
            found = detections(workdir)
        except ProbeError as e:
            print(f"[x] {e}")
            return 2

    # 検出側は対象ルールで絞り、許可側はルールを問わず見る。非対称にしているのは、
    # 前者が「別ルールに拾われて壊れたルールが隠れる」形を、後者が「対象外のルールが
    # 撃ってコミットが止まる」形をそれぞれ落とすため。
    by_rule = {r: {c for rid, c in found if rid == r} for r in RULE_IDS}
    any_rule = {c for _, c in found}

    missed = [(r, c) for r, c, _ in SHOULD_DETECT if c not in by_rule[r]]
    false_positives = [c for c, _ in SHOULD_ALLOW if c in any_rule]

    print(
        f"検査したケース: {len(cases)} 件 "
        f"(検出されるべき {len(SHOULD_DETECT)} / 許可されるべき {len(SHOULD_ALLOW)}) "
        f"/ ルール {len(RULE_IDS)} 本"
    )
    for rule_id, case_id in missed:
        print(f"  [x] 取りこぼし: {case_id} が {rule_id} で検出されなかった")
    for case_id in false_positives:
        print(f"  [x] 誤検出: {case_id} を検出した")

    if missed or false_positives:
        print(f"[x] 不一致 {len(missed) + len(false_positives)} 件")
        return 1
    print("[+] 不一致なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
