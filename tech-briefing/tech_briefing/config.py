"""リポジトリごとの設定の検証。official_sources は web_search / web_fetch の allowed_domains になるので、
ドメインの検証は信頼境界として扱う。"""

import re
from dataclasses import dataclass

from tech_briefing.constants import DENY_HOSTS, DENY_SUFFIXES

REGISTRIES = ("npm", "crates", "pypi")
_REQUIRED = ("criteria", "official_sources", "files")
_OPTIONAL = ("issues_glob", "registries")
# ラベルは英数字とハイフンのみ。国際化ドメインは punycode (xn--) で書かせる
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class FileRule:
    glob: str
    searchable: bool


@dataclass(frozen=True)
class Config:
    criteria: str
    official_sources: tuple[str, ...]
    files: tuple[FileRule, ...]
    issues_glob: str | None
    registries: tuple[str, ...]


def _domain(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"official_sources の項目は空でない文字列にしてください: {value!r}")
    if not value.isascii():
        # 見た目が同じ別文字のドメイン (ホモグリフ) を許可名簿へ入れないため
        raise ConfigError(f"official_sources に ASCII 以外の文字は使えません: {value!r}")
    host = value.lower()
    # Claude docs (2026-11-24 確認): allowed_domains はスキームを含めない。
    # web_fetch はドメインだけで照合するのでパス付きの項目は取得 URL に一致せず、ワイルドカードはパスにしか使えない。
    labels = host.split(".")
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        raise ConfigError(
            f"official_sources はスキーム・パス・ワイルドカード・ポート・空ラベルを含まない"
            f"2ラベル以上のホスト名にしてください: {value!r}"
        )
    # punycode (xn--) は ASCII 以外を再符号化したもので、許すと ASCII 限定の規則 (ホモグリフ対策) の目的を迂回できる
    if any(label.startswith("xn--") for label in labels):
        raise ConfigError(f"official_sources に punycode (xn--) は使えません: {value!r}")
    # IP アドレスはドメインではない。API は不正な形式を要求時に 400 で拒否する (2026-11-24 確認) ので、設定の読み込みで先に落とす
    if all(label.isdigit() for label in labels):
        raise ConfigError(f"official_sources に IP アドレスは指定できません: {value!r}")
    # サブドメインは自動で含まれる (2026-11-24 確認)。許可すると配下すべてが通るので、
    # 拒否ホストそのもの・その親・拒否接尾辞とその配下を拒否する
    if host in DENY_HOSTS or any(d.endswith("." + host) for d in DENY_HOSTS):
        raise ConfigError(f"official_sources に誰でも書き込める場所は指定できません: {value!r}")
    if any(host == s or host.endswith("." + s) for s in DENY_SUFFIXES):
        raise ConfigError(f"official_sources にホスティングサービスの領域は指定できません: {value!r}")
    return host


def _glob(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name} は空でない文字列にしてください: {value!r}")
    if value.startswith("/") or ".." in value.split("/"):
        raise ConfigError(f"{name} はリポジトリの外を指せません (絶対パスと .. は不可): {value!r}")
    return value


def _file_rule(entry: object) -> FileRule:
    if isinstance(entry, str):
        return FileRule(_glob(entry, "files の glob"), False)
    if isinstance(entry, dict):
        unknown = set(entry) - {"glob", "search"}
        if unknown or "glob" not in entry:
            raise ConfigError(f"files の項目は glob (必須) と search だけを持てます: {entry!r}")
        search = entry.get("search", False)
        if not isinstance(search, bool):
            raise ConfigError(f"files の search は真偽値にしてください: {entry!r}")
        return FileRule(_glob(entry["glob"], "files の glob"), search)
    raise ConfigError(f"files の項目は文字列か {{glob, search}} の mapping にしてください: {entry!r}")


def _list(raw: dict, key: str) -> list:
    value = raw[key]
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{key} は空でない配列にしてください")
    return value


def load_config(raw: object) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("設定は mapping にしてください")
    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        raise ConfigError(f"必須の欄がありません: {', '.join(missing)}")
    unknown = sorted(str(k) for k in raw if k not in _REQUIRED + _OPTIONAL)
    if unknown:
        raise ConfigError(f"未知の欄があります: {', '.join(unknown)}")

    criteria = raw["criteria"]
    if not isinstance(criteria, str) or not criteria.strip():
        raise ConfigError("criteria は空でない文字列にしてください")

    sources = tuple(_domain(d) for d in _list(raw, "official_sources"))
    files = tuple(_file_rule(e) for e in _list(raw, "files"))

    issues_glob = raw.get("issues_glob")
    if "issues_glob" in raw:
        issues_glob = _glob(issues_glob, "issues_glob")

    registries: tuple[str, ...] = ()
    if "registries" in raw:
        reg = raw["registries"]
        if not isinstance(reg, list) or any(r not in REGISTRIES for r in reg) or len(set(reg)) != len(reg):
            raise ConfigError(f"registries は重複なしで {', '.join(REGISTRIES)} のいずれかにしてください")
        registries = tuple(reg)

    return Config(criteria, sources, files, issues_glob, registries)
