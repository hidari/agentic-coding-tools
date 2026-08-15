---
status: open
---

# test: 両取り付けの同時撤去を機構で検出できない

## 背景

Issue #8 で `scripts/run-python-tests.py` に取り付け検査を入れた。`.pre-commit-config.yaml` と
`.github/workflows/ci.yml` の非コメント行に自分が現れることを、自分が回すテストが確かめる。
片方を外せばもう片方の経路が赤にする。

ただしこの構造は**両方を同時に外す変更**を検出できない。検査を回す取り付けが消えるので、
検査自身が走らなくなるためである。runner の docstring はこれを限界として明記しており、
「レビューを防衛層とする」という判断で受け入れた。

その判断のときに検討したのは「CI へ第 2 の取り付け (`python3 -m unittest discover -s scripts`
のような runner を介さない経路) を足す」案で、取り付け literal が 2 箇所に増えて二重管理に
なるため採らなかった。

## 提案

マージ前ゲートのレビューで、**literal を増やさずに塞ぐ**別案が出た。採否は未決。

`scripts/check-package-shape.py` は既に pre-commit と CI の両方へ取り付いている独立の
プロセスである。ここへ規則を 1 つ置く。

> `scripts/` 直下の `test_*` を除く全 `*.py` が、`.pre-commit-config.yaml` と
> `.github/workflows/ci.yml` の非コメント行に現れること

canonical はファイルシステムの glob そのものなので、スクリプト名の literal はどこにも
増えない。現状の 4 本 (`check-leak-guard-rules.py` / `check-package-shape.py` /
`gen-readme.py` / `run-python-tests.py`) はいずれも両経路に取り付いており、免除リストは
空のまま成立する (レビュー時の実測)。

`run-python-tests.py` 側の `Attachment` テストも同じ glob 導出へ一般化すれば相互監視になり、
盲点は「2 つの機構の 4 箇所の取り付けを 1 つの diff で同時に消す」まで縮む。

## 検討事項

- `check-package-shape.py` の責務が「パッケージの形と命名規約」から広がる。docstring が
  規約の canonical なので、拡張するなら docstring も直す。責務を混ぜず別スクリプトに
  するという選択もありうる
- 免除が必要になったとき、免除集合が広がりすぎる方向の変異で pin すること
  (CLAUDE.md の「緩めすぎ方向の変異」)
- 残る 2 形 (`__main__` ガードの除去・集計を `return 0` に潰す変更) はこの案でも塞がらない。
  どんな検出器も変異された exit 経路を通って報告する以上、in-band では原理的に塞げない。
  ここは docstring が正しい深さである

## タスク

- [ ] 提案を採るかを決める (`check-package-shape.py` の責務拡張を許すか、別スクリプトにするか、
      現状どおり docstring + レビューで受け入れ続けるか)
- [ ] 採る場合: 規則を実装し、免除集合の広がりすぎ方向まで変異注入で確認する
- [ ] 採る場合: `run-python-tests.py` の `Attachment` テストを glob 導出へ一般化する
- [ ] 採る場合: 限界の記述 (runner の docstring) を新しい範囲へ直す

## 関連

- [Issue #8 (closed): run-python-tests.py の件数ガードが実質 1 件で機能していない](../closed/8_run-python-tests.py%20の件数ガードが実質%201%20件で機能していない/issue.md) —
  この限界を作った変更。限界の canonical は `scripts/run-python-tests.py` の docstring
- [Issue #12: scripts/ の検査スクリプトが自分自身のテストを持たない](../12_scripts%20の検査スクリプトが自分自身のテストを持たない/issue.md) —
  `check-package-shape.py` に触る点が重なる。先に import 安全化が入るなら合わせて考える
