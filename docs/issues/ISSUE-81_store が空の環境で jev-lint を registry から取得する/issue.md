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

## 結果

2026-09-28 16:47 UTC 頃に実測した。完了の定義の 1〜4 はすべて満たした。各段の rc は次のとおり。

- 1: 陽性の stage1.sh が rc=0、陰性の stage1.sh が rc=0、docker inspect が rc=0
- 2: 陽性の check.sh が rc=0、store-after.sh が rc=0
- 3: astgrep-elf.sh が rc=0、astgrep-version.sh が rc=0 (ログの `postinstall script did not run` は 0 件)
- 4: 陰性の check.sh が rc=2

### 環境

- Docker の server は 29.4.0 の linux/arm64 で、image も linux/arm64 で作った。`@ast-grep/cli` は linux-arm64-gnu のバイナリを踏んだ。x64 のバイナリと、背景に書いた消費側の macOS 用のバイナリは、どちらも踏んでいない
- base image は `node:24-trixie` で、digest は `sha256:be40f6a87b9b22215ddb20da0a2320a5c6d583fe3ee3b0024d9fa4f05b40c8fd` (Dockerfile の FROM で固定した)
- build した image の ID は `sha256:69218703ed317adff478d6b01cbee91d43dc8fdfc7c2b3a3643dfbbbd7ceac34`
- コンテナは root で動かした (HOME は `/root`)
- pnpm は npm も corepack も通さずに置いた。pnpm 12.3.4 の `pnpm` 本体のパッケージは、`optionalDependencies` に `@pnpm/exe.<platform>` を並べ、preinstall と postinstall で `node install.js` を走らせる形で配られている (registry のメタデータで確認。`install.js` の中身は読んでいない)。`@pnpm/exe.linux-arm64` の中身は単一の実行ファイル `package/pnpm` だったので、`@pnpm/exe.linux-arm64@12.3.4` の tarball を registry の `dist.integrity` (sha512) と照合してから実行ファイルだけを取り出した。corepack を避けたのは、corepack の shim が版の指定の無い起動で registry に最新の版を問い合わせ、陰性のコンテナで pnpm 自身の起動が失敗しうるため (corepack の README の `COREPACK_DEFAULT_TO_LATEST` の記載による。ここでは実測していない)

### Dockerfile

```
FROM node:24-trixie@sha256:be40f6a87b9b22215ddb20da0a2320a5c6d583fe3ee3b0024d9fa4f05b40c8fd

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 file \
 && rm -rf /var/lib/apt/lists/*

# pnpm 12 は @pnpm/exe.<platform> の単一の実行ファイルで配られる。npm も corepack も通さずに置くので、
# ネットワーク無しでも起動でき、store も作らない。ハッシュは registry の dist.integrity (sha512) を 16 進にしたもの
RUN curl -fsSL -o /tmp/pnpm.tgz https://registry.npmjs.org/@pnpm/exe.linux-arm64/-/exe.linux-arm64-12.3.4.tgz \
 && echo "b7bd40540ecb46a88a4f2679c4c61a65cda7e437dda4c6dfa2466e8883971c138cd371029c5d2de226306810ea26056394a6143b0685fdb4506a318d038709e3  /tmp/pnpm.tgz" | sha512sum -c - \
 && tar -xzf /tmp/pnpm.tgz -C /usr/local/bin --strip-components=1 package/pnpm \
 && rm /tmp/pnpm.tgz

# 陰性のコンテナは clone できないので、v0.10.0 を含む clone を image に入れる
COPY repo /work/repo

WORKDIR /root
```

### 各段のコマンド

build context と、ラッパを bind mount する元を用意する段。clone の origin はホストの絶対パスを持つので外してから image に入れた。

```
git archive v0.10.0 skills/tooling/jev-lint-curated > .cache/archive.tar
mkdir -p .cache/v0.10.0 && tar -xf .cache/archive.tar -C .cache/v0.10.0
git clone --quiet --no-hardlinks "$PWD" "$PWD/.cache/ctx/repo"
git -C "$PWD/.cache/ctx/repo" remote remove origin
docker pull --platform linux/arm64 node:24-trixie
docker build --progress=plain --platform linux/arm64 -t issue81-jevlint "$PWD/.cache/ctx"
docker run -d --name issue81-pos --platform linux/arm64 -e CLAUDE_SKILL_DIR=/skill -v "$PWD/.cache/v0.10.0/skills/tooling/jev-lint-curated:/skill:ro" -v "$PWD/.cache/ctr:/scripts:ro" issue81-jevlint sleep infinity
docker run -d --name issue81-neg --platform linux/arm64 --network none -e CLAUDE_SKILL_DIR=/skill -v "$PWD/.cache/v0.10.0/skills/tooling/jev-lint-curated:/skill:ro" -v "$PWD/.cache/ctr:/scripts:ro" issue81-jevlint sleep infinity
```

各段は別々に実行し、どれも `> .cache/<段>.log 2>&1; echo "rc=$?"` で rc を取った。

```
# 1
docker exec issue81-pos sh /scripts/stage1.sh
docker exec issue81-neg sh /scripts/stage1.sh
docker inspect -f '{{.Name}} image={{.Image}} network={{.HostConfig.NetworkMode}}' issue81-pos issue81-neg
# 2
docker exec issue81-pos sh /scripts/check.sh
docker exec issue81-pos sh /scripts/store-after.sh
# 3
docker exec issue81-pos sh /scripts/astgrep-elf.sh
docker exec issue81-pos sh /scripts/astgrep-version.sh
docker exec issue81-pos sh /scripts/astgrep-marker.sh
# 4
docker exec issue81-neg sh /scripts/check.sh
# 後始末
docker rm -f issue81-pos issue81-neg
```

`.cache/ctr/` に置いたスクリプトは次の 6 本。check.sh が 2 と 4 で同じラッパの起動を担い、その終了コードはラッパのものになる。

```
# check.sh
#!/bin/sh
# 2. 陽性 / 4. 陰性: clone の中で、SKILL.md の「呼び出しの形」でラッパを起動する。終了コードはラッパのもの
cd /work/repo || exit 90
python3 -E -s -B "${CLAUDE_SKILL_DIR}/scripts/jevlint.py" check --dry-run --commit v0.10.0 scripts
```

```
# stage1.sh
#!/bin/sh
# 1. 前提: ラッパを起動する前の状態を示す。前提がすべて成り立てば 0、どれかが崩れていれば 1 で終わる
ok=0

echo "== env"
for name in HOME XDG_CACHE_HOME XDG_DATA_HOME PNPM_HOME; do
  printenv "$name" > /tmp/env-value.txt
  if [ -s /tmp/env-value.txt ]; then
    read value < /tmp/env-value.txt
    echo "$name=$value"
  else
    echo "$name is unset"
  fi
done

echo "== pnpm store path (cwd=\$HOME)"
cd "$HOME" || exit 90
pwd
pnpm store path > /tmp/store-path.txt
echo "pnpm store path rc=$?"
read store < /tmp/store-path.txt
echo "store=$store"
if [ -e "$store" ]; then
  echo "store exists, entries:"
  ls -A "$store" > /tmp/store-entries.txt
  cat /tmp/store-entries.txt
  if [ -s /tmp/store-entries.txt ]; then
    echo "store is NOT empty"
    ok=1
  else
    echo "store is empty"
  fi
else
  echo "store does not exist"
fi

echo "== ~/.cache/jev-lint-curated"
if [ -e "$HOME/.cache/jev-lint-curated" ]; then
  ls -la "$HOME/.cache/jev-lint-curated"
  echo "jev-lint-curated EXISTS"
  ok=1
else
  echo "jev-lint-curated does not exist"
fi
ls -la "$HOME/.cache" 2>&1

echo "== versions"
pnpm --version > /tmp/pnpm-version.txt
echo "pnpm --version rc=$?"
read pnpm_version < /tmp/pnpm-version.txt
echo "pnpm $pnpm_version"
if [ "$pnpm_version" != "12.3.4" ]; then
  echo "pnpm is not 12.3.4"
  ok=1
fi
node --version
git --version
python3 --version

exit "$ok"
```

```
# store-after.sh
#!/bin/sh
# 2. 陽性: 1 と同じ cwd の pnpm store path が指すディレクトリが非空になったかを示す。非空なら 0
cd "$HOME" || exit 90
pwd
pnpm store path > /tmp/store-path.txt
echo "pnpm store path rc=$?"
read store < /tmp/store-path.txt
echo "store=$store"
ls -la "$store"
ls -A "$store" > /tmp/store-entries.txt
if [ -s /tmp/store-entries.txt ]; then
  echo "store is not empty"
  exit 0
fi
echo "store is EMPTY"
exit 1
```

```
# astgrep-elf.sh
#!/bin/sh
# 3. postinstall: host の中の @ast-grep/cli の ast-grep が 1 つだけあり、ELF であることを示す。そうなら 0
cd "$HOME/.cache/jev-lint-curated/0.7.0" || exit 90
ls -d node_modules/.pnpm/@ast-grep+cli@*/node_modules/@ast-grep/cli/ast-grep > /tmp/bins.txt
wc -l < /tmp/bins.txt > /tmp/count.txt
read count < /tmp/count.txt
echo "count=$count"
if [ "$count" -ne 1 ]; then
  exit 1
fi
read bin < /tmp/bins.txt
ls -l "$bin"
file "$bin"
head -c 4 "$bin" | od -An -tx1 -c
head -c 4 "$bin" > /tmp/magic.bin
printf '\177ELF' > /tmp/elf.bin
cmp /tmp/magic.bin /tmp/elf.bin
```

```
# astgrep-version.sh
#!/bin/sh
# 3. postinstall: host の中の @ast-grep/cli の ast-grep を --version で起動する。終了コードは ast-grep のもの
cd "$HOME/.cache/jev-lint-curated/0.7.0" || exit 90
for bin in node_modules/.pnpm/@ast-grep+cli@*/node_modules/@ast-grep/cli/ast-grep; do
  echo "exec $bin --version"
  exec "$bin" --version
done
```

```
# astgrep-marker.sh
#!/bin/sh
# 3 の対照: 「postinstall script did not run」の文言が @ast-grep/cli のパッケージの中に実在するかを示す。
# 実在すれば、--version のログにそれが無いことは「postinstall が置き換えた」ことの証拠になる
cd "$HOME/.cache/jev-lint-curated/0.7.0/node_modules/.pnpm/@ast-grep+cli@0.45.3/node_modules/@ast-grep/cli" || exit 90
ls -la
grep -rn -F 'postinstall script did not run' . --exclude=ast-grep --exclude=sg
```

### 1. 前提

陽性と陰性で同じ出力になった。

- env: `HOME=/root`。`XDG_CACHE_HOME`・`XDG_DATA_HOME`・`PNPM_HOME` はどれも未設定
- `cd "$HOME"` での `pnpm store path` は `/root/.local/share/pnpm/store/v11` で、そのディレクトリは存在しなかった
- `~/.cache/jev-lint-curated` は存在せず、`~/.cache` 自体も無かった
- 版: pnpm 12.3.4 (陰性のコンテナでもネットワーク無しで起動した)、node v24.21.0、git 2.47.3、Python 3.13.5
- docker inspect: 両方の image が `sha256:69218703ed317adff478d6b01cbee91d43dc8fdfc7c2b3a3643dfbbbd7ceac34`。NetworkMode は陽性が `bridge`、陰性が `none`

### 2. 陽性

check.sh のログの全文 (rc=0)。

```
Update available! 12.3.4 → 12.6.0.
Changelog: https://pnpm.io/v/12.6.0
To update, run: curl -fsSL https://get.pnpm.io/install.sh | sh -
Packages are hard linked from the content-addressable store to the virtual store.
  Content-addressable store is at: /root/.local/share/pnpm/store/v11
  Virtual store is at:             node_modules/.pnpm
Downloading @ast-grep/cli-linux-arm64-gnu@0.45.3: 0.00 B/8.05 MB
Packages: +5
+++++
Progress: resolved 5, reused 0, downloaded 5, added 5, done
.../node_modules/@ast-grep/cli postinstall$ node postinstall.js
.../node_modules/@ast-grep/cli postinstall: Done

dependencies:
+ jev-lint 0.7.0

Done in 2.6s using pnpm v12.3.4
using /tmp/jevlint-rsn_nt2d/scratch/config.json
commit b44ae283e9dde8231707776ab10a8e0a1aa00965  jev-lint 0.7.0
見積もり: subject 1009 件、費用 $0.05371
関連テストが無く 73 件 (paired) を見送った
何にも一致しなかった rule: python/doc-errors-match-body, python/tests-cover-failure-paths
対象ファイルの無い言語: javascript (1), moonbit (7), rust (5), typescript (7)
```

- 進捗行は `Progress: resolved 5, reused 0, downloaded 5, added 5, done` で、reused が 0、downloaded が 5
- store の前後: 前は `/root/.local/share/pnpm/store/v11` が存在しなかった。後は同じ `pnpm store path` (cwd は `/root`) が同じパスを指し、そこに `files/` と `index.db` ができて非空になった (store-after.sh が rc=0)
- 冒頭の `Update available!` は、pnpm が自分の新しい版を registry に問い合わせた結果で、取得そのものには関わらない

### 3. postinstall

- astgrep-elf.sh (rc=0): 該当は `node_modules/.pnpm/@ast-grep+cli@0.45.3/node_modules/@ast-grep/cli/ast-grep` の 1 つだけで、`file` は `ELF 64-bit LSB pie executable, ARM aarch64, version 1 (SYSV), dynamically linked, interpreter /lib/ld-linux-aarch64.so.1, for GNU/Linux 3.7.0` を返し、先頭 4 バイトは `7f 45 4c 46`
- astgrep-version.sh (rc=0): 出力は `ast-grep 0.45.3` で、`postinstall script did not run` は 0 件
- 0 件の対照: registry の `@ast-grep/cli@0.45.3` の tarball をホストの `.cache/` に取って読むと、`package/ast-grep` は `#!/u` で始まる 1481 バイトのスクリプトで、12 行目に `[warn] postinstall script did not run; falling back to runtime binary resolution.` を持っていた。postinstall が走らなければこのスクリプトが残り、`--version` の出力にこの文言が出る。このスクリプトは実行時にバイナリを探して起動するので、`--version` の rc=0 だけでは postinstall が走ったかどうかを区別できない。host の中の `postinstall.js` や `package.json` などにはこの文言が無い (astgrep-marker.sh の grep が rc=1) ので、0 件は置き換わった `ast-grep` を読んだ結果である

### 4. 陰性

check.sh のログの全文 (rc=2)。1〜9 行目が pnpm 自身の registry へ到達できないことを名指すエラーで、最後の行がラッパの pnpm add の失敗の行である。

```
Error: ERR_PNPM_RESOLVING_NPM_RESOLVER_NETWORK_ERROR

  × adding a new package
  ├─▶ Failed to fetch metadata from https://registry.npmjs.org/jev-lint: error
  │   sending request for url (https://registry.npmjs.org/jev-lint)
  ├─▶ client error (Connect)
  ├─▶ dns error
  ╰─▶ failed to lookup address information: Temporary failure in name
      resolution

jevlint: pnpm add jev-lint@0.7.0 が失敗した (終了コード 1)
```

### ISSUE-84 への材料 (実行役の側から)

- 3 の「ログに無い」は、文言がパッケージに実在することを確かめる対照が無いと、見ていない 0 件と区別できなかった。条件は対照を求めていなかったので、実行役が自分で足した
- 「pnpm はネットワーク無しでも起動できる形にする」は手段を指定しておらず、pnpm 12 の配られ方 (本体のパッケージは install scripts と platform ごとの `optionalDependencies` を持つ) を調べて決める必要があった

## 関連

ISSUE-65 (jev-lint の採用方式。host を取得する設計の出所)
ISSUE-74 (jev-lint-curated の後始末と入れ替えの残り)
ISSUE-84 (完了の定義の共通の条項をひな形にする。この Issue の実行の結果を材料にする)
