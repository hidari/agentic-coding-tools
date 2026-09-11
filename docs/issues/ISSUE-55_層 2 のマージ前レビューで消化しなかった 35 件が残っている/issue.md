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
- **指摘の全文は残っていない。** 生成元は workflow の journal で `.cache/` 配下にあるため消える。
  ここに残したのは題と位置だけなので、着手時は現物を読んで再判断すること
- **再判断を省かないこと。** 消化した 19 件のうち 2 件は、適用したテストが「間違った理由で
  通る」形になっていて変異注入が暴いた。レビューの指摘は出発点であって結論ではない

## タスク

### ボーイスカウト (20 件)

- [ ] **テスト:878** — Attachment の pre-commit YAML ヘルパ 4 本が test_issue_id_attachment.py の逐語コピー (F2 / reuse)
- [ ] **本体:570** — main が受ける env を run_check が貫通して os.environ を直読みしており、index=temporary が dead pin (F15 / altitude)
- [ ] **本体:484** — _read_chunk 末尾の件数検査は構造上到達しない (同じ関数のループが 1:1 で append する) (F5 / simplification)
- [ ] **本体:282** — load_entries の docstring「使えない形はすべて Unable」が実装より広い。自己照合の取り付けだけ main が持つ (F16 / altitude)
- [ ] **hook 配線:7** — ヘッダーコメントと gitleaks hook の name が `.gitleaks.toml` のルール集合を再掲したまま層 1 の追加 (vm-uuid / email-address) から取り残されている (F25 / canonical-drift)
- [ ] **テスト:138** — entries_of がテスト側で fold を掛けるため、Fold クラスの「リスト側にも fold」の pin が production の parse_entries を通らない (F45 / pin-quality)
- [ ] **テスト:692** — 位置指標の序数と oid が形 (regex) だけで pin され、値が間違っても緑 (F46 / pin-quality)
- [ ] **テスト:429** — gitlink (mode 160000) の除外と gitlinks カウンタに対照が無い (F48 / pin-quality)
- [ ] **テスト:693** — index=temporary の分岐に到達するテストが無く、定数 default に潰しても緑 (F50 / pin-quality)
- [ ] **本体:362** — 非通常ファイル全部に errno=21 (EISDIR) と印字しており、そのためだけに errno を import している (F6 / simplification)
- [ ] **本体:39** — 走査面節が内容照合から外れる 3 クラスを書いていない。docstring が canonical と名指しされている面での宣言過大 (F17 / altitude)
- [ ] **本体:196** — fold() の docstring に履歴説明コメントが残っており、同じ経緯が issue.md にもある (F33 / boy-scout)
- [ ] **テスト:499** — test_oversize_blobs_are_excluded_without_being_read は「読まずに」を pin していない (F47 / pin-quality)
- [ ] **テスト:424** — 「symlink は辿る」(resolve_denylist の stat) に正の対照が無く lstat へ戻しても緑 (F49 / pin-quality)
- [ ] **ISSUE-15 の issue.md** — 「残っているのは層 3 の skill と PR 本文の検査」の列挙が、同じファイルのタスク欄で未決のままの注釈付きタグ本文を落としている (F23 / canonical-drift)
- [ ] **テスト:878** — .pre-commit-config.yaml を読む 4 つのヘルパが scripts/test_issue_id_attachment.py から逐語で複製されている (F38 / boy-scout)
- [ ] **本体:609** — run_check_text の docstring が spec の項目を序数で指し、未実装ステータスをコードで持っている (F35 / boy-scout)
- [ ] **本体:10** — モジュール docstring が spec の旧表を再掲しており、同じ訂正が spec 側にも入って二重になっている (F34 / boy-scout)
- [ ] **本体:2** — CLAUDE.md が canonical に指名した module docstring に、リストの書式と内容を読まない blob の条件が無い (F22 / canonical-drift)
- [ ] **本体:91** — 恒久ルールの出所としてコード内で ISSUE-15 を引いている (canonical は CLAUDE.md) (F36 / boy-scout)

### 余力があれば (15 件)

- [ ] **本体:247** — scan_text が 1 行につき禁止語件数ぶんの部分文字列検索を回す (行数 × エントリ数)。正規表現の前置フィルタで同じ出力のまま 13-56 倍 (F10 / efficiency)
- [ ] **本体:223** — fold() が全行に対して Cf 除去の Python レベル逐文字ループを回す。ASCII 行と printable 行では原理的に no-op (F11 / efficiency)
- [ ] **本体:448** — チャンク境界がテストでも実リポジトリでも一度も実行されていない (F51 / pin-quality)
- [ ] **テスト:834** — 他の pin から論理的に導かれるテストが 3 件あり、単独で赤くなる変異が存在しない (F8 / simplification)
- [ ] **本体:507** — scan_tracked が並行配列と順序の違う 4-tuple を扱っており、同じ内包表記が別の形の上で偶然成立している (F7 / simplification)
- [ ] **本体:41** — 「worktree は 3 経路で blob と食い違う」の閉じた列挙が、最も日常的な経路 (staged 後の未 stage 編集) を落としている (F27 / canonical-drift)
- [ ] **ISSUE-15 の spec** — spec 層 2 節で実装に追い越された文は分岐表だけではないが、注記が付いたのは表だけ (F28 / canonical-drift)
- [ ] **本体:580** — 「検出 0 件なら緑」の判定を run_check と run_check_text の 2 箇所に書いている (F3 / reuse)
- [ ] **CLAUDE.md:104** — 「検証」節の「ローカルにしか無い」検査の列挙に、設計上 CI へ取り付けない層 2 が入っていない (F29 / canonical-drift)
- [ ] **本体:362** — 通常ファイルでない指し先すべてに EISDIR を「相当」として印字しており、FIFO やデバイスでは誤誘導になる (F39 / boy-scout)
- [ ] **テスト:840** — --check-text の要約の行数を見るテストが無い (F53 / pin-quality)
- [ ] **CLAUDE.md:23** — 新規に足した「形の決まったカテゴリ」の列挙は `.gitleaks.toml` のルール集合の再掲で、同じ段落が canonical を toml と名指ししている (F26 / canonical-drift)
- [ ] **テスト:11** — テストの docstring が根拠を「前セッション」に置いており、後から検証できない (F40 / boy-scout)
- [ ] **テスト:1005** — CI の negative pin は文字列一致なので `pre-commit run` を CI に足す取り付けを見ない (F52 / pin-quality)
- [ ] **CLAUDE.md:27** — CLAUDE.md が「取り付けの canonical は docstring」と書いているが、docstring は pre-commit への取り付けを持たない (F41 / boy-scout)

## 関連

ISSUE-15 (層 2 の実装本体。本 Issue はそのマージ前レビューの残り)
ISSUE-23 (同じく ISSUE-15 から派生した、散文側の露出スイープの保留分)
ISSUE-12 (検査スクリプトが自分自身のテストを持たない。pin-quality の指摘群と面が重なる)
