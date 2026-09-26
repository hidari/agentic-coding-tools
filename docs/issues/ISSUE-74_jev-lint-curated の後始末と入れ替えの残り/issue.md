---
status: open
---

# refactor: jev-lint-curated の後始末と入れ替えの残り

## 背景

ISSUE-65 で jev-lint-curated を取り込んだとき、最終レビューで出た指摘のうち、設計の判断が要るものと挙動を変えるものを、マージの前には直さずここへ残した。どれも送る範囲とキーの隔離には関わらない。最後の 2 つ以外は、今の形が閉じる側 (失敗なら終了コード 2 で止まるか、他の実行に譲る) に倒れている。

- host の入れ替えの窓: 同時に 2 つの実行が host を用意すると、再利用できるかの確認と古い host をどかす rename の間、どかす rename と置く rename の間に、それぞれ短い窓がある。finally がどかした良い host を消して host の名前が空のまま残る経路と、同時の rename で偽の終了コード 2 になる経路がある。緩和は、兄弟のロックファイルへの `fcntl.flock` で入れ替えを直列にするか、失敗の経路でどかしたものを戻してから消す形
- 上の「どかしてから置く」手順と、置き換えが失敗したときの再確認の分岐は、テストで押さえられていない (どかさずに消す形に戻す変異でも緑のまま)
- signal を例外に変える文脈が `SIG_IGN` も置き換える。nohup の下で SIGHUP が無視されていても、実行が中断されるようになる。前のハンドラが C の側で入れた `None` のときは復元で TypeError になる (終了コード 2 には落ちる)
- 位置引数の `-` 始まりの拒否が、入口の関数と `normalize_path` の 2 箇所にある。canonical は正規化した後で見る `normalize_path` の方で、入口の関数は生の文字列しか見ないので `check -- ./-x` を素通りさせる (通った値は `normalize_path` が止める)。一本化するとエラーが出る時点と文面が変わる
- 後始末の取りこぼしの告知: `_discard_worktree` が worktree の登録の残りを告げるのは、remove と prune がどちらも失敗したときだけである。rmtree がディレクトリを消し残すと、prune は成功してもその登録を残す (ディレクトリが在るので prune の対象にならない) が、告げない
- 相対の TMPDIR と compat: check と review は一時ディレクトリの置き場がリポジトリの中なら 2 で止めるが、compat はリポジトリを使わないのでこの検査を持たない。TMPDIR が相対のとき、置き場を決める `tmpdir_from_env` の fallback の `tempfile.gettempdir()` が同じ相対の値を cwd を基準に絶対化するので、compat の置き場が cwd (消費側のリポジトリであることが多い) の下にできる (Python 3.14.7 と 3.9.6 で実測)。祖先の `sgconfig.yml` は拒否するので ast-grep の設定は差し込めず、compat はキーを使わない (上流は `rules --json` と `check --dry-run` で呼ぶ)。この経路のテストは `env` の TMPDIR だけを変えて `os.environ` は変えないので、本番の形を模していない

## キーを使う確認 (2026-09-27、ラッパは main の 4ad69c7、対象は 66231ad の scripts/)

- ISSUE-65 の完了条件として、`check --commit 66231ad scripts` をキーを使って 1 回走らせた。上流は jev-lint 0.7.0 で、応答したモデルは jev-1.13.0
- 見た subject は 1009 件で、dry-run と同じ。未回答が 133 件あり、うち 9 件は上流の HTTP 400 (`max_tokens_exceeded`) だった。degraded は 1 件
- 指摘は 5 件 (var-name-describes-value が 4 件、test-name-describes-code が 1 件)。ISSUE-65 の測定で有用と判定した 2 件のうち、`INHERITED` (`scripts/test_check_related_refs.py`) は出たが、`children` (`scripts/check-issue-closure.py`) は出なかった。出力は未回答の subject を数えるだけで列挙せず、一時ディレクトリの記録も終了時に消えるので、`children` が未回答だったのか、答えが cutoff に届かなかったのかは区別できない。1 回の実行の有無は、rule が有用かの根拠にならない
- 費用は $0.13330 (126 回) だった。同じ対象の dry-run の見積もりは $0.05370 で、実際はその約 2.5 倍になった

## タスク

- [ ] host の入れ替えを直列にするか、戻してから消す形にするかを決めて直す。どかしてから置く手順と、置き換えの失敗の再確認をテストで押さえる
- [ ] signal を例外に変える文脈で、`SIG_IGN` のハンドラは置き換えない。`None` のハンドラの復元を扱う
- [ ] 位置引数の `-` 始まりの拒否を `normalize_path` と `parse_version` に一本化する
- [ ] rmtree の消し残しで worktree の登録が残ったときも告げる
- [ ] 相対の TMPDIR のとき compat の置き場をどうするか (相対を拒否する、cwd の外へ置く、check と同じくリポジトリの中を拒否する) を決めて直す。テストは `os.environ` の TMPDIR も同じ値にした本番の形で押さえる
- [ ] dry-run の費用の見積もりが実際を下回った (実際は見積もりの約 2.5 倍) 原因を確かめ、見積もりを直すか、見積もりが何を数えていないかを出力に書くかを決める
- [ ] 未回答の subject を識別できるようにするか (一覧を出す、記録を残すオプションを足すなど) を決める。既知の指摘が出なかったときに、未回答と cutoff 未満を区別するため

## 関連

ISSUE-65 (jev-lint-curated の取り込み。最終レビューでこれらを残した)
