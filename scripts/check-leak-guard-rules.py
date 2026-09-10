#!/usr/bin/env python3
"""漏洩ガードの custom ルールを、検出側と許可側の両方の対照で検証する。

`.gitleaks.toml` の custom ルールが「捕まえるべきものを捕まえ、通すべきものを通す」
ことを確かめる。対象は RULE_IDS が持つ。regex は読むだけでは正しさを判定できず、
取りこぼしと誤検出の両方を実際に埋め込んだ実績がある (経緯は `.gitleaks.toml` の
コメントが持つ)。どちらも例外ではなく静かな取りこぼし / もっともらしい検出として出るので、
検出側だけ、あるいは許可側だけの対照では気づけない。両方を並べて初めて判定できる。

ケースは 1 つの一時ディレクトリへ書き出し、gitleaks を 1 回だけ呼ぶ。判定は exit code
ではなくレポートの内容で行う (gitleaks の exit code は「検出があったか」であって
「期待どおりか」ではない)。

対照が痩せたことは対照自身には見えないので、pin を 2 段構えにしてある。

1. ルール集合の一致。`.gitleaks.toml` の [[rules]] id・RULE_IDS・SHOULD_DETECT が
   名指しするルールの 3 集合が一致することを要求する。片方向だけだと、toml へルールを
   足して RULE_IDS を触らない形も、RULE_IDS へ足して検出ケースを書かない形も、
   「ルール N 本を検査した」と名乗ったまま緑で通る (どちらも実測で再現した)
2. ケース ID 集合の manifest。pin するのは件数ではなく ID 集合で、消えた側 (痩せ) も
   未記録側 (増加) も名指しで赤にする。件数の下限は canonical の再掲になって drift し、
   下限を割らない痩せを原理的に捕捉できない。同型の先例は scripts/run-python-tests.py

終了コードは 0 (期待どおり) / 1 (不一致あり・manifest とのずれ) / 2 (検査できなかった)。
2 を 1 と分けるのは、ルールの誤りと「gitleaks を走らせられなかった」を混ぜないため。
後者は検査の結果ではなく、同じ赤にすると config が壊れている状態がルールの誤りに見える。
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".gitleaks.toml"
MANIFEST = ROOT / "scripts" / "leak-guard-cases-manifest.txt"
UPDATE_CMD = "python3 scripts/check-leak-guard-rules.py --update-manifest"

# 検証対象のルール。canonical は `.gitleaks.toml` の [[rules]] id で、ここはその参照。
# 3 集合の一致を先に確かめて、名前のずれ (検査不能) をルールの誤り (不一致) と分ける。
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
# TLD の長さ下限 ({2,}) を pin するための 2 文字 TLD。ここが緩むと .jp / .io 宛の
# 実アドレスが静かに素通りする (fail-open 側なので誤検出より重い)。
#
# ドメインを単一ラベルにするのは、多ラベルだと下限を上げても内側のラベルで match が
# 成立して pin にならないため。`mail.internal.jp` のような多ラベルのドメインは {3,} へ
# 狭めても `mail` + `.internal` の側で当たり、対照として空振りする (変異注入で実測)。
MAIL_SHORT_TLD = f"{NAME}@" + "internal" + ".jp"
# 免除の末尾アンカー ($) を pin する 2 形。予約ドメインを末尾ではなく途中に持つ。
# アンカーを落とすと「予約ドメインを含むだけ」で許可され、実在ドメイン宛が静かに
# 素通りする (fail-open 側)。免除が広がる向きの変異は誤検出として現れないので、
# 検出側にこの形を置かないと気づけない。
MAIL_RESERVED_TLD_IN_MIDDLE = f"{NAME}@" + "host.test" + ".example-corp" + ".com"
MAIL_RESERVED_2LD_IN_MIDDLE = f"{NAME}@" + "example.com" + ".evil" + ".net"

# `.gitleaks.toml` が押さえると宣言している形に、実際に掛かる変形を足したもの。
# 掛かる形の canonical はこのリストで、toml 側のコメントは意図の説明を持つ。
#
# 各ケースは「どのルールが捕まえるべきか」を持つ。判定でどう使うかは main() の
# by_rule / any_rule のコメントが持つ (canonical はそちら)。
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
    # 単語境界を使っていたころ素通りしていた 2 形。境界を戻すとここが赤くなる。
    # 接頭辞側は security-blue-red-team plugin が文書化している seed の形。
    ("vm-uuid", "uuid-underscore-prefixed", "seed" + "_" + UUID_LOWER),
    ("vm-uuid", "uuid-hex-suffixed", UUID_LOWER + "9"),
    ("email-address", "mail-plain", MAIL),
    ("email-address", "mail-in-prose", f"連絡先は {MAIL} まで"),
    ("email-address", "mail-two-letter-tld", MAIL_SHORT_TLD),
    ("email-address", "mail-reserved-tld-in-middle", MAIL_RESERVED_TLD_IN_MIDDLE),
    ("email-address", "mail-reserved-2ld-in-middle", MAIL_RESERVED_2LD_IN_MIDDLE),
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
    # 追跡下の test_winvm.py / test_macvm.py が使っている合成 fixture。免除しないと
    # 既存ツリーが赤くなる。パスで免除しない理由は `.gitleaks.toml` が持つ。
    ("uuid-fixture-a", "aaaa1111-bbbb-4ccc-8ddd-eeee22223333"),
    ("uuid-fixture-a-upper", "AAAA1111-BBBB-4CCC-8DDD-EEEE22223333"),
    # 単語境界を外しても免除は縮まないこと。免除は match 全体 (素の UUID) へ ^...$ で
    # 掛かるので、接頭辞が付いても fixture は fixture のまま許可される。
    ("uuid-fixture-a-prefixed", "seed" + "_" + "aaaa1111-bbbb-4ccc-8ddd-eeee22223333"),
    ("uuid-fixture-b", "ffff4444-aaaa-4bbb-8ccc-dddd55556666"),
    ("uuid-fixture-b-upper", "FFFF4444-AAAA-4BBB-8CCC-DDDD55556666"),
    ("uuid-nil", "00000000-0000-0000-0000-000000000000"),
    # RFC 2606 と RFC 6761 の予約。予約 2LD の下位と、予約 TLD そのものを宛先に持つ形の
    # 両方を置く。大小の変種を 1 つ置くのは、免除側の (?i) を pin するため。
    ("mail-example-com", "user@example.com"),
    ("mail-example-org", "user@example.org"),
    ("mail-example-net", "user@example.net"),
    ("mail-example-subdomain", "user@mail.example.com"),
    ("mail-example-tld", "docs@corp.example"),
    ("mail-invalid-tld", "probe@example.invalid"),
    ("mail-invalid-tld-upper", "Probe@Example.INVALID"),
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
            # 免除ファイルの探索先をケースの側へ固定する。既定は cwd なので、
            # 指定しないとリポジトリの `.gitleaksignore` がこの検査にも効く (実測)。
            # 危険な向きは片側だけで、許可ケースが免除されると allowlist が壊れて
            # 誤検出している状態が緑で通る。ケース側には免除ファイルを置かない。
            "--gitleaks-ignore-path",
            str(workdir / "cases"),
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
    # RuleID を捨てずに返す。捨てて集合にすると、main() が置いている判定の非対称
    # (そちらのコメントが canonical) を作れなくなる。
    return [(f.get("RuleID", ""), Path(f["File"]).stem) for f in findings]


def config_rule_ids() -> set[str]:
    """`.gitleaks.toml` が定義する custom ルールの id 集合を返す。

    文字列の部分一致ではなく tomllib で読む。部分一致は TOML として等価な別表記
    (`id="x"` / 単引用符 / 空白の数) を「定義が無い」と誤報し、逆にコメントへ残った
    旧 id を「定義あり」と誤報する (どちらも実測)。誤報の向きが両方あるので、
    rename を検査不能として分ける、というこの関数の目的をどちらも壊す。
    tomllib は 3.11+ の標準ライブラリなので pin すべき依存は増えない。
    """
    with CONFIG.open("rb") as fh:
        data = tomllib.load(fh)
    return {r["id"] for r in data.get("rules", []) if "id" in r}


def case_keys() -> list[str]:
    """manifest へ書く対照ケースの ID 集合。

    検出側はルール ID を伴わせる。ケース ID だけだと、ケースを別のルールへ
    付け替える変更が集合として同一になり、manifest が素通りする。
    """
    keys = [f"detect::{rule}::{case_id}" for rule, case_id, _ in SHOULD_DETECT]
    keys += [f"allow::{case_id}" for case_id, _ in SHOULD_ALLOW]
    return sorted(keys)


def read_manifest() -> set[str] | None:
    """manifest が記録している ID 集合。ファイルが無ければ None。"""
    if not MANIFEST.exists():
        return None
    return {
        line.strip()
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def write_manifest(keys: list[str]) -> None:
    # 先頭に再生成コマンドを書くのは、利用者が文面から原因へ辿り着けるようにするため。
    header = (
        f"# {UPDATE_CMD} が生成する。手で編集しない。\n"
        "# 対照ケースの ID 集合を pin し、消えた側 (痩せ) も未記録側 (増加) も赤にする。\n"
        "# ケースを増減・改名したら上のコマンドで再生成し、diff ごとコミットする。\n"
        "# 衝突したら手でマージせず再生成する。\n"
    )
    MANIFEST.write_text(header + "\n".join(keys) + "\n", encoding="utf-8")


def check_rule_sets() -> int:
    """toml・RULE_IDS・SHOULD_DETECT が名指しするルールの 3 集合の一致を要求する。

    一致しない形はどれも「ルール N 本を検査した」と名乗ったまま緑で通るので、
    検査の結果 (1) ではなく検査不能 (2) として止める。
    """
    try:
        defined = config_rule_ids()
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"[x] `.gitleaks.toml` を読めない: {e}")
        return 2

    declared = set(RULE_IDS)
    covered = {rule for rule, _, _ in SHOULD_DETECT}
    if defined == declared == covered:
        return 0

    for label, other in (("`.gitleaks.toml`", defined), ("SHOULD_DETECT", covered)):
        for rule in sorted(other - declared):
            print(f"[x] {label} にあって RULE_IDS に無いルール: {rule}")
        for rule in sorted(declared - other):
            print(f"[x] RULE_IDS にあって {label} に無いルール: {rule}")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-manifest",
        action="store_true",
        help="対照ケースの ID 集合を manifest へ書き直す (照合はしない)",
    )
    args = parser.parse_args()

    if shutil.which("gitleaks") is None:
        print("[x] gitleaks が見つからない (brew install gitleaks)")
        return 2

    if not CONFIG.exists():
        print(f"[x] 設定ファイルが無い: {CONFIG}")
        return 2
    rc = check_rule_sets()
    if rc != 0:
        return rc

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

    # 件数ではなくルールごとの内訳を出す。合計だけだと、あるルールの対照が痩せても
    # 「N 件を検査した」の N が減るだけで、どこが痩せたかが読めない。
    breakdown = " / ".join(
        f"{r} {sum(1 for rule, _, _ in SHOULD_DETECT if rule == r)}" for r in RULE_IDS
    )
    print(
        f"検査したケース: {len(cases)} 件 "
        f"(検出されるべき {len(SHOULD_DETECT)} [{breakdown}] / "
        f"許可されるべき {len(SHOULD_ALLOW)})"
    )
    for rule_id, case_id in missed:
        print(f"  [x] 取りこぼし: {case_id} が {rule_id} で検出されなかった")
    for case_id in false_positives:
        print(f"  [x] 誤検出: {case_id} を検出した")

    if missed or false_positives:
        print(f"[x] 不一致 {len(missed) + len(false_positives)} 件")
        if args.update_manifest:
            print("[x] 赤の run では manifest を更新しない (壊れた状態を baseline へ焼かない)")
        return 1

    keys = case_keys()
    if args.update_manifest:
        write_manifest(keys)
        print(f"[+] manifest を更新した: {len(keys)} 件")
        return 0

    recorded = read_manifest()
    if recorded is None:
        print(f"[x] manifest が無い: {MANIFEST} ({UPDATE_CMD} で生成する)")
        return 2
    vanished = sorted(recorded - set(keys))
    unrecorded = sorted(set(keys) - recorded)
    for key in vanished:
        print(f"  [x] manifest にあって今回のケースに無い: {key}")
    for key in unrecorded:
        print(f"  [x] 今回のケースにあって manifest に無い: {key}")
    if vanished or unrecorded:
        print(f"[x] manifest とのずれ {len(vanished) + len(unrecorded)} 件 ({UPDATE_CMD})")
        return 1

    print(f"[+] 不一致なし (manifest と一致: {len(keys)} 件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
