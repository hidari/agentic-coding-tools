---
status: open
---

# fix: 層 2 のマージ前レビューで消化しなかった 35 件が残っている

## 背景

ISSUE-15 の層 2 を main へ入れる前に、8 観点 (reuse / simplification / efficiency / altitude /
canonical-drift / boy-scout / pin-quality / correctness-security) の並列レビューを回し、54 件の
指摘が出た。critical 2 件と important 11 件、および 1 行で済む boy-scout はその PR で消化した。
本 Issue は残る 35 件を引き取る。

消化しなかったのは件数の問題で、質の問題ではない。1700 行を足す PR にさらに 35 件を積むと、
CLAUDE.md がボーイスカウトルールの境界に置いている「今回の PR のレビュー範囲に収まるか」を
超える。指摘自体はいずれも実測の根拠を持つ。

**この Issue が無いと、35 件は引き継ぎ書にしか残らない。** 引き継ぎ書は ignore される
`.cache/` 配下にあり、消費後に削除される。ISSUE-23 が同じ理由で立っている。

## 読むときの注意

- **行番号は消化前のもの。** 同じ PR が本体とテストを両方直しているので、そのままでは指さない。
  題と識別子で引くこと
- 「本体:NNN」「テスト:NNN」の座標は、層 2の本体とテストが `scripts/` にあった時点のもの (`scripts/check-leak-guard-denylist.py` と `scripts/test_check_leak_guard_denylist.py`)。ISSUE-58の Task 1で両方を `plugins/dev-workflow/skills/commit-and-pr-message/scripts/` へ移し、テストの Attachment は `scripts/test_leak_guard_attachment.py` へ分けたので、ファイルの置き場も変わっている
- **指摘の全文は残っていない。** 生成元は workflow の journal で `.cache/` 配下にあるため消える。
  ここに残したのは題と位置だけなので、着手時は現物を読んで再判断すること
- **再判断を省かないこと。** 消化した 19 件のうち 2 件は、適用したテストが「間違った理由で
  通る」形になっていて変異注入が暴いた。レビューの指摘は出発点であって結論ではない
- **起票直後から `[x]` が付いている項目がある。** ISSUE-15 の PR が別の指摘を直す過程で
  ついでに解決したもので、その旨を項目ごとに書いてある。群の見出しの件数は引き取った総数で、
  未消化の数ではない (未消化の数を書くと、消化するたびに散文だけが古びる)
- **題の「35 件」は起票時の引き取り数。** その後マージ直前のゲートが拾った分を別の群として
  足しているので、題より多い。題を直すにはディレクトリの改名が要り、相対リンクの baseline
  検査に触るので、増えた分は群を足す形で持つ

## タスク

### ボーイスカウト (20 件)

- [x] **テスト:878** — Attachment の pre-commit YAML ヘルパ 4 本が test_issue_id_attachment.py の逐語コピー (F2 / reuse) — ISSUE-58の PR で解決済み。Task 1で4本のヘルパを `scripts/hook_config_lines.py` へ移し、`scripts/test_issue_id_attachment.py` と、層 2の Attachment を分けた先の `scripts/test_leak_guard_attachment.py` の両方がそれを読む形にした。どちらのファイルにもヘルパの定義は残っていない
- [x] **本体:570** — main が受ける env を run_check が貫通して os.environ を直読みしており、index=temporary が dead pin (F15 / altitude) — ISSUE-15 の PR で解決済み。F50 と同時に閉じた。env を run_check の引数へ通し、判定を resolve_index_kind へ切り出して両側の対照を置いた
- [ ] **本体:484** — _read_chunk 末尾の件数検査は構造上到達しない (同じ関数のループが 1:1 で append する) (F5 / simplification)
- [ ] **本体:282** — load_entries の docstring「使えない形はすべて Unable」が実装より広い。自己照合の取り付けだけ main が持つ (F16 / altitude)
- [x] **hook 配線:7** — ヘッダーコメントと gitleaks hook の name が `.gitleaks.toml` のルール集合を再掲したまま層 1 の追加 (vm-uuid / email-address) から取り残されている (F25 / canonical-drift) — ISSUE-58の PR で解決済み。Task 2で冒頭コメントからルール集合の再掲を消し、canonical として2本の config の置き場を名指す形にした。gitleaks の hook は config ごとの2本になり、name はどちらの config を使うかだけを持つ
- [ ] **テスト:138** — entries_of がテスト側で fold を掛けるため、Fold クラスの「リスト側にも fold」の pin が production の parse_entries を通らない (F45 / pin-quality)
- [ ] **テスト:692** — 位置指標の序数と oid が形 (regex) だけで pin され、値が間違っても緑 (F46 / pin-quality)
- [ ] **テスト:429** — gitlink (mode 160000) の除外と gitlinks カウンタに対照が無い (F48 / pin-quality)
- [x] **テスト:693** — index=temporary の分岐に到達するテストが無く、定数 default に潰しても緑 (F50 / pin-quality) — ISSUE-15 の PR で解決済み。**指摘の見立てより悪かった**。到達するテストが無いだけでなく判定規則そのものが誤りで、`GIT_INDEX_FILE` の有無で分けていたため as-is の `git commit` でも temporary になっていた (git は as-is でも hook へ `.git/index` を渡す。実測 git 2.55.0)。つまり hook 経由の全コミットが temporary で、序数ずれの手がかりとして機能していなかった。値を既定の index と突き合わせる形へ直し、両側に対照を置いて変異 2 種 (有無判定へ戻す / 定数 default へ潰す) で赤くなることを確認済み。なお同じ結論は closed の ISSUE-44 が「変数の有無ではなく値が絶対パスかどうかが分ける」として既に記録していた (記録済みの実測が新しいコードへ適用されなかった形)
- [ ] **本体:362** — 非通常ファイル全部に errno=21 (EISDIR) と印字しており、そのためだけに errno を import している (F6 / simplification)
- [x] **本体:39** — 走査面節が内容照合から外れる 3 クラスを書いていない。docstring が canonical と名指しされている面での宣言過大 (F17 / altitude) — ISSUE-15 の PR で解決済み。UTF-16 の手当てを書くときに 3 クラス (gitlink / 上限超え / NUL を含むもの) を明記した
- [x] **本体:196** — fold() の docstring に履歴説明コメントが残っており、同じ経緯が issue.md にもある (F33 / boy-scout) — ISSUE-58の PR のマージ前ゲートで直した。「当初どちらも…測り直した結果がこれ」の2文を消した。前後の NFKC がそれぞれ何を守るかは、続く2つの段落が現在形の理由として持っている
- [ ] **テスト:499** — test_oversize_blobs_are_excluded_without_being_read は「読まずに」を pin していない (F47 / pin-quality)
- [ ] **テスト:424** — 「symlink は辿る」(resolve_denylist の stat) に正の対照が無く lstat へ戻しても緑 (F49 / pin-quality)
- [x] **ISSUE-15 の issue.md** — 「残っているのは層 3 の skill と PR 本文の検査」の列挙が、同じファイルのタスク欄で未決のままの注釈付きタグ本文を落としている (F23 / canonical-drift) — ISSUE-15の PR で対象の文が消えていた (PR #52)。今の ISSUE-15の issue.md に「残っているのは」の列挙は無く、注釈付きタグの本文は「## 適用: 注釈付きタグ」節で走査面に含めると決まっている
- [x] **テスト:878** — .pre-commit-config.yaml を読む 4 つのヘルパが scripts/test_issue_id_attachment.py から逐語で複製されている (F38 / boy-scout) — ISSUE-58の PR で解決済み。F2と同じ指摘で、同じ変更 (Task 1の `scripts/hook_config_lines.py`) で解決した
- [x] **本体:609** — run_check_text の docstring が spec の項目を序数で指し、未実装ステータスをコードで持っている (F35 / boy-scout) — ISSUE-58の PR で解決済み。Task 4で、spec の序数と「未実装」を持つ1文を docstring から消した。層 2の本体に「実装順序」「未実装」の語は残っていない
- [x] **本体:10** — モジュール docstring が spec の旧表を再掲しており、同じ訂正が spec 側にも入って二重になっている (F34 / boy-scout) — ISSUE-58の PR のマージ前ゲートで直した。「当初は3分岐で設計したが…」の旧表と経緯を消し、ファイルがあることとエントリが取れることは別の検査だ、という現在形の理由と「分岐はこの表が canonical」だけを残した。テスト側の docstring 2箇所にあった「当初の3分岐」も、同じく現在形の理由へ書き換えた
- [x] **本体:2** — CLAUDE.md が canonical に指名した module docstring に、リストの書式と内容を読まない blob の条件が無い (F22 / canonical-drift) — ISSUE-15 の PR で解決済み。両方を書いた (「リストの書式」節の新設と、走査面節の 3 クラス)。リストを実際に作る段になって、canonical に指名された文書から書式が読めないことが表面化したもの
- [x] **本体:91** — 恒久ルールの出所としてコード内で ISSUE-15 を引いている (canonical は CLAUDE.md) (F36 / boy-scout) — ISSUE-58の PR で解決済み。Task 1で層 2を配布物へ移したとき、ISSUE-15を出所として引く文を、この検査が防ぐ流入そのものを理由にする文へ書き換えた。層 2の本体に ISSUE-15の名指しは残っていない

### 余力があれば (15 件)

- [ ] **本体:247** — scan_text が 1 行につき禁止語件数ぶんの部分文字列検索を回す (行数 × エントリ数)。正規表現の前置フィルタで同じ出力のまま 13-56 倍 (F10 / efficiency)
      **測り直した結果、着手しない方を推す。** 実リポジトリ (追跡 143 件 / 29,834 行) で全走査が
      エントリ 1 件のとき 0.147 秒、400 件でも 0.465 秒。エントリ 1 件増えるぶんは約 0.8 ms。
      倍率は本当でも絶対値が動機にならない。さらに前置フィルタを 1 本の連言正規表現へ畳むと
      エントリ単位の `denylist line N` の帰属が壊れ、テストの対照 1 本を失う。着手するなら
      帰属を保つ形に限る。なお `fold(line)` のエントリループ外への引き上げは既に入っている
- [ ] **本体:223** — fold() が全行に対して Cf 除去の Python レベル逐文字ループを回す。ASCII 行と printable 行では原理的に no-op (F11 / efficiency)
      **測り直した結果、着手しない方を推す。** `fold` は走査時間の約 78% で、`unicodedata.category`
      の呼び出しが 1.14 M 回。`if text.isascii(): return text.lower()` の高速路は ASCII 域では
      厳密に等価 (ASCII の全 code point で `fold(c) == c.lower()`、実リポジトリの 29,834 行で
      一致を確認) で、実際 61% の行が ASCII のみ。それでも全走査は 0.147 秒から 0.114 秒に
      なるだけ。33 ms のために、照合の正規化関数に 2 本目の経路を作ることになる。ここは
      「両側へ同一に掛ける限り差は一致範囲が広がる側にしか出ず fail-closed へ倒れる」ことを
      理由に casefold を選んだ当の関数で、経路が 2 本になればその同期が新しい pin の対象になる
- [ ] **本体:448** — チャンク境界がテストでも実リポジトリでも一度も実行されていない (F51 / pin-quality)
- [ ] **テスト:834** — 他の pin から論理的に導かれるテストが 3 件あり、単独で赤くなる変異が存在しない (F8 / simplification)
- [ ] **本体:507** — scan_tracked が並行配列と順序の違う 4-tuple を扱っており、同じ内包表記が別の形の上で偶然成立している (F7 / simplification)
- [ ] **本体:41** — 「worktree は 3 経路で blob と食い違う」の閉じた列挙が、最も日常的な経路 (staged 後の未 stage 編集) を落としている (F27 / canonical-drift)
- [ ] **ISSUE-15 の spec** — spec 層 2 節で実装に追い越された文は分岐表だけではないが、注記が付いたのは表だけ (F28 / canonical-drift)
- [ ] **本体:580** — 「検出 0 件なら緑」の判定を run_check と run_check_text の 2 箇所に書いている (F3 / reuse)
- [ ] **CLAUDE.md:104** — 「検証」節の「ローカルにしか無い」検査の列挙に、設計上 CI へ取り付けない層 2 が入っていない (F29 / canonical-drift)
- [ ] **本体:362** — 通常ファイルでない指し先すべてに EISDIR を「相当」として印字しており、FIFO やデバイスでは誤誘導になる (F39 / boy-scout)
- [ ] **テスト:840** — --check-text の要約の行数を見るテストが無い (F53 / pin-quality)
- [x] **CLAUDE.md:23** — 新規に足した「形の決まったカテゴリ」の列挙は `.gitleaks.toml` のルール集合の再掲で、同じ段落が canonical を toml と名指ししている (F26 / canonical-drift) — ISSUE-15 の PR で解決済み。括弧内の列挙を落とした。列挙が toml のルール 3 本と完全一致していること (部分集合でないこと) を確認してから消している
- [x] **テスト:11** — テストの docstring が根拠を「前セッション」に置いており、後から検証できない (F40 / boy-scout)。追記 (2026-09-24): ISSUE-58の Task 1で語は言い換えたが、根拠の実測内容は引用されていなかった — ISSUE-58の PR のマージ前ゲートで直した。言い換えた出所の括弧 (失敗モードを列挙したときの経緯) を丸ごと消した。引用できる実測を持たない出所の記述だったため
- [ ] **テスト:1005** — CI の negative pin は文字列一致なので `pre-commit run` を CI に足す取り付けを見ない (F52 / pin-quality)
- [x] **CLAUDE.md:27** — CLAUDE.md が「取り付けの canonical は docstring」と書いているが、docstring は pre-commit への取り付けを持たない (F41 / boy-scout) — ISSUE-15 の PR で解決済み。確認手順は docstring、取り付けは `.pre-commit-config.yaml` へ振り分けた。誤った参照先は「以後のセッションを間違ったファイルへ送る」ので、指示ファイル上では整理ではなく誤りとして扱った

### マージ直前のゲートで追加で見つかった分 (10 件)

起票後に走らせたマージ前ゲート (simplify 4 観点 / code-reviewer / boy-scout sweep) が、上の
54 件に入っていないものを拾った。行番号は書かない。上の群がそうなっているのと同じ理由で、
同じ PR が本体とテストを続けて直すので着手時には必ずずれる。題と識別子で引くこと。

- [x] **CLAUDE.md の canonical 表** — 行ラベル「secret とパスの漏洩」が `.gitleaks.toml` の実際の射程 (パス・UUID・メールアドレス) より狭く、canonical を宣言している当の表が部分集合になっていた (boy-scout) — ISSUE-15 の PR で解決済み。値を再掲しないラベルへ広げた
- [ ] **本体** — decode の 2 分岐が末尾 4 行の `findings.extend` を共有している。`_decode_blob(body, index, oid) -> str | None` へ切り出せる (S1 / simplification)。隔離コピーで適用して 78 件緑、さらに抽出後の関数内で NUL 検査を UTF-16 検査より前へ動かすと `test_utf16_text_blob_is_scanned_not_counted_as_binary` が赤くなることまで確認済み (pin は抽出を跨いで生きる)
- [x] **本体** — `# noqa: BLE001` がリポジトリ唯一の noqa で、ruff / flake8 / pyproject / setup.cfg の設定はどこにも無い。走らせていない linter を抑制している。WHY は直後のコメントが既に持つ (S3 / simplification) — ISSUE-58の PR で解決済み。層 2の本体の1箇所と、同じ PR が入口 `check-outgoing-text.py` に3箇所、`scripts/check-leak-guard-rules.py` に1箇所足していた4箇所を、まとめて外した。外したのは noqa のコメントだけで、except の行と、その理由を述べるコメントや docstring は残してある
- [ ] **本体** — `scan_tracked` が git plumbing / バイト列のエンコーディング判定 / 走査の帳簿付けの 3 抽象層をまたぐ。S1 が 1 つ持ち上げるので、残るのは `readable → sizes → wanted` のパイプライン (A2 / altitude)。除外件数は要約が印字するので観測可能なまま残すこと
- [ ] **本体** — `env` 引数と `_iter_blobs` の戻りが、モジュール内で唯一の未アノテーション署名 (A3 / altitude)
- [ ] **テスト** — `GIT_ENV` の 9 行が `test_check_issue_closure.py` と `test_check_related_refs.py` と逐語一致で 3 コピー目 (R2 / reuse)。F2 / F38 の hook-config ヘルパと同じ共有モジュールへ寄せられる。これは pin ではなく隔離なので、まとめても検証力は変わらない。追記 (2026-09-24): 層 2のテストは ISSUE-58で配布物 (`plugins/dev-workflow/skills/commit-and-pr-message/scripts/`) へ移ったので、`scripts/` の共有モジュールの対象から外れる。配布物のテストが配布元の `scripts/` を読むと、配布先では成立しない。寄せられるのは `scripts/` に残る `test_check_issue_closure.py` と `test_check_related_refs.py` の2本だけになる
- [x] **本体** — `_locator` を集約したコメントに、この PR 自身が直した欠陥の過去形記述 (「実際に検査不能メッセージの側が序数だけを出しており、一時 index では別のファイルを指していた」) が残っている。集約の WHY は直前の文が述べている (boy-scout) — ISSUE-58の PR のマージ前ゲートで直した。過去形の1文を消し、集約の理由を述べる直前の文だけを残した
- [x] **テスト** — docstring が根拠を「(前セッションの実測)」に置き、実測内容を引用していない箇所がもう 1 つある (F40 と同種で別箇所)。CLAUDE.md は実測内容を添えることを要求しているので、引用するか落とす (boy-scout)。追記 (2026-09-24): ISSUE-58の Task 1で語は言い換えたが、根拠の実測内容は引用されていなかった — ISSUE-58の PR のマージ前ゲートで直した。`Fold` クラスの docstring の「かな・漢字には差が出ない」を測り直し、ひらがな・カタカナ・CJK 統合漢字の各ブロックの全 code point で lower・casefold・NFC・NFKC のどれを掛けても変わらないこと (Python 3.9と3.14の両方) を括弧の中へ引用した。「絶対に」の言い切りは外した
- [ ] **本文側の fold が production の入口を通って pin されていない (未検証)** — `Fold` クラスの 8 件は全部 `scan_text` を直接呼び、`scan_tracked` / `run_check_text` を通る内容 fixture は `fold(x) == x` の語しか使っていないため、`scan_tracked` が fold を通していること自体の対照が無い、という指摘。**未検証**。出した側に Bash が無く静的読解のみだったため、着手時にまず再現から入ること。再現手順は隔離コピーで照合を `e.raw in line` (fold 無しの生一致) へ変えてテストを回す。緑なら dead。手当ては内容 fixture 1 件を全角か NFD の語にして、fold 不変の fixture も残して両側から挟む。F10 の前置フィルタを fold 前の生テキストへ掛ける実装が自然にこの形になるので F10 と同時に判断する
- [ ] **判断の記録** — `split_lines` は `issue-id.py` の `_split_lines` と同じロジックだが**借用しない**。借用機構 (`notation()` + `BORROWED`) は読み込み失敗時に `CheckError` を投げ、この層を apm 配布の plugin パッケージへ結合させる。4 行のロジックと引き換えにしない。見落としではなく決定であることを残す (R3 / reuse)

## 関連

ISSUE-15 (層 2 の実装本体。本 Issue はそのマージ前レビューの残り)
ISSUE-44 (closed。hook から見える `GIT_INDEX_FILE` の実測表を持つ。F15 / F50 の判定規則はこれを適用しなかったことによる)
ISSUE-23 (同じく ISSUE-15 から派生した、散文側の露出スイープの保留分)
ISSUE-12 (検査スクリプトが自分自身のテストを持たない。pin-quality の指摘群と面が重なる)
ISSUE-58 (層 2を配布物へ移し、層 1の config を2本に分けた。同じ PR で F2・F25・F33・F34・F35・F36・F38・F40・S3と、F40と同種の箇所・`_locator` の過去形記述を解決した)
