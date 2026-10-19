---
status: open
---

# fix(jev-lint-curated): モデル ID を alias で渡し、測定した版に固定されていない

## 背景

jev-lint-curated は上流の jev-lint の版を `scripts/jevlint.py` の `UPSTREAM_VERSION` で固定しているが、上流へ渡すモデル ID は `scripts/jevlint_host.py` の `fixed_tail` が `jev-latest` の literal で渡している。`jev-latest` は alias と見られる (送った ID と、ISSUE-74 の記録で応答したモデルの名前が異なる)。alias なら、上流が新しいモデルを公開したときに指す版が変わりうる。

厳選した rule の一覧と cutoff は、特定のモデルの版で測った結果に基づく。ISSUE-74 に残した完了条件の実行記録では、応答したモデルは jev-1.13.0 だった。alias のままだと、ラッパの版は固定されているのにモデルだけが入れ替わり、測った前提が黙って崩れる。

起票のきっかけは消費側の棚卸しからの報告。提供元の models ページでは、`jev-latest` が jev-1.13.0 を、`jev-preview` がより新しい版を指している、とされた。このページの対応はこのリポジトリではまだ確かめていない。

`SKILL.md` の「版を上げる手順」はラッパの版しか扱っておらず、モデル ID の更新も、更新後の測り直しも手順に入っていない。

## タスク

- [ ] モデル ID を、提供元の文書に載っている版付きの ID に固定する。ID の文字列は文書から読み、推測で組み立てない
- [ ] `scripts/test_jevlint_host.py` の `fixed_tail` の assert を、固定した ID へ合わせる
- [ ] 「版を上げる手順」に、モデル ID の更新と、更新したあとの測り直しを入れる
- [ ] 固定した ID がキー無しの経路 (`check --dry-run` / `compat`) で壊れないことを確かめる。キーを使う確認はユーザーの `!` に任せる

## 関連

- ISSUE-65
- ISSUE-74
