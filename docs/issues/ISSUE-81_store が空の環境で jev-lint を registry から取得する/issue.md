---
status: open
---

# test: store が空の環境で jev-lint を registry から取得する

## 背景

v0.10.0 で jev-lint-curated を配布に加えた。消費側での配布経路の確認 (dotfiles の PR #236 の作業ツリー、2026-09-28) では、host の取得 (`skills/tooling/jev-lint-curated/scripts/jevlint_host.py` の `prepare_host` が呼ぶ `pnpm add`) が pnpm の store から複製され、registry からネットワーク越しに取得する段を通っていない。新しいマシンのように store が空の環境で初めて起動したときの経路は、まだ誰も確かめていない。

store から複製した緑と registry から取得した緑は、ラッパの終了コードだけでは区別できない。区別の手がかりは 3 つある。取得の前に store と host が空であること、pnpm の進捗行の `reused` と `downloaded`、ネットワークを切ると同じ起動が失敗することである。3 つ目の対照は、陽性と同じ image から作らないと別の理由で失敗する。pnpm がネットワーク無しで起動できない image では pnpm 自身の取得の失敗になり、clone が image に無ければ `check` は host の取得より前の git の段で止まる。どちらも終了コードは同じになるので、対照として働かない。

環境は Linux コンテナにする (2026-09-28 裁定)。store と host の両方が空の状態を作れ、`@ast-grep/cli` の postinstall がホストで走らない。残る差として、消費側の macOS 用の `@ast-grep/cli` のバイナリは踏まない。

## タスク

- [ ] 下の「完了の定義」の 1〜6 を実測で満たす
- [ ] image と各ツールの版、進捗行、各終了コードをこの Issue の「結果」節に記録する

## 完了の定義

`/goal` の判定役は会話に出た出力しか読まないので、条件は Issue を読まなくても判定できるように自己完結させてある。完了の条件の canonical はこの節で、貼った側を書き換えない。auto mode で回す。ターンの上限で止まったときも `/goal` の記録は achieved になるが、それは完了ではない。「未完了:」の出力が無いことを確かめること。

2026-09-28 に、抜け道と実現不能の 2 観点の批評を 1 回通して直してある。

```
/goal ISSUE-81 を完了させる。jev-lint-curated の v0.10.0 が、pnpm の store も host も空の Linux コンテナで jev-lint を registry から取得して動くことを実測で示し、結果を ISSUE-81 に記録して PR を作る。

進め方の決まり:
- main から新しいブランチを切って作業する
- base image は node:24-trixie (glibc。git が jevlint_tree.py の _MIN_GIT_VERSION を満たす。Alpine は @ast-grep/cli が musl 用のバイナリを持たないので使わない)。python3 と pnpm 12.3.4 を入れ、pnpm はネットワーク無しでも起動できる形にする。v0.10.0 を含むこのリポジトリの clone も image に入れる (陰性のコンテナは clone できないため)。pnpm の store と ~/.cache/jev-lint-curated は image に作らず、XDG_CACHE_HOME も設定しない
- 陽性と陰性は、この同じ image から起動した別々のコンテナで行う。docker run に -t を付けない
- ラッパは git archive v0.10.0 で取り出した skills/tooling/jev-lint-curated を読み取り専用で bind mount し、python3 -E -s -B で起動する。check は clone の中で起動する
- Dockerfile とコンテナで回すスクリプトは .cache/ にファイルで書く。ホスト側のパスは $PWD で書き、$(...) を使わない
- 各段は別々の Bash 呼び出しにする。出力はパイプに繋がず > .cache/<段>.log 2>&1; echo "rc=$?" で取ってから cat で示す。スクリプトに set -e を置かない。陰性の段は Bash の timeout を 600000 にする

満たすこと:
1. 前提: 陽性と陰性の各コンテナで、ラッパを起動する前に次を示す。env の HOME・XDG_CACHE_HOME・XDG_DATA_HOME・PNPM_HOME。cd "$HOME" で実行した pnpm store path と、そのディレクトリが無いか空であること。~/.cache/jev-lint-curated が無いこと。pnpm --version が 12.3.4。docker inspect による各コンテナの image の ID (両者で一致) と NetworkMode (陰性は none)
2. 陽性: 陽性のコンテナで check --dry-run --commit v0.10.0 scripts が rc=0。pnpm の進捗行 (出る版なら) で reused が 0 かつ downloaded が 1 以上。1 と同じ cwd の pnpm store path が指すディレクトリが非空になったことを ls で示す
3. postinstall: node_modules/.pnpm/@ast-grep+cli@*/node_modules/@ast-grep/cli/ast-grep が ELF であること (file か先頭 4 バイト) と、その --version が rc=0 で stderr に postinstall script did not run が無いことを示す
4. 再利用: 同じコンテナで pnpm を PATH から外し (command -v pnpm が非 0 であることを示す)、同じ check が rc=0。pnpm が呼ばれればラッパは rc=2 になるので、host を再利用した証拠になる。示した後に戻す
5. compat: 同じコンテナで compat 0.7.0 が rc=0
6. 陰性: 陰性のコンテナで同じ check が rc=2。stderr に、ラッパの pnpm add の失敗の行と、pnpm 自身の registry へ到達できないことを名指すエラー行がある
7. 記録と PR: ISSUE-81 に「結果」節を足し、base image の digest、Dockerfile の全文、各段の docker コマンド、node・pnpm・git・python3 の版、2 の進捗行、1〜6 の rc を書く。dev-workflow:in-repo-issue の「クローズ経路: feature PR 同梱を優先」節の手順で、タスクの [x] 化とクローズを 1 コミットにする。コンテナを消す。pre-commit run --all-files が rc=0 になり、dev-workflow:pre-merge-quality-gate を通した後に、本文に Closes ISSUE-81 を持つ PR を作り、URL を示す

失敗したときの出口:
- ラッパ自身が原因と言えるのは、同じコンテナで ~/.cache/jev-lint-curated の下の一時ディレクトリに {"private": true} の package.json を置き、ラッパと同じ pnpm add --allow-build=@ast-grep/cli jev-lint@0.7.0 を手で実行すると rc=0 なのにラッパ経由では失敗する場合か、stderr が jevlint: 想定外のエラー で始まる場合に限る。2 回の再現の stderr と手の実行の rc を示し、原因と再現手順を「結果」節に書き、ISSUE-81 は閉じずに PR を作って止まる。それ以外の失敗は環境の側を直して続ける
- ラッパのコードは直さない

制約:
- マージと、キーを使う check / review (--dry-run 無し) はしない。どちらもユーザーが ! で行う
- ホストの ~/.cache と pnpm の store に触れない。ホストで pnpm add を走らせない
- コミット・PR のタイトルと本文・PR のコメントに、ホストの絶対パスも、その区切りをダッシュに置き換えた形も書かない
- 30 ターンに達したら「未完了: 残りの番号」を出力して止まる
```

## 関連

ISSUE-65 (jev-lint の採用方式。host を取得する設計の出所)
ISSUE-74 (jev-lint-curated の後始末と入れ替えの残り)
