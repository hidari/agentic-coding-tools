---
status: open
---

# refactor: jev-lint-curated の後始末と入れ替えの残り

## 背景

ISSUE-65 で jev-lint-curated を取り込んだとき、最終レビューで出た指摘のうち、設計の判断が要るものと挙動を変えるものを、マージの前には直さずここへ残した。どれも送る範囲とキーの隔離には関わらない。「後始末の取りこぼしの告知」と「相対の TMPDIR と compat」の 2 つ以外は、今の形が閉じる側 (失敗なら終了コード 2 で止まるか、他の実行に譲る) に倒れている。費用の見積もりと未回答の subject の 2 件は、最終レビューではなく、ISSUE-65 の完了条件だったキーを使う確認 (下の節) で出たものである。`--allow-build=@ast-grep/cli` の理由の件は、ISSUE-81 の実測をマージ前ゲートに通したときに出た。これは挙動の欠陥ではなく、理由と確かめ方がコードに無いことを扱う。

- host の入れ替えの窓: 同時に 2 つの実行が host を用意すると、再利用できるかの確認と古い host をどかす rename の間、どかす rename と置く rename の間に、それぞれ短い窓がある。finally がどかした良い host を消して host の名前が空のまま残る経路と、同時の rename で偽の終了コード 2 になる経路がある。緩和は、兄弟のロックファイルへの `fcntl.flock` で入れ替えを直列にするか、失敗の経路でどかしたものを戻してから消す形
- 上の「どかしてから置く」手順と、置き換えが失敗したときの再確認の分岐は、テストで押さえられていない (どかさずに消す形に戻す変異でも緑のまま)
- signal を例外に変える文脈が `SIG_IGN` も置き換える。nohup の下で SIGHUP が無視されていても、実行が中断されるようになる。前のハンドラが C の側で入れた `None` のときは復元で TypeError になる (終了コード 2 には落ちる)
- 位置引数 (パスと版) の `-` 始まりの拒否が、入口の関数と canonical の側の 2 箇所にある。canonical は、パスでは正規化した後で見る `normalize_path`、版では数字の形しか通さない `parse_version` である。入口の関数は生の文字列しか見ないので `check -- ./-x` を素通りさせる (通った値は `normalize_path` が止める)。一本化するとエラーが出る時点と文面が変わる
- 後始末の取りこぼしの告知: `_discard_worktree` が worktree の登録の残りを告げるのは、remove と prune がどちらも失敗したときだけである。rmtree がディレクトリを消し残すと、prune は成功してもその登録を残す (ディレクトリが在るので prune の対象にならない) が、告げない
- `--allow-build=@ast-grep/cli` の理由: `jevlint_host.py` の `prepare_host` が `pnpm add` に付けるこのフラグの理由は、コードに無く ISSUE-65 にしか無い。ISSUE-81 では、`@ast-grep/cli` の postinstall が、パッケージに入っている Node のスクリプト `ast-grep` をネイティブバイナリへ置き換えていた。そのスクリプトは、コードを読んだ限りでは stderr に警告を出したうえでバイナリを探して起動するので、postinstall が走らなくても `ast-grep --version` は rc=0 になりうる。ISSUE-65 の記録では、フラグを外すと pnpm 12 は `ERR_PNPM_IGNORED_BUILDS` で止まるので、その後退はラッパの取得の失敗 (終了コード 2) として見える。rc に現れないのは、フラグがあっても postinstall が走らない経路 (温まった store の side-effects cache など) のほうである
- 相対の TMPDIR と compat: check と review は一時ディレクトリの置き場がリポジトリの中なら 2 で止めるが、compat はリポジトリを使わないのでこの検査を持たない。TMPDIR が相対のとき、置き場を決める `tmpdir_from_env` の fallback の `tempfile.gettempdir()` が同じ相対の値を cwd を基準に絶対化するので、compat の置き場が cwd (消費側のリポジトリであることが多い) の下にできる (Python 3.14.7 と 3.9.6 で実測)。祖先の `sgconfig.yml` は拒否するので ast-grep の設定は差し込めず、compat はキーを使わない (上流は `rules --json` と `check --dry-run` で呼ぶ)。この経路のテストは `env` の TMPDIR だけを変えて `os.environ` は変えないので、本番の形を模していない

## キーを使う確認 (2026-09-27、ラッパは main の 4ad69c7、対象は 66231ad の `scripts/`)

- ISSUE-65 の完了条件として、`check --commit 66231ad scripts` をキーを使って 1 回走らせた。上流は jev-lint 0.7.0 で、応答したモデルは jev-1.13.0
- 見た subject は 1009 件で、dry-run と同じ。未回答は 133 件、degraded は 1 件。エラーは 9 件で、これは失敗した request の数であって subject の数ではない。要約が理由を出すのは先頭の 1 件だけで、上流の HTTP 400 (`max_tokens_exceeded`) だった。残り 8 件の理由は出力に無い
- 指摘は 5 件 (var-name-describes-value が 4 件、test-name-describes-code が 1 件)。ISSUE-65 の測定で有用と判定した 2 件のうち、`INHERITED` (`scripts/test_check_related_refs.py:782`) は出たが、`children` (`scripts/check-issue-closure.py:305`) は出なかった (同じファイルの `:743` には別の指摘が出た)。今回は `--json-out` を付けずに走らせたので、記録は一時ディレクトリごと消え、`children` が未回答だったのか、答えが cutoff に届かなかったのかは区別できない。採点は非決定的なので (ISSUE-65-spec.md の既知の限界)、1 回で出なかったことは rule が有用かの根拠にならない
- 完了条件への結論: `INHERITED` が出てモデルが応答したので、キーを渡す経路は端から端まで通った。未回答があったので、要約は指摘より先に「133 件は答えが無い」を出した。この行は終了コード 3 のときだけ出るが、終了コードそのものは記録していない
- 費用は $0.13330 (126 回) だった。同じ対象の dry-run の見積もりは $0.05370 で、実際はその約 2.5 倍になった。同じ dry-run を `--json-out` 付きで再現すると、JSON は requests 45、retry 3 を持っていた。上流の見積もり `usd` は 1 パス分で、ラッパは常に `--retry 3` を渡すが、dry-run の要約は `usd` だけを出す。45 × 3 = 135 は、実行の 126 回 (上流は成功した request だけを数える) とエラー 9 件の和に一致する

## `--json-out` を付けた測り直し (2026-09-28、ラッパは main の 1d9c5ac、対象は同じ)

- 上の確認と同じ対象に `--json-out` を付けて、キーを使ってもう 1 回走らせた。ラッパは上の確認の 4ad69c7 から jev-lint-curated に差分が無い。見た subject 1009 件、未回答 133 件、エラー 9 件、degraded 1 件、費用 $0.13330 (126 回)、指摘 5 件で、件数と費用は上の確認と一致した。指摘も、上の確認が記録した範囲 (rule ごとの内訳と、`:782` と `:743` が出て `:305` が出ないこと) では一致した。今回も request は送られて課金されているが、上流が採点を再利用したのかは出力からは区別できない
- この実行では、`children` (`scripts/check-issue-closure.py:305`) は未回答ではなく cutoff 未満だった。記録には var-name-describes-value の答えがあり、値は 0.393 で cutoff の 0.44 に届かなかった。同じ rule で報告された `INHERITED` (`scripts/test_check_related_refs.py:782`) の値は 0.473 で、3 パスとも cutoff を超えた (パス間の幅は 0.06)。`children` のパスごとの値は記録に無い。上の確認の回がどちらだったかは、再利用の有無が分からず採点も非決定的なので、この結果からは決まらない。`scripts/check-issue-closure.py` の 100 subject には、すべて答えがあった
- 未回答の 133 件は、すべて `scripts/test_check_issue_closure.py` の subject だった (var-name-describes-value 70 件、comment-describes-declaration 63 件)。`--json-out` の JSON の `errors` では、エラー 9 件も同じファイルで、理由は 9 件とも上流の HTTP 400 (`max_tokens_exceeded`) だった (要約は今回も先頭の 1 件だけを出す)。エラーごとの subject 数は 43・44・46 がそれぞれ 3 回ずつで、43 + 44 + 46 = 133 は未回答の件数に一致する。3 つの request が 3 パスとも失敗したと読める (件数の組からの推論で、request の対応は JSON にも無い)。degraded の 1 件も同じファイルで、full から graph へ落ちており、理由は state budget だった
- 未回答か cutoff 未満かは、`--json-out` の記録で見分けられた。記録で値が null の答えは 133 件で、要約の未回答の件数と一致した。タスクの「未回答の subject を識別できるようにするか」を決める材料になる

## タスク

- [ ] host の入れ替えを直列にするか、戻してから消す形にするかを決めて直す。どかしてから置く手順と、置き換えの失敗の再確認をテストで押さえる
- [ ] signal を例外に変える文脈で、`SIG_IGN` のハンドラは置き換えない。`None` のハンドラの復元を扱う
- [ ] 位置引数の `-` 始まりの拒否を `normalize_path` と `parse_version` に一本化する
- [ ] rmtree の消し残しで worktree の登録が残ったときも告げる
- [ ] 相対の TMPDIR のとき compat の置き場をどうするか (相対を拒否する、cwd の外へ置く、check と同じくリポジトリの中を拒否する) を決めて直す。テストは `os.environ` の TMPDIR も同じ値にした本番の形で押さえる
- [ ] dry-run の要約の費用の見積もりに、ラッパが渡すパス数 (`--retry 3`) を反映する。掛けた値を出すか、パス数を併記するかを決めて直す
- [ ] 未回答の subject を識別できるようにするか (要約に一覧を出す、`--json-out` の記録で見分ける方法を SKILL.md に書くなど) を決める。既知の指摘が出なかったときに、未回答と cutoff 未満を区別するため。`--json-out` の記録は、答えの無い subject を値が null の答えとして持つ
- [ ] `--allow-build=@ast-grep/cli` の理由と、postinstall が走ったかの確かめ方を `jevlint_host.py` に WHY として書く。書く前に pnpm 12.3.4 で測る。フラグ無しでは、ISSUE-65 の記録どおり `pnpm add` が止まるかを見る。`--ignore-scripts` と温まった store (side-effects cache) では、host の `ast-grep` がネイティブバイナリ (Linux なら ELF、macOS なら Mach-O) になるかと、`--version` の stderr に `postinstall script did not run` が出るかを見る。ISSUE-81 は空の store の Linux (arm64) しか見ていないので、macOS と x64 も含める

## 関連

ISSUE-65 (jev-lint-curated の取り込み。最終レビューの指摘と、完了条件のキーを使う確認で出た件をここへ残した)
ISSUE-81 (空の store での取得の実測。`--allow-build` のタスクの出所)
