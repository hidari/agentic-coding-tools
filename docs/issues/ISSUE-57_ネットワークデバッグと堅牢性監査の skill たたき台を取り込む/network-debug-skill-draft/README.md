# network-debug-mac skill たたき台

Claude Code 対応の `network-debug-mac` skill の構成と内容の下書き。
方向性の確定が目的で、そのまま完成品として使うことは想定していない。

## 中身

- `DECISIONS.md`: なぜこの構成にしたか。3 つの軸と実測結果
- `skills/tooling/network-debug-mac/`: `agentic-coding-tools` にそのまま貼れる skill 本体
- `snippets/plugin-promotion.md`: 後から plugin へ昇格するときの差分
- `snippets/script-extraction.md`: コマンドをスクリプトへ切り出す条件と preflight のたたき台

## 使い方

```bash
cp -R skills/tooling/network-debug-mac <agentic-coding-tools>/skills/tooling/
cd <agentic-coding-tools> && python3 scripts/check-package-shape.py
```

## 残っている TODO

本文に `TODO:` で埋め込んである。いずれも「開発機の状態を確認しないと書けない」か
「一度実際にデバッグしてみないと精度が出ない」もの。

- `references/http.md` の証明書の信頼させ方を、実際に使う言語ランタイムで検証する
- `references/http.md` の Firefox の CA 手当て (enterprise_roots か手動インポート) を
  1 度通して、どちらを標準にするか決める
- `references/packet.md` の `-Q` メタデータフィルタを sudo が使える環境で 1 度通す
  (sudo なしで黙殺されることは実測済み)

## 見直しのトリガー

期日ではなく**観測できる合図**で見直す。合図を踏んだら対応する snippet を読む。

- 同じ検査を別の場所へ 3 回書いた、または前提の確認が 20 行を超えた
  → `snippets/script-extraction.md`
- component を独立して呼びたくなった (agents / commands / hooks / MCP が必要になった)
  → `snippets/plugin-promotion.md`
- `TODO:` が実測で埋まった
  → 該当箇所を実測ベースの記述へ差し替え、TODO を消す
- レイヤー選択の判断テーブルが伸び始めた
  → 分割の軸がずれた兆候。`DECISIONS.md`「内容の割り方」を読み直す

これらは `dev-workflow:retrospective-codify` の対象として扱う。
新しい儀式を作らず、既存の振り返りの流れに乗せる。
