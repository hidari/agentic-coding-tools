#!/usr/bin/env bash
# =============================================================================
# CI の runner へ gitleaks を入れる。版と sha256 の canonical はこのファイル。
#
# GitHub Actions の step から呼ぶ。$RUNNER_TEMP へ展開し、$GITHUB_PATH へ追記して
# 後続の step の PATH へ載せる。ダウンロードと sha256 の照合は download-and-verify.sh に
# 任せ、ここは版・期待値・展開・PATH への追記だけを持つ。
#
# 使い方: install-gitleaks.sh (引数なし。RUNNER_TEMP と GITHUB_PATH を環境から読む)
# =============================================================================
set -euo pipefail

gitleaks_version="8.30.1" # 開発機と揃える
# sha256 は展開前に内容を pin する堰。
# 再計算方法: 下で download-and-verify.sh へ渡している URL から取得したファイルに sha256sum を実行する。
expected_sha256="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"

# 未設定のまま進むと展開先が / 直下になり、PATH への追記も黙って消える。
: "${RUNNER_TEMP:?RUNNER_TEMP が未設定}"
: "${GITHUB_PATH:?GITHUB_PATH が未設定}"

# 隣の download-and-verify.sh はこのファイルの位置から引く。$GITHUB_WORKSPACE を基点に
# すると、Actions の外 (ローカルでの確認) では解決できない。
script_dir="$(dirname -- "${BASH_SOURCE[0]}")"
archive="$RUNNER_TEMP/gitleaks.tar.gz"
"$script_dir/download-and-verify.sh" \
    "https://github.com/gitleaks/gitleaks/releases/download/v${gitleaks_version}/gitleaks_${gitleaks_version}_linux_x64.tar.gz" \
    "$expected_sha256" \
    "$archive"
tar -xz -C "$RUNNER_TEMP" -f "$archive" gitleaks
echo "$RUNNER_TEMP" >> "$GITHUB_PATH"
