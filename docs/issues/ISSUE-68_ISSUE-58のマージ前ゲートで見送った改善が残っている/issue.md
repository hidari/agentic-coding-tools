---
status: open
---

# refactor: ISSUE-58のマージ前ゲートで見送った改善が残っている

## 背景

ISSUE-58の PR を作る前の品質ゲート (simplify の4観点、Boy Scout Sweep、コードレビュー、PUBLIC 漏洩スイープ) で出た指摘のうち、挙動や設計を変えるものと、その PR で触っていないファイルのものを、マージ直前のブランチでは直さずにここへ残した。コードレビューと漏洩スイープの指摘は0件で、ここに残したものに正しさの問題は無い。

どの指摘も、ゲートのレビュアーが HEAD の版を読んで出したもので、数値はレビュアーが測った値である。着手するときは現物で測り直す。

## 残したもの

1. 入口の例外クラスの統合 (`check-outgoing-text.py`): `InputError` と `Layer1Error` は理由を持つだけの同じ実装で、`ReportError` と `CanaryMissing` は `scan_stdin` の中で `Layer1Error` へ付け替えられるだけである。付け替えのときに渡していたメッセージは捨てられ、出力に届かない。理由を持つ例外1つにまとめ、`parse_report` と `locate` から理由付きで投げれば、クラス3つと付け替えの処理が消える。テストは例外の型ではなく理由の語で照合する形になり、pin は強くなる。出力は変わらない
2. 入口の起動を並べる (`check-outgoing-text.py` の `run_layer1`、`run_layer2`、`check`): 入力と config ごとの gitleaks と層 2は互いに独立なのに、逐次に起動している。標準ライブラリの `concurrent.futures` で並べると、1入力の `check` が約半分になり、入口のテストファイルが11.7秒から7.8秒になった (負荷の高いホストでの中央値)。runner の hook は毎コミット走るので、この差はコミットのたびに効く。custom の config が失敗しても既定の config の起動が走る (結果は捨てる) 点だけが変わる
3. 層 2の判定を見るテスト2本が実物の gitleaks を起動している (`test_check_outgoing_text.py` の `test_denylist_stubs` と `test_combine_layer2_worst_state`): 見ているのは層 2の判定で、層 1の結果に依存しない。PATH を空のディレクトリにすれば約1秒縮む。2を採るなら効果はほぼ消える
4. 対照検査の hook の発火条件 (`.pre-commit-config.yaml` の `leak-guard-rules` の `files:`): 検査が読むファイルの集合を、手で保守する2つ目のリストとして持っている。ISSUE-58でも3本から5本へ広げる必要があり、スクリプトの側と突き合わせるテストは無い。`always_run: true` にすれば2つ目の canonical が消える (60ケースで約0.3秒)。hook が毎コミット走るようになる
5. 対照検査の出力の伏せ方 (`scripts/check-leak-guard-rules.py` の `redact_paths`): 絶対パスを公開される CI のログへ出さないという規則が呼び出し箇所ごとに散っていて、print の一部だけがこれを通る。出力の関所を1つにして全ての print をそこ経由にすれば、新しい print が黙ってパスを出す経路が消える。このスクリプトにはテストが無い
6. custom の config の自己除外 (`leak-guard.gitleaks.toml`): config 自身のコメントの例がルールに当たるので、config を名前で層 1の走査から外している。例をプレースホルダの形で書けば、生きている2本の config を除外から外せる (履歴に残る root の config の名前は外したままにする)。層 1が見るファイルが変わる
7. 上限を超える本文の分割を入口の中へ (`check-outgoing-text.py` の入力の前段): 今は空行の位置で分けて渡し直す手順をエージェントに任せている。入口が層 1についてだけ既存の空行の位置で分けて走査し、座標を元へ戻せば、手順とユーザーへの確認が1つずつ減る。100KB を超える本文はまれなので優先度は低い
8. ISSUE-58の PR で触っていないファイルのコメント: `plugins/dev-workflow/skills/in-repo-issue/scripts/issue-id.py` の `allow_abbrev` の説明に、層 2で直したのと同じ誤り (`--che` を短縮として受理すると書いているが、実際は ambiguous で止まる) がある。`scripts/check-issue-closure.py`、`scripts/check-related-refs.py`、`scripts/gen-readme.py`、`skills/devops/macos-vm-verification/macvm.py`、`plugins/dev-workflow/skills/pre-merge-quality-gate/SKILL.md`、`.gitleaksignore` に、in-repo Issue の識別子を書いたコメントがある。担当の範囲を指すものは残すか外すかの判断が要る

## タスク

- [ ] 1の例外クラスを統合するか決め、採るなら理由の語で照合するテストへ直す
- [ ] 2と3の起動の並べ方を決め、採るならテストの spy が数える起動の回数と順序を確かめる
- [ ] 4の hook の発火条件を決める
- [ ] 5の出力の関所を作るか決める。作るなら、パスを含む出力を足したときに伏せられることをテストで押さえる
- [ ] 6の config の除外を減らすか決め、減らすなら全履歴の走査の件数を ISSUE-58の基準と比べる
- [ ] 7の分割を入口へ移すか決める
- [ ] 8のコメントを直す

## 関連

ISSUE-58 (この指摘を出したマージ前ゲートの対象)
ISSUE-55 (層 2のマージ前レビューで消化しなかった分。同じ形の記録)
ISSUE-66 (user-path のルールを変えるときに、6の例の書き方と関わる)
