---
status: open
---

# fix: Phase 1 の recipe 実行と submit 禁止の制約が衝突する

## 背景

`agents/monkey-explorer-agent.md` の Phase 1 は、ログイン手順をこう命じている。

> Otherwise execute the free-text `AUTH_RECIPE` verbatim using agent-browser primitives.

一方 Safety constraints 節は冒頭で優先順位を宣言したうえで submit を制限する。

> These apply to every action. The profile and the dispatcher cannot loosen them.

> Never submit a form whose accessible name is not in `SUBMIT_ALLOWLIST` (fill is allowed, submit is not).

ログインは submit を伴う。そしてログインフォームの accessible name が
`safety.submit_allowlist` に載っている profile は想定されていない。template の例は
`[検索]` であり、ログインフォームを allowlist へ入れる運用はどこにも説明が無い。

つまり `auth: seed_login` の section では、Phase 1 の指示と immutable な制約が
**両立しない**。しかも制約側は「dispatcher は緩められない」と書いてあるので、文面どおりなら
制約が勝ってログインが成立しない。

## 実測 (2026-08-28)

ISSUE-31 の検証で隔離コピーへ通した probe のうち、独立した 2 本がこの衝突を報告した。
どちらもこちらから指摘したものではなく、指示を読んだ結果として自分で見つけている。

1 本目は衝突を section 中断の根拠の 1 つとして挙げた。

> 両立不能で、かつ「dispatcher は緩められない」と書かれている側が勝ちます。

2 本目は解釈が割れる点そのものを問題として挙げた。

> login の submit を制約の対象外とする意図なら、その例外を明記しないと、実行者ごとに
> 解釈が割れます。

## 何が問題か

衝突そのものより、**どちらに倒れるかが読み手に委ねられている**ことが問題である。

- 制約を優先 → `seed_login` の section が常に中断する。安全側だが機能しない
- Phase 1 を優先 → immutable と宣言した制約が実際には immutable でなくなる

どちらの解釈でもエラーにはならない。前者は「ログインできなかった」という正常な中断として
記録され、後者は正常な探索として完走する。**どちらも赤くならないので、解釈が割れていること
自体に気づく経路が無い。**

production では ISSUE-31 で入れた gate が `seed_login` の section を拒否するので衝突は
表面化しない。残るのは local と staging である。

## タスク

- [ ] どちらを正とするか裁定する。ログインの submit を制約の対象外とするか、
      `submit_allowlist` へログインフォームを含めることを profile へ要求するか
- [ ] 採った側を Phase 1 と Safety constraints の両方から読めるようにする。片方だけに書くと、
      もう片方を読んだ実行者が逆の結論を出す
- [ ] template と schema が新しい要求を満たすか確認する。allowlist へログインフォームを含める
      解を採るなら、template の例と説明も変わる

## 関連

- ISSUE-31: この衝突を probe が見つけた検証。production 側は gate が塞ぐが local と staging
  には残る
- ISSUE-34: production ガードが profile の自己申告だけに依存する。`submit_allowlist` を含む
  profile の入力契約を扱う
