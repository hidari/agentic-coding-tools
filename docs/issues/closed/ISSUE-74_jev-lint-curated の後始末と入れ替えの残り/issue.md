---
status: closed
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

- [x] host の入れ替えを直列にするか、戻してから消す形にするかを決めて直す。どかしてから置く手順と、置き換えの失敗の再確認をテストで押さえる
- [x] signal を例外に変える文脈で、`SIG_IGN` のハンドラは置き換えない。`None` のハンドラの復元を扱う
- [x] 位置引数の `-` 始まりの拒否を `normalize_path` と `parse_version` に一本化する
- [x] rmtree の消し残しで worktree の登録が残ったときも告げる
- [x] 相対の TMPDIR のとき compat の置き場をどうするか (相対を拒否する、cwd の外へ置く、check と同じくリポジトリの中を拒否する) を決めて直す。テストは `os.environ` の TMPDIR も同じ値にした本番の形で押さえる
- [x] dry-run の要約の費用の見積もりに、ラッパが渡すパス数 (`--retry 3`) を反映する。掛けた値を出すか、パス数を併記するかを決めて直す
- [x] 未回答の subject を識別できるようにするか (要約に一覧を出す、`--json-out` の記録で見分ける方法を SKILL.md に書くなど) を決める。既知の指摘が出なかったときに、未回答と cutoff 未満を区別するため。`--json-out` の記録は、答えの無い subject を値が null の答えとして持つ
- [x] `--allow-build=@ast-grep/cli` の理由と、postinstall が走ったかの確かめ方を `jevlint_host.py` に WHY として書く。書く前に pnpm 12.3.4 で測る。フラグ無しでは、ISSUE-65 の記録どおり `pnpm add` が止まるかを見る。`--ignore-scripts` と温まった store (side-effects cache) では、host の `ast-grep` がネイティブバイナリ (Linux なら ELF、macOS なら Mach-O) になるかと、`--version` の stderr に `postinstall script did not run` が出るかを見る。ISSUE-81 は空の store の Linux (arm64) しか見ていないので、macOS と x64 も含める

## 結果

2026-09-29 (UTC) に実装した。タスクごとの決定と確かめたことを書く。方針の 4 つ (host の入れ替え、相対の TMPDIR、費用の見積もり、未回答の見せ方) はユーザー裁定で推奨の案に決めた。

### host の入れ替え

- `prepare_host` は、再利用できないと読んだ後の再確認・`pnpm add`・入れ替えを、host の親の版ごとのロックファイル (`.lock-<版>`) への排他の `flock` の内側で行う。最初の確認はロックの外で行い、再利用できればロックもロックファイルの作成もしない (double-checked)。ロックを先に取る形だと、親が読み取り専用で host だけ再利用できる配置でロックファイルを作れず、再利用できる host があるのに 2 で終わる (実装の途中の版で起き、この形にした)
- どかしてから置く手順と、置き換えの失敗の再確認の分岐はテストで押さえた (どかさずに消す変異、再確認を外す変異、再確認をロックの外へ出す変異で赤)
- 2 プロセスを同時に走らせる確認 (テストの外、偽の取得で、別の版の host がある状態から。ログは残していない): flock ありは 5 回とも rc [0,0] で取得は 1 回、残骸はロックファイルと host だけ。これは実装とは別に、一次資料と突き合わせるレーンが隔離した場所で再現した。flock を外した対照では毎回 2 回取得した (実装の側で 3 回、レーンで 3 回)。実装の側の 1 回目では、片方が `host.rename(old)` の ENOENT から `HostError: host の設置に失敗した` になった (背景の「同時の rename で偽の終了コード 2」)。レーンの 3 回では出ず、タイミングに依存する

### signal

- 元のハンドラが `SIG_IGN` か `None` のシグナルは置き換えない。先に `getsignal` で読んでから決める。`SIG_DFL` は置き換える (既定の動作では後始末が走らない)

### 位置引数の `-` 始まり

- 入口の検査を外した。canonical 側が止めることを先に確かめた: `normalize_path` は正規化した後の先頭の `-` を見るので `-x`・`./-x`・`.//-x`・`-` を止め、`parse_version` は数字の形の fullmatch なので `-` 始まりを通さない。`check -- -x` は `'-' で始まるパスは受け付けない` で終了コード 2 になり、時点は git の後・host の前に移った

### worktree の後始末の告知

- 背景の「ディレクトリが在るので prune の対象にならない」は半分だけ正しかった (実測: git 2.55.0)。prune が見るのは `<tree>/.git` の有無で、ディレクトリが残っても `.git` が無ければ登録は消える。作業ツリーが読み取り専用のとき、`worktree remove --force` は登録を先に消してから作業ツリーの削除で失敗する (rc 255)。`.git` が壊れていて読み取り専用のときは、remove は何も消さず (rc 128)、prune は rc 0 で登録を残す
- prune の後にディレクトリが残っていれば告げる形にし、告知の文面を、登録が残った場合と残らない場合のどちらでも真になる形に変えた

### 相対の TMPDIR

- `tmpdir_from_env` は、空でない相対の `TMPDIR` を `TreeError` (終了コード 2) にする。check・review・compat の 3 つとも同じ規則で、呼び出し側の経路のテストを置いた。テストは `os.environ` の TMPDIR も同じ値にし、`tempfile.tempdir` のキャッシュを前後で戻す

### 費用の見積もりと未回答

- dry-run の見積もりは `費用 $0.05370 (1 パス分。3 パスで最大 $0.16110)` の形にした。上流 (0.7.0) の `dist/run.js` の `run` では、正の整数の `retry` がそのままパス数 (`passes`) になり、`usd` は 1 パス分。パス数は `jevlint_host.RETRY_PASSES` の 1 箇所に置き、`fixed_tail` と要約の両方がそれを使う
- 未回答の行に、記録の `answers` のうち `value` が null の要素をファイルごとに数えた内訳を出す (例: `未回答: 133 件 (scripts/test_check_issue_closure.py 133)`)。記録の形は上流の `buildRecord` (`dist/run.js`) で確かめた。数えた合計が `stats.missing` と食い違うときは両方を出し、記録の形が想定と違うときは件数だけを出す

### `--allow-build=@ast-grep/cli`

pnpm 12.3.4、jev-lint 0.7.0 で、macOS arm64 (node v24.18.0) と Linux x64 (`node:24-trixie` を `--platform linux/amd64`、非 root、node v24.21.0) の 2 つの環境を測った。store とメタデータの cache は作業ディレクトリの下に閉じた (`--store-dir` と環境変数 `PNPM_CONFIG_CACHE_DIR`。`pnpm add` は `--cache-dir` を受け付けなかった)。

| ケース | pnpm add | rc | パッケージの中の ast-grep | `--version` の rc | stderr の `postinstall script did not run` |
|---|---|---|---|---|---|
| A: 空の store、フラグあり | `--allow-build=@ast-grep/cli jev-lint@0.7.0` | 0 | ネイティブ (macOS は Mach-O arm64、Linux は ELF x86-64) | 0 | 無し |
| B: 空の store、フラグ無し | `jev-lint@0.7.0` | 1 (`ERR_PNPM_IGNORED_BUILDS`、挙がるのは `@ast-grep/cli@0.45.3` だけ) | 1481 バイトの Node のスクリプト | 0 | あり |
| C: 空の store、`--ignore-scripts` | フラグあり + `--ignore-scripts` | 0 | Node のスクリプト | 0 | あり |
| D: A の store、フラグあり | A と同じ | 0 | ネイティブ | 0 | 無し |

- rc では postinstall が走ったかを区別できない。区別できたのは、パッケージの中の `ast-grep` の実体と、stderr の警告
- 表の `pnpm add` の rc は測った subagent の報告の値で、ログには終了コードが残っていない。ログにあるのは、A・C・D の `Done in` と B の `ERR_PNPM_IGNORED_BUILDS` の文面。macOS の B の rc 1 は、一次資料と突き合わせるレーンが再現した
- pnpm の出力の `postinstall` の行は目印にならない。A の store には、両方の環境とも side-effects の記録 (macOS は `darwin;arm64`、Linux は `linux;x64` の `ast-grep` の追加) があった。それでも macOS の D はこの行が無いのにネイティブになり、Linux の D は postinstall を再び走らせた。違いの原因は確かめていない
- postinstall が走ると、pnpm が作る jev-lint の中の bin の shim (`exec node .../ast-grep`) はネイティブのバイナリを JS として読み、SyntaxError の rc 1 になる。上流は shim より先にパッケージの位置の `ast-grep` を起動する (`dist/scan.js` の `astGrepBin`) ので、今は踏まない
- pnpm 12.3.4 は `pnpm add` の cwd に `pnpm-workspace.yaml` (`allowBuilds`) を書く。host の中にもこのファイルができる
- 測っていないもの: macOS x64、musl の Linux、ラッパの `prepare_host` を通した取得 (pnpm を直接呼んだ)

理由と確かめ方は `_install_into` (`prepare_host` から切り出した取得の本体) の `--allow-build` の行の前にコメントで書いた。

### マージ前ゲート

8 観点 (コードレビュー、テストの素材と pin の強さ、ボーイスカウト、PUBLIC 漏洩スイープ、simplify の 4 観点) の指摘 16 件をすべて直した。挙動を変えるものは無かった。主なもの:

- ロックの probe が `LOCK_EX | LOCK_NB` で試していたので、製品コードの排他ロックを共有ロックに弱めても全テストが緑のままだった。probe を `LOCK_SH | LOCK_NB` にして、その変異で赤になることを確かめた
- シグナルのテストが受け継いだ扱いを出発点にしていて、SIG_IGN を残すようにした後は nohup の下で赤になった。SIG_DFL を出発点に明示した

残したもの: 相対の TMPDIR を拒否する時点は host の用意の後で、compat では引数の版の `pnpm add` を走らせてから終了コード 2 になる。止まる向きは正しいが、拒否を前へ出すと失敗の時点が変わるので、この PR では変えない。

## 関連

ISSUE-65 (jev-lint-curated の取り込み。最終レビューの指摘と、完了条件のキーを使う確認で出た件をここへ残した)
ISSUE-81 (空の store での取得の実測。`--allow-build` のタスクの出所)
