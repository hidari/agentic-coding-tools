#!/usr/bin/env bash
# =============================================================================
# CI のツール取得で共通する「HTTPS ダウンロード + 固定 sha256 検証」を1箇所へ集約する。
#
# setup-ast-grep / setup-neovim / gitleaks 導入ステップが独立に書いていた
#   curl (--proto '=https' 等の security flag 群) + sha256sum -c の2行
# を共通化し、検証手順を変えるときの drift を防ぐ。展開方法は3者で異なる
# (unzip / tar 全体 / tar 単一メンバ) ため展開は呼び出し側に残す。
#
# 対象外: setup-bats (commit-SHA 付き URL を tar へ直接パイプ) と bootstrap.sh の
# installer (curl | sh) は「ファイルへ落として sha256 で pin」という契約に合わない
# 別の完全性モデルのため、同じ security flag を持っていてもここには通さない。
#
# 使い方:
#   download-and-verify.sh <url> <sha256> <dest>
#     <url>    取得する HTTPS アーカイブの URL (tag 付け替えで中身が変わりうる mutable な参照)
#     <sha256> 期待する 64 桁 hex の sha256 (展開前に内容を pin する堰)
#     <dest>   保存先パス (この後、呼び出し側が展開する)
#
# テスト容易性のため検証本体を verify_sha256 に切り出し、source 時は main を走らせず
# 関数だけ公開する (末尾の BASH_SOURCE guard)。set -euo pipefail は main の内側に
# 置く。top-level に置くと source したテストシェルへ errexit が漏れるため
# (scripts/tests/download-and-verify.bats)。
# =============================================================================

# ダウンロード済みファイルの sha256 が期待値と一致するか検証する。
# 不一致・ファイル不在なら sha256sum が非0で返る。
verify_sha256() {
    local file="$1"
    local expected="$2"
    echo "${expected}  ${file}" | sha256sum -c -
}

# HTTPS 限定でアーカイブを取得し、固定 sha256 で内容を検証する (エントリポイント)。
main() {
    set -euo pipefail
    if [ "$#" -ne 3 ]; then
        echo "使い方: download-and-verify.sh <url> <sha256> <dest>" >&2
        return 2
    fi
    local url="$1"
    local sha256="$2"
    local dest="$3"
    curl --proto '=https' --tlsv1.2 -fsSL -o "$dest" "$url"
    verify_sha256 "$dest" "$sha256"
}

# 直接実行時のみ main を走らせ、source 時は関数だけ公開する
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    main "$@"
fi
