---
status: open
---

# fix: in-repo Issue の識別子が GitHub の番号空間と衝突する

## 背景

in-repo Issue は `docs/issues/<NNN>_<title>/` というディレクトリなので GitHub の Issue/PR
カウンタを消費しない。一方で採番は「リポジトリ内連番 max+1」で、参照も `#NNN` と書く。つまり
**GitHub と同じ記法・同じ番号空間を共有したまま消費だけしない**。結果、in-repo Issue #N を
起票した後に作られた PR が #N を取り、`#N` がどちらを指すか文脈からしか判別できなくなる。

この問題は `astralys-art` の Issue #1267 が investigation として詳細に測っている。本 Issue は
そこで挙がった対応候補を再評価した上で、skill 側の規則としてこのリポジトリで先行実装する。
`astralys-art` と `dotfiles` の移行は本 Issue の範囲外で、各リポジトリの判断に委ねる。

### 実測 (2026-08-15)

3 リポジトリが同じ問題の異なる位相にいる。

| repo | in-repo Issue max | GitHub Issue/PR max | 位相 |
|---|---|---|---|
| astralys-art | 1282 | 1282 | ちょうど並んでいる。次の 1 件が無防備 |
| dotfiles | 34 | 114 | 全 34 件が既に PR と衝突済み |
| agentic-coding-tools (本リポ) | 13 | 9 | #1〜#9 衝突済み、#10〜#13 は未消費 |

本リポは `hasIssuesEnabled: true` で GitHub Issue は 0 件、番号は全て PR が消費している。

### 現状の無事故は規約ではなく副作用に依存している

PR 本文の `Closes [Issue #N](path)` という Markdown リンク形は GitHub の closing keyword
パーサに一致しない (`Closes` と `#N` の間に `[Issue ` が挟まるため)。実測すると
PR #3 / #6 / #9 の `closingIssuesReferences` はいずれも 0 で、自動クローズの誤爆は起きていない。

つまり守っているのはリンク形の副作用であって規約ではない。素形式 `Closes #12` を書けば発火する。

### 既に materialize している実害 (astralys-art #1267 の実測より)

1. GitHub サーバ側に誤結線が記録される。in-repo Issue #1190 を閉じた PR のコメントが、
   無関係な GitHub PR #1190 のタイムラインへ `cross-referenced` として永久に残る。後から消せない
2. 同一ファイルが同じ番号に矛盾した答えを与える (`#1274` が in-repo Issue と GitHub PR の
   両方を指す記述が 1 ファイル内に混在)
3. 参照の 54% が裸の `#NNNN` で書かれており、接頭辞規約 (`Issue #N` / `PR #N`) は過半を
   覆えていない。かつ接頭辞は人間には効くが GitHub の番号解決には効かない

### 既存の対策が破綻した機序

「GitHub にダミー Issue を立てて即 close して番号を予約する」運用が生まれたが、

- どの canonical な手順書にも書かれていない
- 予約 Issue の title 文言が 3 回ドリフトしている
- 2026-08-13 を最後に予約は止まり、止まったことに誰も気づかなかった

予約は Issue を起票するたびに走り続けるトレッドミルで、忘れても何も起きない。

### skill 側の委譲が空振りしている

`in-repo-issue` SKILL.md の「PR / コミット規約」節に

> 採番衝突回避ルールをプロジェクト CLAUDE.md に明示すること

とあるが、本リポの CLAUDE.md 「Issue 管理」節にその記述は無い。散文への委譲は、委譲先が
空でも誰も赤くならないので必ず空振りする。予約運用が止まったのと同じ silent failure の形。

## 設計

4 案を独立に生成して 2 軸 (静かに失敗しないか / 二重管理を作らないか) で評価した。
評価は帯分離 (番号を高位帯へ逃がす案) を僅差で勝たせたが、その決め手だった「規律ゼロで安全」
という利点に対し、帯分離が支払う代償 — 装置量と、交わらないことを距離でしか守れない性質 —
が見合わないと判断して記法分離を採る。判断の根拠は「## 却下した案」節。

### 中核: 識別子の記法を分離する

in-repo Issue の識別子を `ISSUE-<N>` にする。GitHub の autolink が既定で反応するのは `#N`
だけなので、`ISSUE-14` は GitHub のどのオブジェクトにも解決されない。

これは距離ではなく種類の分離である。番号がいくつであろうと `ISSUE-14` と `#14` は別物なので、
GitHub のカウンタがどこまで伸びても衝突しない。帯分離が必要とする cutover 値・接近検出の
tripwire・帯を移す rebanding 手順は、いずれも「いつか交わる」ことへの備えなので全て不要になる。

副次効果として、`Closes ISSUE-14` は GitHub の自動クローズ構文に原理的に一致しないため、
現状の「Markdown リンク形だから偶然セーフ」という副作用依存も同時に消える。

### 適用範囲: 既存 13 件もディレクトリ名を揃える (番号は変えない)

`docs/issues/1_<title>/` を `docs/issues/ISSUE-1_<title>/` へ rename する。closed 配下も含めて
全 13 件。**番号は保存する**ので、既存 Issue の同一性は変わらない。

揃える理由は 2 形式フォークを作らないこと。新規だけを `ISSUE-N_` にすると、番号抽出の
正規表現と全ての glob に旧形式と新形式の 2 アームが永続的に残る。揃えれば単一アームで済む。

### 採番: 連番のまま、ただし決定論的にスクリプト化する

採番規則は `max+1` のままでよい (記法が分離されているので帯は要らない)。ただし現行 A.1 の
inline bash — 8 進解釈の罠、`sort -n | tail -1`、ブレースグループ、NUL 区切りの扱い — を
skill 同梱スクリプトの呼び出しに置き換える。手順書がシェルのセマンティクスを散文で説明する
形をやめ、実行可能な 1 箇所に閉じる。

### 検査

`plugins/dev-workflow/skills/in-repo-issue/scripts/issue-id.py` に置く。標準ライブラリのみ、
外部プロセスは `git` だけ、ネットワーク非依存。exit 0 / 1 (違反) / 2 (検査不能)。
先例は `scripts/check-leak-guard-rules.py`。

規則は 1 つ、エンジンは 1 つ、入口は 3 つにする。

- 規則: `#<digits>` は GitHub 専用。直前が `PR ` (自リポの PR) か `owner/repo` (他リポ) の
  ときだけ許す。in-repo Issue を指す唯一の形は `ISSUE-<N>`
- エンジン: `scan_text()` 1 本。経路ごとに規則が分岐しない
- 入口: (1) 追跡下ファイルの走査 (2) コミットメッセージ (3) PR タイトル / 本文

規約の canonical はスクリプトの docstring。SKILL.md も CLAUDE.md も regex も番号も再掲せず
ファイル名で参照する。

コードフェンスとインラインコード内は免除する (GitHub は autolink しないため)。フェンスの
閉じ忘れは免除を末尾まで広げる最大の穴なので、閉じ忘れ自体を違反として報告する。

### 委譲の空振りを構造で塞ぐ

SKILL.md の「プロジェクト CLAUDE.md に明示すること」を削除し、skill が `issue-id.py` を同梱して
初期化手順で配る形へ置き換える。置いたかどうかはファイルの有無で見えるので「書いたつもり」が
成立しない。

### 同時に直す既存バグ

1. **`git ls-tree -d` が非再帰**なので、未マージブランチの `docs/issues/closed/<N>_...` が
   採番から見えない。skill 自身が「クローズは feature PR 同梱を優先」を推奨しているので、
   ブランチ内で起票と close を同梱した Issue はこの穴にちょうど落ちる。A.1 の解説文が掲げる
   「未マージブランチの起票済み番号も拾う」という目的が closed については達成できていない
2. **Phase C.1 が PR の body しか見ていない**。実測で PR #3 / #6 / #9 はタイトル側にしか
   Issue 参照が無い。title も読む
3. **skill 間契約が literal で二重化している**。`pre-merge-quality-gate/SKILL.md:128` と
   `in-repo-issue` の frontmatter description が `Closes #NNN` / `Fixes #NNN` を持つ。
   記法を変えるならここも直さないと自動クローズが静かに止まる

## 既知の限界 (受容して明文化する)

- 既存の commit message に焼き付いた `#N` は直せない (push 済み)。過去の参照は曖昧なまま残る
- GitHub 上で直接打つ PR タイトル / 本文はローカル hook を通らない。squash merge commit は
  サーバ側生成なので commit-msg hook も通らない。この経路を塞ぐには CI 側の機構が要る
- 検査は自分の取り付けを自分では検証できない。pre-commit と CI の**両方**を同時に外すと
  静かに緑になる (Issue #13 と同じ形。片側だけの撤去は Attachment テストが赤にする)
- 未 push の別 clone / worktree での同時採番は解けない。全 ref 横断スキャンは ref に載った
  分しか見えないので、同番号の二重取得は次の push まで検出されない
- リポジトリに custom autolink reference を設定すると `ISSUE-` が autolink されうる。
  既定では発火しないが、設定した場合はこの設計の前提が崩れる

## タスク

- [ ] `issue-id.py` を作る (`--next` / `--next --as=number` / `--check` / `--check-text`)
- [ ] 既存 13 件を `ISSUE-<N>_<title>` へ rename し、Issue 間の相対リンク 19 件を直す
- [ ] `issue-id.py` を pre-commit の pre-commit stage と commit-msg stage、および CI へ取り付ける
- [ ] 変異注入 3 種 + 範囲 + 緩める方向を置いて、それぞれ赤になることを実測する
- [ ] git を歩く経路のテストは fixture リポを作る形にする (実測で dead pin になった前例がある)
- [ ] skill の A.1 をスクリプト呼び出しへ置き換え、`git ls-tree -d` の非再帰バグを直す
- [ ] skill の Phase C.1 が PR の title も見るようにする
- [ ] skill の C.3 / E.1 の `ls` グロブを見直す (シェルの nomatch 設定に依存して空振りしうる)
- [ ] skill の委譲記述を削除し、スクリプト同梱で置き換える
- [ ] skill 間契約の consumer を数えて直す (`pre-merge-quality-gate` と frontmatter description)
- [ ] CLAUDE.md へ canonical の参照を足す (値は再掲しない)
- [ ] README を再生成する (frontmatter を変えた場合)
- [ ] apm 配布のロールアウト順序を決める (skill は 3 リポ共通なので更新は即座に届く)

## 却下した案

| 案 | 却下理由 |
|---|---|
| 番号空間を高位帯へ分離する (帯分離) | 交わらないことを距離でしか守れず、tripwire (接近検出) と rebanding (帯の移動) という装置が要る。設計者自身が「rebanding 手順は書くが一度も実行されないまま rot する可能性が高い」と認めた。記法分離ならこれらが全て不要になる。評価では僅差で勝ったが、決め手だった「規律ゼロで安全」の利点に対し代償が見合わない |
| 予約運用を CI で検証する | silent failure が loud failure になるだけで、予約を打つ手作業は残る。title 文言が 3 通りにドリフトしているので検査は 3 通り全部を知る必要がある |
| まとめて先行予約 (100 件など) | 先送りにしかならない。使い切れば同じ状態に戻る |
| 参照だけ `IR-N` にしてディレクトリ名は数字のまま | 2 形式フォークは避けられるが、`ls docs/issues/` で新旧が区別できず、ディレクトリ名と参照記法の変換が常に要る |
| GitHub のカウンタを消費する側へ倒す | 防衛が散文の注意書きになり、忘れても何も起きない形が再生産される |
| 既存 Issue を renumber する | 裸参照は薄いが、コミットメッセージに既に焼き付いていて push 済みのため修正できない。存在しない番号を指す記述が残る |
| 既存 13 件のディレクトリ名を数字のまま残す | 番号抽出の regex と全 glob に 2 アームが永続する。rename は番号を保存するので同一性は失われない |

## 関連

- `astralys-art` Issue #1267 (investigation。機序と実測の一次資料。本 Issue はそこから
  skill 側の実装を引き取ったもので、astralys-art 側の既存衝突の遡及可否は向こうの判断)
- 検査機構が自分の取り付けを検証できない問題は Issue #13 と同型
