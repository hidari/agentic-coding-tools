#!/bin/bash
# plugins/ 配下の各パッケージへ claude plugin validate --strict を通す。
#
# plugin.json のフィールド名や必須項目の canonical はこのコマンドが持つ。散文へ再掲すると
# drift するため、検証はここへ委譲する。
#
# claude が無い環境では skip せず失敗させる。skip する検査は「通った」と見分けがつかず、
# 検査があるつもりで無い状態を作るため。
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

if ! command -v claude > /dev/null 2>&1; then
  echo "[x] claude が見つからない。plugin.json の検証を実行できない"
  echo "    インストールするか、この hook を意図的に外すこと (黙って skip はしない)"
  exit 1
fi

fail=0
count=0

for pkg in plugins/*/; do
  [ -d "$pkg" ] || continue
  count=$((count + 1))
  name="$(basename "$pkg")"
  out="$(claude plugin validate --strict "$pkg" 2>&1)"
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "[x] $name: validate --strict が失敗した (exit $rc)"
    echo "$out" | sed 's/^/      /'
    fail=1
  else
    echo "[+] $name: ok"
  fi
done

if [ "$count" -eq 0 ]; then
  echo "[x] plugins/ 配下にパッケージが 1 個も無い。検査対象ゼロは合格ではない"
  exit 1
fi

echo "検証したパッケージ: $count 個"
exit $fail
