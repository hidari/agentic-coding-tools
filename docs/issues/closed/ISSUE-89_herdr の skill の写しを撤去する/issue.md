---
status: closed
---

# chore: herdr の skill の写しを撤去する

## 背景

`skills/tooling/herdr` は herdr 上流が配る skill の古い写しで、上流と離れている。

- herdr 0.9.5 の `herdr --skill` の出力と本文が大きく違い、発火条件 (description) も上流では「ユーザーが herdr に明示的に触れたときだけ」に変わっている
- 写しには 0.9.3 にも 0.9.5 にも無い CLI が残っている (トップレベルの `herdr wait agent-status`、`1-1` 形式の pane id)。`herdr wait agent-status --help` はトップレベルのヘルプに落ちて rc=0 を返すので、誤りがエラーとして表に出ない
- herdr の `--help` 自体が、skill が文脈に無ければ `herdr --skill` を実行するよう案内する。写しを持たなくても情報源はある

写しを追従させ続けるより、vendor をやめて上流を唯一の情報源にする。案内の 1 行を CLAUDE.md などへ足すことはしない。

## タスク

- [x] `skills/tooling/herdr` を削除する
- [x] README を `scripts/gen-readme.py` で再生成する

## 関連

- ISSUE-64: 移す先の表に `herdr` の description が載っていた
- ISSUE-69: この記録は feature の PR で起票したので、ISSUE-69 の裁定 (起票は起票だけの PR) では同梱しない側に入る。例外にするかは ISSUE-69 で決める
- dotfiles 側は消費側の pin と展開物を自分で片付ける
