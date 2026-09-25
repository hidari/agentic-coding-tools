---
name: jev-lint-curated
description: pin した版の jev-lint を、このリポジトリ向けに厳選した rule だけで check / review / compat 実行したいときに使う。rule の新規作成や cutoff の較正、一般の jev-lint 運用は上流の skill jev-lint を使うこと。
---

# jev-lint 厳選ラッパ

pin した版の jev-lint を、このリポジトリ向けに厳選した rule の一覧だけで動かす薄いラッパ。
版と厳選した rule の一覧は `scripts/jevlint.py` が唯一の場所として持ち、check・review・compat の 3 つのサブコマンドを提供する。
