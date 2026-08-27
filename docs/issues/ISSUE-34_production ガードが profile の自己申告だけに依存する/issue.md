---
status: open
---

# fix: production ガードが profile の自己申告だけに依存する

## 背景

`security-blue-red-team` と `web-monkey-qa` はどちらも、消費側が書いた
`<project>/.claude/*-profile.yml` の `environment.kind` で production を拒否する。
この判定の入力は自己申告 1 個だけで、申告と実体を突き合わせる経路がない。

**2 つの plugin で防御の厚みは大きく違う。**同列に扱うと手当てを誤るので分けて書く。

## 実測 (2026-08-27)

### 共通: 検査経路が 0 件

profile を指す語を `scripts/` と `.github/` と `.pre-commit-config.yaml` で引いて 0 件。
対照として同じ範囲で `issue-id.py` は 24 件ヒットするので、grep 自体は生きている。

profile の schema 適合を検証する経路も配布側に無い。agent の手順は
「jsonschema が使えるなら検証、無ければ目視」と書いており、
実測したマシンでは `jsonschema` も `yaml` も入っていなかった。
つまり既定経路は常に目視側になる。

#### 追記 (2026-08-28): parse の実行可能性を probe 4 本で再確認した

ISSUE-31 の検証で隔離コピーへ通した probe 4 本が、いずれも独立に `python3` の PyYAML 不在を
踏んだ。4 本とも自力で別の手段を調達しており、内訳は Ruby の Psych が 1 本、
`uv run --with pyyaml` が 3 本 (オプションの組み合わせは 3 本とも異なる)。

つまり「構造化 parse せよ」という要求だけなら実行者が満たせる。壊れているのは要求ではなく、
特定の実装を名指しした例示の方である。

monkey 側の `commands/monkey-qa.md` には PR #30 で調達手段を書き足し、agent 側は具体的な
コマンドを持たず要求だけを述べる形へ変えた (canonical を dispatcher 側の 1 箇所に保つため)。
`commands/security-redteam.md` は `import sys, yaml` を含む Python 断片を手順として直接
埋め込んでおり、こちらは未対応のまま残っている。上の「既定経路は常に目視側になる」は
security 側では今も成立する。

### security-blue-red-team: 防御は厚い。残る穴は 1 点

`agents/red-team-agent.md` は対象の照合をかなり厚く書いている。

- 全 HTTP リクエストで scheme と host が allowlist のいずれかと完全一致すること
- 前方一致による照合の明示的禁止 (allowlist の値を接頭辞に持つ別ホストを通さないため)
- リダイレクトの自動追跡禁止。allowlist の host を離れる 3xx は finding 扱い
- 名前解決後の宛先 IP が loopback / RFC1918 / link-local / メタデータ IP なら拒否
- 検証済み IP を固定して名前解決の TOCTOU を塞ぐ

残る穴は「staging と申告しながら allowlist に本番ホストを並べた profile」だけ。
このとき Layer 3 の能動テストが本番へ完走し、レポートも findings も正常に出る。

### web-monkey-qa: 対象の allowlist が存在しない

schema の `environment` の必須キーは `kind` と `base_url_env` の 2 つだけで、
allowlist に相当するものが無い (該当語の出現は command と agent とも 0 件)。

対象 URL は `base_url_env` が指す環境変数を実行時に解決した値になる。
つまり **gate が見る値と、実際に叩く URL が構造的に切り離されている**。
local と申告したまま env を本番 origin に向ければ、読み取り専用にもならず
入力と送信を含む探索が本番へ走る。dispatcher も explorer もこの不一致を検出しない。

なお production と正しく申告した場合は読み取り専用へ縮退する設計になっており、
申告が正しい限りは安全側に倒れる。

## 何が問題か

判定が自己申告に依存すること自体は避けられない。実体を知る手段が無いため。
問題は**申告と実体のあいだに突き合わせる材料が 1 つも無い**ことで、
monkey 側は材料そのものが schema に存在しない。

## タスク

- [ ] web-monkey-qa の profile schema へ対象の allowlist を導入し、
      解決した URL がそれに一致することを dispatch 前に確かめる形にする
- [ ] security 側の申告と allowlist の整合を見る材料を検討する
- [ ] profile の schema 適合検証を配布側の責任範囲に置けるか検討する。
      現状は消費側が自作するしかなく、作った消費側でも自動経路へ配線されていない
- [ ] schema のテストを走らせる経路を作る。テストは 2 本存在するが、
      pre-commit にも CI にも実行系の言及が 0 件で一度も走っていない
- [ ] `commands/security-redteam.md` が埋め込む parse 断片を、実行環境に PyYAML が無くても
      成立する形にする。monkey 側は PR #30 で対応済み

## 関連

- ISSUE-31: monkey-explorer-agent が production ガードの写しを持たない。
  本 Issue は写しの問題ではなく判定の入力そのものを扱う
- ISSUE-40: Phase 1 の recipe 実行と submit 禁止の制約が衝突する。`submit_allowlist` の
  入力契約が変われば向こうの裁定にも影響する
- ISSUE-13: 両取り付けの同時撤去を機構で検出できない
- ISSUE-36: skill と plugin が消費側へ要求する取り付けを棚卸しする。本 Issue の出所
- dotfiles の ISSUE-46: 両リポジトリの Issue をマイルストーンへ整理し着手順を決める
