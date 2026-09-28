---
status: open
---

# test: store が空の環境で jev-lint を registry から取得する

## 背景

v0.10.0 で jev-lint-curated を配布に加えた。消費側での配布経路の確認 (dotfiles の PR #236 の作業ツリー、2026-09-28) では、host の取得 (`skills/tooling/jev-lint-curated/scripts/jevlint_host.py` の `prepare_host` が呼ぶ `pnpm add`) が pnpm の store から複製され、registry からネットワーク越しに取得する段を通っていない。新しいマシンのように store が空の環境で初めて起動したときの経路は、まだ誰も確かめていない。

温まった store では、pnpm の side-effects cache によって `@ast-grep/cli` の postinstall が飛ばされうる (pnpm の文書による。12.3.4 では未確認)。そのため、ラッパが `--allow-build=@ast-grep/cli` で許している postinstall の実行も、未確認のうちに入る。

store から複製した緑と registry から取得した緑は、ラッパの終了コードだけでは区別できない。区別は下の「完了の定義」の 1・2・4 で行う。陰性の対照 (4) は陽性と同じ image から作る。pnpm がネットワーク無しで起動できない image では pnpm 自身の取得の失敗になり、clone が image に無ければ `check` は host の取得より前の git の段で止まる。どちらもラッパの終了コードは同じになるので、対照として働かない。

環境は Linux コンテナにする (2026-09-28、ユーザー裁定)。store と host の両方が空の状態を作れ、`@ast-grep/cli` の postinstall がホストで走らない。残る差として、消費側の macOS 用の `@ast-grep/cli` のバイナリは踏まない。

## タスク

- [ ] 完了の定義の 1〜4 を実測で満たす
- [ ] 完了の定義の 5 が挙げる項目を「結果」節に記録する

## 完了の定義

`/goal` の判定役は会話に出た出力しか読まないので、条件は Issue を読まなくても判定できるように自己完結させてある。完了の条件の canonical はこの節で、貼った側を書き換えない。auto mode で回す。`/goal` の記録は 3 つの終わり方のどれでも achieved になる。完了したことは、「未完了:」の出力が無く、この Issue が閉じていることで確かめる。

```
/goal ISSUE-81 を完了させる。jev-lint-curated の v0.10.0 が、pnpm の store も host も空の Linux コンテナで jev-lint を registry から取得して動くことを実測で示し、結果を ISSUE-81 に記録して PR を作る。この条件は、下の 1〜5 をすべて満たしたとき、下の「失敗したときの出口」に当たって PR を作ったとき、または 30 ターンに達して「未完了: 残りの番号」を出力したときに満たされる。

進め方の決まり:
- main から新しいブランチを切って作業する
- 各段は別々の Bash 呼び出しにする。rc を取るコマンドはパイプに繋がず、> .cache/<段>.log 2>&1; echo "rc=$?" で取る。ログは短ければ cat で、長ければ要所を grep -n で抜いて示す (Bash ツールの出力は長いと途中で切れる)。スクリプトに set -e を置かない
- Dockerfile とコンテナで回すスクリプトは .cache/ にファイルで書く。ホスト側のパスは $PWD で書き、$(...) を使わない
- docker run に -t を付けない。長い段は Bash の timeout を 600000 にする
- base image は node:24-trixie (glibc。git が jevlint_tree.py の _MIN_GIT_VERSION を満たす。Alpine は @ast-grep/cli が musl 用のバイナリを持たないので使わない)。python3 と pnpm 12.3.4 を入れ、pnpm はネットワーク無しでも起動できる形にする。v0.10.0 を含むこのリポジトリの clone も image に入れる (陰性のコンテナは clone できないため)。pnpm の store と ~/.cache/jev-lint-curated は image に作らず、XDG_CACHE_HOME も設定しない
- 陽性と陰性は、この同じ image から起動した別々のコンテナで行う。陰性 (4) は 2〜3 と並行して回してよい
- ラッパは git archive v0.10.0 で取り出した skills/tooling/jev-lint-curated を読み取り専用で bind mount し、取り出した SKILL.md の「呼び出しの形」節の形で起動する。check は clone の中で起動する

満たすこと:
1. 前提: 陽性と陰性の各コンテナで、ラッパを起動する前に次を示す。env の HOME・XDG_CACHE_HOME・XDG_DATA_HOME・PNPM_HOME。cd "$HOME" で実行した pnpm store path と、そのディレクトリが無いか空であること。~/.cache/jev-lint-curated が無いこと。pnpm・node・git・python3 の --version (pnpm は 12.3.4)。docker inspect による各コンテナの image の ID (両者で一致) と NetworkMode (陰性は none)
2. 陽性: 陽性のコンテナで check --dry-run --commit v0.10.0 scripts が rc=0。pnpm の進捗行 (出る版なら) で reused が 0 かつ downloaded が 1 以上。1 と同じ cwd の pnpm store path が指すディレクトリが非空になったことを ls で示す
3. postinstall: 陽性のコンテナで node_modules/.pnpm/@ast-grep+cli@*/node_modules/@ast-grep/cli/ast-grep が ELF であること (file か先頭 4 バイト) と、その --version が rc=0 でログに postinstall script did not run が無いことを示す
4. 陰性: 陰性のコンテナで同じ check が rc=2。ログに、ラッパの pnpm add の失敗の行と、pnpm 自身の registry へ到達できないことを名指すエラー行がある
5. 記録と PR: ISSUE-81 に「結果」節を足し、base image の digest、Dockerfile の全文、各段の docker コマンド、1 の版、2 の進捗行と store の前後、1〜4 の rc を書く。dev-workflow:in-repo-issue の「クローズ経路: feature PR 同梱を優先」節の手順で、タスクの [x] 化とクローズを 1 コミットにする。コンテナを消す。dev-workflow:pre-merge-quality-gate を通した後の HEAD で pre-commit run --all-files が rc=0 になってから、本文に Closes ISSUE-81 を持つ PR を作り、URL を示す

失敗したときの出口:
- ラッパ自身が原因と言えるのは、同じコンテナで ~/.cache/jev-lint-curated の下の一時ディレクトリに {"private": true} の package.json を置き、ラッパと同じ pnpm add --allow-build=@ast-grep/cli jev-lint@0.7.0 を手で実行すると rc=0 なのにラッパ経由では失敗する場合か、ログに jevlint: 想定外のエラーで判定できない の行がある場合に限る。2 回の再現のログと手の実行の rc を示し、原因と再現手順を「結果」節に書き、ISSUE-81 は閉じずに PR を作って止まる。それ以外の失敗は環境の側を直して続ける
- ラッパのコードは直さない

制約:
- マージしない。キーを使うサブコマンドの扱いは、取り出した SKILL.md の「エージェントが自分で実行してよい範囲」節に従う
- ホストの ~/.cache と pnpm の store に触れない。ホストで pnpm add を走らせない
- コミット・PR のタイトルと本文・PR のコメントに、ホストの絶対パスも、その区切りをダッシュに置き換えた形も書かない
```

## 関連

ISSUE-65 (jev-lint の採用方式。host を取得する設計の出所)
ISSUE-74 (jev-lint-curated の後始末と入れ替えの残り)
ISSUE-84 (完了の定義の共通の条項をひな形にする。この Issue の実行の結果を材料にする)
