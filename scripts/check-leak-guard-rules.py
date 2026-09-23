#!/usr/bin/env python3
"""漏洩ガードの層 1 の config 2 本を、検出側と許可側の両方の対照で検証する。

対象は CUSTOM_RULES (custom ルールだけの config) と DEFAULT_RULES (gitleaks の既定ルール
だけの config)。custom ルールが「捕まえるべきものを捕まえ、通すべきものを通す」ことと、
どちらの config も通すべきものを撃たないことを確かめる。regex は読むだけでは正しさを
判定できず、取りこぼしと誤検出の両方を実際に埋め込んだ実績がある。どちらも例外ではなく
静かな取りこぼし / もっともらしい検出として出るので、検出側だけ、あるいは許可側だけの
対照では気づけない。両方を並べて初めて判定できる。

ケースは 1 つの一時ディレクトリへ書き出し、config ごとに gitleaks を 1 回ずつ呼ぶ。
検出ケースは custom の config の検出で判定し、許可ケースはどちらの config のどのルールにも
掛からないことで判定する。既定の config については、ルールを持たないことと許可ケースを
撃たないことだけを見る。判定は exit code ではなくレポートの内容で行う (gitleaks の
exit code は「検出があったか」であって「期待どおりか」ではない)。

対照が痩せたことは対照自身には見えないので、pin を 2 段構えにしてある。

1. ルール集合の一致。custom の config の [[rules]] id、SHOULD_DETECT が名指しするルール、
   入口 (ENTRY) の custom_canary() のキーの 3 集合が一致することを要求する。片方向だけだと、
   config へルールを足して検出ケースを書かない形が「ルール N 本を検査した」と名乗ったまま
   緑で通る。逆向きのずれ (config に無いルールを名指すケース) は、rename が「全ケース
   取りこぼし」に化けて regex が壊れた形と同じ赤になる。どちらも検査不能として分ける。
   入口の canary を加えるのは、入口が実行時に config を読まず、ルールごとの canary が
   検出されることでそのルールが生きていると判定するため。config にあって canary に無い
   ルールは、入口では欠けても止まらない
2. ケース ID 集合の manifest。pin するのは件数ではなく ID 集合で、消えた側 (痩せ) も
   未記録側 (増加) も名指しで赤にする。件数の下限は canonical の再掲になって drift し、
   下限を割らない痩せを原理的に捕捉できない。同型の先例は scripts/run-python-tests.py

終了コードは 0 (期待どおり) / 1 (不一致あり・manifest とのずれ) / 2 (検査できなかった)。
config や manifest を読めないときと、想定外の例外で止まったときも 2 に入る。
2 を 1 と分けるのは、ルールの誤りと「gitleaks を走らせられなかった」を混ぜないため。
後者は検査の結果ではなく、同じ赤にすると config が壊れている状態がルールの誤りに見える。

出力に絶対パスを出さない (redact_paths)。CI のログは PUBLIC で、リポジトリの置き場所は
開発機ではユーザー名を含む。想定外の例外は traceback も文言もパスを持ちうるので、型名だけを
出して 2 で終える。
"""

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 層 1 の canonical は配布物の側にある。このリポジトリの pre-commit と CI も同じ 2 本を呼ぶ。
RULES_DIR = ROOT / "plugins" / "dev-workflow" / "skills" / "commit-and-pr-message" / "scripts"
CUSTOM_RULES = RULES_DIR / "leak-guard.gitleaks.toml"
DEFAULT_RULES = RULES_DIR / "leak-guard-default.gitleaks.toml"
# 入口。custom ルールごとの canary の行を持ち、そのキー集合をここで借りる (docstring の 1)
ENTRY = RULES_DIR / "check-outgoing-text.py"
MANIFEST = ROOT / "scripts" / "leak-guard-cases-manifest.txt"
UPDATE_CMD = "python3 scripts/check-leak-guard-rules.py --update-manifest"

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

# 帰属行が持つ Anthropic の noreply アドレス。許可側の値なので literal で書く。
# 検出側の 2 形 (前置・後置) はこれを連結して作り、ソース上に連続した形を置かない。
ANTHROPIC_NOREPLY = "noreply@anthropic.com"
# 許可の ^ と $ を pin する 2 形。どちらかのアンカーを落とすと、noreply アドレスを
# 部分に持つだけの別アドレスが許可され、静かに素通りする (fail-open 側)。後置側の
# ドメインは予約ドメインの免除に掛からない形にしてある。掛かると、アンカーの有無に
# かかわらず予約ドメインの免除で許可され、検出側のケースとして成り立たない。
MAIL_ANTHROPIC_NOREPLY_PREFIXED = "x" + ANTHROPIC_NOREPLY
MAIL_ANTHROPIC_NOREPLY_SUFFIXED = ANTHROPIC_NOREPLY + ".evil" + ".net"
# GitHub の noreply 形は login を含み個人を指すので許可しない。ID の先頭を 0 にするのは、
# 実在するアカウントの ID と login の組を作らないため。GitHub の ID は 0 で始まらないので、
# login が何であってもこの組は実在しない。
MAIL_GITHUB_NOREPLY = "0" + "1234567" + "+" + NAME + "_" + "dev" + "@users.noreply" + ".github.com"
# gitleaks の既定 config の全体除外 (?i)^true|false|null$ に掛かる形。この除外は同じ config の
# custom ルールの検出にも効くので (gitleaks 8.30.1 で実測)、custom の config に
# `useDefault` が戻るとこれらを取りこぼす。
MAIL_TRUE_PREFIXED = "true" + NAME + "@" + "mail.internal" + ".corp"
MAIL_FALSE_IN_LOCAL = NAME + ".false" + "@" + "mail.internal" + ".corp"

# custom の config が押さえると宣言している形に、実際に掛かる変形を足したもの。
# 掛かる形の canonical はこのリストで、config 側のコメントは意図の説明を持つ。
#
# 各ケースは「どのルールが捕まえるべきか」を持つ。判定でどう使うかは main() の
# by_rule / hits のコメントが持つ (canonical はそちら)。
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
    # 既定 config の全体除外に掛かる名前 (上の MAIL_TRUE_PREFIXED のコメントを参照)。
    # パスは / で始まるので ^true には掛からず、false と null$ の 2 形だけが成り立つ。
    ("user-path", "false-in-name", f"/Users/{NAME + 'false'}"),
    ("user-path", "null-suffixed-name", f"/Users/{NAME + 'null'}"),
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
    ("email-address", "mail-anthropic-noreply-prefixed", MAIL_ANTHROPIC_NOREPLY_PREFIXED),
    ("email-address", "mail-anthropic-noreply-suffixed", MAIL_ANTHROPIC_NOREPLY_SUFFIXED),
    ("email-address", "mail-github-noreply", MAIL_GITHUB_NOREPLY),
    ("email-address", "mail-true-prefixed-local", MAIL_TRUE_PREFIXED),
    ("email-address", "mail-false-in-local", MAIL_FALSE_IN_LOCAL),
]

# allowlist が持つ名前は 1 つずつ全部並べる。覆っていない名前は allowlist から消しても
# 緑のままで、免除が縮んだことに気づけない。こちらは検出されない前提なので literal で
# 書いてよく、書けること自体が確認になる。
#
# 許可側は「どちらの config のどのルールにも掛からないこと」で判定する。利用者から見れば
# どのルールが撃ってもコミットは止まるので、対象ルールだけを見ると別ルールの誤検出を
# 素通りさせる。既定の config も同じ入力を走査するので、そちらの誤検出も同じく止まる。
SHOULD_ALLOW = [
    ("placeholder-example", "/Users/example/project"),
    ("placeholder-user", "/Users/user/dev"),
    ("placeholder-username", "/Users/username/dev"),
    ("placeholder-angle-macos", "/Users/<name>/dev"),
    ("placeholder-angle-windows", r"C:\Users\<name>\AppData"),
    ("ci-runner", "/Users/runner/work/repo"),
    ("shared", "/Users/shared/data"),
    # このリポジトリでは winvm が一時 .ps1 の置き場に Public を使っているので、許可しないと
    # winvm のソースとテストが全部引っかかる
    ("windows-public", r"C:\Users\Public\Documents"),
    ("windows-public-slash", "C:/Users/Public"),
    ("windows-public-trailing-period", r"C:\Users\Public."),
    ("windows-default", r"C:\Users\Default\NTUSER.DAT"),
    ("windows-default-app-pool", r"C:\Users\DefaultAppPool\AppData"),
    ("windows-all-users", r"C:\Users\All Users"),
    ("rest-api-users", "/api/users/123"),
    # 追跡下の test_winvm.py / test_macvm.py が使っている合成 fixture。免除しないと
    # 既存ツリーが赤くなる。パスで免除しない理由の一般形は custom の config が持つ。
    # このリポジトリでの実例は、ISSUE-15 のスイープで test_winvm.py の fixture から開発機に
    # 実在する VM の UUID が出たこと。テストファイルを免除すると、見つけた穴を作り直す。
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
    # ベンダーの固定値で個人を指さない。既定の帰属行がこれを持つので、許可しないと
    # 帰属行を持つ本文で検査が毎回止まる
    ("mail-anthropic-noreply", ANTHROPIC_NOREPLY),
]


# 表示名と config の組。全ケースをどちらにも通す。検出側の判定は custom の config の
# 検出だけを使う (main() の by_rule のコメントを参照)。
CUSTOM_LABEL = "custom の config"
DEFAULT_LABEL = "既定の config"
CONFIGS = ((CUSTOM_LABEL, CUSTOM_RULES), (DEFAULT_LABEL, DEFAULT_RULES))


class ProbeError(RuntimeError):
    """gitleaks を走らせられなかった。検出 0 件と区別するために送出する。"""


def redact_paths(text: str, *dirs: Path) -> str:
    """text に含まれるリポジトリと一時ディレクトリの絶対パスをプレースホルダへ置き換える。

    パスを印字しうる経路 (gitleaks の stderr、設定ファイルや manifest が無いときの文言、
    読めないときの例外の文字列) は全てここを通す。gitleaks は config を読めないとき、
    読もうとしたファイルのパスを stderr にそのまま出す (gitleaks 8.30.1 で実測)。

    一時ディレクトリは渡された形と実体の両方を置き換える。実測では渡した形のまま出たが、
    macOS の一時領域は /var が /private/var への symlink で、実体の形で出る経路が無いことまでは
    確かめていない。長いものから置き換えるのは、一時ディレクトリがリポジトリの下にある環境で
    先にリポジトリを置き換えると、一時ディレクトリの残りが崩れた形で残るため。
    """
    placeholders = {str(ROOT): "<repo>"}
    for d in dirs:
        placeholders[str(d)] = "<tmp>"
        placeholders[str(d.resolve())] = "<tmp>"
    for raw in sorted(placeholders, key=len, reverse=True):
        text = text.replace(raw, placeholders[raw])
    return text


def detections(workdir: Path, config: Path, label: str, report: Path) -> list[tuple[str, str]]:
    """config で全ケースを走査し、(ルール ID, ケース ID) の組を返す。

    1 ケースが複数ルールに掛かる形も落とさない。
    """
    proc = subprocess.run(
        [
            "gitleaks",
            "dir",
            str(workdir / "cases"),
            "-c",
            str(config),
            # 免除ファイルの探索先をケースの側へ固定する。既定は cwd なので、
            # 指定しないとリポジトリの `.gitleaksignore` がこの検査にも効く (実測)。
            # 危険な向きは片側だけで、許可ケースが免除されると allowlist が壊れて
            # 誤検出している状態が緑で通る。ケース側には免除ファイルを置かない。
            "--gitleaks-ignore-path",
            str(workdir / "cases"),
            # 配備の呼び出し (pre-commit と CI) と揃える。行に gitleaks:allow の印を持つと、
            # 付けない呼び出しではその行の検出が消える (実測)。揃えないと、この検査と
            # 配備で同じ入力の判定が食い違う。
            "--ignore-gitleaks-allow",
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
        raise ProbeError(redact_paths(f"gitleaks の実行に失敗した ({label}): {detail}", workdir))
    if not report.exists():
        raise ProbeError(f"gitleaks がレポートを出力しなかった ({label})")
    try:
        findings = json.loads(report.read_text(encoding="utf-8") or "[]")
        # RuleID を捨てずに返す。捨てて集合にすると、main() が置いている判定の非対称
        # (そちらのコメントが canonical) を作れなくなる。
        return [(f.get("RuleID", ""), Path(f["File"]).stem) for f in findings]
    except (OSError, ValueError, KeyError, TypeError) as e:
        # 未捕捉の例外は traceback にこのスクリプトの絶対パスを載せるので、ここで受ける
        raise ProbeError(
            redact_paths(f"gitleaks のレポートを読めない ({label}): {e}", workdir)
        ) from e


def load_config(path: Path) -> dict:
    """config を読む。

    文字列の部分一致ではなく tomllib で読む。部分一致は TOML として等価な別表記
    (`id="x"` / 単引用符 / 空白の数) を「定義が無い」と誤報し、逆にコメントへ残った
    旧 id を「定義あり」と誤報する (どちらも実測)。誤報の向きが両方あるので、
    rename を検査不能として分ける、という check_rule_sets の目的をどちらも壊す。
    tomllib は 3.11+ の標準ライブラリなので pin すべき依存は増えない。
    """
    with path.open("rb") as fh:
        return tomllib.load(fh)


def rule_ids(config: dict) -> set[str]:
    """config が定義するルールの id 集合。"""
    return {r["id"] for r in config.get("rules", []) if "id" in r}


def canary_rule_ids() -> set[str]:
    """入口の custom_canary() のキー集合。

    ハイフン名のスクリプトは import 文では読めないため importlib で読む。読み込みは
    モジュールの定義を実行するだけで、入口の main は呼ばない。
    """
    spec = importlib.util.spec_from_file_location("check_outgoing_text", ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.custom_canary())


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
    """custom の config の id・SHOULD_DETECT のルール・入口の canary のキーの一致と、既定の
    config がルールを持たないことを要求する。

    一致しない形はどれも「ルール N 本を検査した」と名乗ったまま緑で通るか、rename が
    全ケース取りこぼしに化けるので、検査の結果 (1) ではなく検査不能 (2) として止める。
    入口を読めないときも 2 にする。読めない入口は配布先でも動かず、canary の集合が取れない
    ままここを緑にすると、config と canary のずれが見えなくなる。

    既定の config へ足したルールは、その config が持つ全体除外の下で動く (config を 2 本に
    分けた理由そのもの)。しかも検出側の判定は custom の config しか見ないので、そこへ足した
    ルールはどの検出ケースにも覆われない。
    """
    loaded = {}
    for label, path in CONFIGS:
        try:
            loaded[label] = load_config(path)
        # tomllib は UTF-8 でない入力を TOMLDecodeError ではなく UnicodeDecodeError で返す (実測)
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
            print(redact_paths(f"[x] {label} を読めない: {e}"))
            return 2
    try:
        canaried = canary_rule_ids()
    except Exception as e:  # noqa: BLE001
        # exec_module は構文エラーから import の失敗まで何でも上げる。文言はパスを持ちうる
        # (SyntaxError はファイル名を含む) ので redact_paths を通す
        print(redact_paths(f"[x] 入口 (canary の借り先) を読めない: {type(e).__name__}: {e}"))
        return 2

    rc = 0
    stray = loaded[DEFAULT_LABEL].get("rules")
    if stray:
        ids = sorted(rule_ids(loaded[DEFAULT_LABEL]))
        print(f"[x] {DEFAULT_LABEL} がルールを {len(stray)} 本持つ: {ids}")
        rc = 2

    # 3 集合を総当たりで比べる。どの 2 つのずれも名指しで出す
    sets = (
        (f"{CUSTOM_LABEL}", rule_ids(loaded[CUSTOM_LABEL])),
        ("SHOULD_DETECT", {rule for rule, _, _ in SHOULD_DETECT}),
        ("入口の custom_canary()", canaried),
    )
    for (label_a, set_a), (label_b, set_b) in ((sets[0], sets[1]), (sets[0], sets[2]), (sets[1], sets[2])):
        for rule in sorted(set_a - set_b):
            print(f"[x] {label_a} にあって {label_b} に無いルール: {rule}")
        for rule in sorted(set_b - set_a):
            print(f"[x] {label_b} にあって {label_a} に無いルール: {rule}")
        if set_a != set_b:
            rc = 2
    return rc


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

    for label, path in (*CONFIGS, ("入口", ENTRY)):
        if not path.exists():
            print(redact_paths(f"[x] {label} が無い: {path}"))
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
            found = {
                label: detections(workdir, path, label, workdir / f"report-{i}.json")
                for i, (label, path) in enumerate(CONFIGS)
            }
        except ProbeError as e:
            print(f"[x] {e}")
            return 2

    # 検出側は custom の config の対象ルールで絞り、許可側は両方の config のルールを
    # 問わず見る。非対称にしているのは、前者が「別ルールに拾われて壊れたルールが隠れる」
    # 形を、後者が「対象外のルールが撃ってコミットが止まる」形をそれぞれ落とすため。
    # ルールの並びは SHOULD_DETECT の出現順 (check_rule_sets で config と一致を確かめ済み)。
    rules = list(dict.fromkeys(rule for rule, _, _ in SHOULD_DETECT))
    by_rule = {r: {c for rid, c in found[CUSTOM_LABEL] if rid == r} for r in rules}
    hits = {}
    for label, pairs in found.items():
        for rid, c in pairs:
            hits.setdefault(c, set()).add(f"{label} の {rid}")

    missed = [(r, c) for r, c, _ in SHOULD_DETECT if c not in by_rule[r]]
    false_positives = [c for c, _ in SHOULD_ALLOW if c in hits]

    # 件数ではなくルールごとの内訳を出す。合計だけだと、あるルールの対照が痩せても
    # 「N 件を検査した」の N が減るだけで、どこが痩せたかが読めない。
    breakdown = " / ".join(
        f"{r} {sum(1 for rule, _, _ in SHOULD_DETECT if rule == r)}" for r in rules
    )
    print(
        f"検査したケース: {len(cases)} 件 "
        f"(検出されるべき {len(SHOULD_DETECT)} [{breakdown}] / "
        f"許可されるべき {len(SHOULD_ALLOW)})"
    )
    for rule_id, case_id in missed:
        print(f"  [x] 取りこぼし: {case_id} が {rule_id} で検出されなかった")
    for case_id in false_positives:
        # どちらの config のどのルールが撃ったかを出す。既定の config の誤検出は
        # custom の allowlist を直しても消えないので、直す場所を取り違えないため
        print(f"  [x] 誤検出: {case_id} を検出した ({', '.join(sorted(hits[case_id]))})")

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

    try:
        recorded = read_manifest()
    except (OSError, UnicodeDecodeError) as e:
        # manifest がディレクトリのときの例外の文字列は、読もうとした絶対パスを持つ (実測)
        print(redact_paths(f"[x] manifest を読めない: {e}"))
        return 2
    if recorded is None:
        print(redact_paths(f"[x] manifest が無い: {MANIFEST} ({UPDATE_CMD} で生成する)"))
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
    try:
        sys.exit(main())
    except Exception as e:
        # 想定外の例外の traceback はこのスクリプトの絶対パスを載せ、文言もパスを持ちうる。
        # redact_paths は想定した経路にしか掛かっていないので、ここでは型名だけを出す。
        print(f"[x] 想定外の例外で検査できなかった: {type(e).__name__}")
        sys.exit(2)
