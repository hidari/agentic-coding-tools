---
status: closed
---

# ci: CI の runner を ubuntu-26.04 へ移す

## 背景

GitHub の告知 (main の CI の注釈と https://github.com/actions/runner-images/issues/14748) によると、`ubuntu-latest` は 2026-10-19 から Ubuntu 26 へ移り始め、2026-11-19 までに移り終える予定である。その間、`ubuntu-latest` の job が 24.04 と 26.04 のどちらに当たるかは分からない。

このリポジトリの CI は runner の `python3` をそのまま使う (`setup-python` を置かない理由は `.github/workflows/ci.yml` のコメント)。2026-09-28 に `ubuntu:26.04` の image (`Ubuntu 26.04.1 LTS`) で `apt-cache policy` を引くと、python3 の候補は 3.14 系、git の候補は 2.53 系だった。24.04 の runner (python3 は 3.12 系) から版が上がり、手元の pre-commit が使う python3 (開発機では 3.14 系) と揃う。版が揃うと、`sys.version_info` で期待値を分けたテストの 3.13 未満の側は、どの自動の経路でも走らなくなる。テスト ID の集合は変わらないので、manifest の照合は緑のままになる。この分岐は ISSUE-83 で扱い、jev-lint-curated の製品コードから pathlib の述語を外して、`sys.version_info` で期待値を分けるテストを 0 件にした。0 件であることは `scripts/test_run_python_tests.py` のテストが見る (2026-09-29)。`sys.version_info` を使わずに版の差を吸収するテスト (例: `test_check_leak_guard_denylist` の `-h<値>` の subcase は、版で経路が変わるため rc に 0 と 2 のどちらも許す) は残っていて、この検査の外にある。この Issue では、CI と手元の python3 の版を記録する。

2026-09-28 のユーザー裁定で、ローカルの `ubuntu:26.04` のコンテナで確かめたうえで、runs-on を `ubuntu-26.04` にした PR で実際の runner も踏み、そのまま `ubuntu-26.04` でマージすることにした。切り替え期間の混在を避けられる代わりに、次の LTS へ上げる手間が 1 つ残る。ローカルのコンテナは最小の image なので、runner image との差 (git の版、preinstalled のツール、HOME) は埋まらない。実際の runner の PR はその差を埋めるために置く。runner は非 root で動くので、ローカルでも非 root で回す (root は `chmod` で作った権限エラーを無視して読めるので、権限を扱うテストが Ubuntu 26.04 と関係なく落ちる)。

`main` の ruleset は PR と 4 job の required checks を要求するが、owner は常に bypass できる (2026-09-28 に ruleset の詳細で確認)。この Issue を閉じるコミットは実際の runner の CI より先に入るので、緑を確かめてからマージすることはユーザーの運用で担保し、実際の runner の結果は PR へのコメントに残す。

## タスク

- [x] 完了の定義の 1〜2 (ローカルの確認) を満たす
- [x] 完了の定義の 3 (runs-on の変更と、runner を事実として書いた記述の扱い) を満たす
- [x] 完了の定義の 4 が挙げる項目を「結果」節に記録する

## 完了の定義

`/goal` の判定役は会話に出た出力しか読まないので、条件は Issue を読まなくても判定できるように自己完結させてある。完了の条件の canonical はこの節で、貼った側を書き換えない。auto mode で回す。`/goal` の記録は 3 つの終わり方のどれでも achieved になる。完了したことは、「未完了:」の出力が無く、この Issue が閉じていることで確かめる。

```
/goal ISSUE-82 を完了させる。CI の全 job の runs-on を ubuntu-26.04 にし、ローカルの Ubuntu 26.04 のコンテナと実際の runner の両方で全 step が通ることを示して PR を作る。この条件は、下の 1〜5 をすべて満たしたとき、下の「失敗したときの出口」に当たって PR を作ったとき、または 30 ターンに達して「未完了: 残りの番号」を出力したときに満たされる。

進め方の決まり:
- main から新しいブランチを切って作業する
- 各段は別々の Bash 呼び出しにする。rc を取るコマンドはパイプに繋がず、> .cache/<段>.log 2>&1; echo "rc=$?" で取る。ログは短ければ cat で、長ければ要所を grep -n で抜いて示す (Bash ツールの出力は長いと途中で切れる)。スクリプトに set -e を置かない
- Dockerfile とコンテナで回すスクリプトは .cache/ にファイルで書く。ホスト側のパスは $PWD で書き、$(...) を使わない
- docker run に -t を付けない。長い段は Bash の timeout を 600000 にする
- コンテナは ubuntu:26.04 を base に --platform linux/amd64 で 1 つ起動し (runner は x86_64)、各段は docker exec で回す。apt の導入と非 root のユーザーの作成は Dockerfile で image に焼いてよい。リポジトリは :ro で bind mount し、コンテナの中の書き込める場所へ全履歴で clone する

満たすこと:
1. ローカルの環境: apt で python3・git・curl・ca-certificates を入れ、非 root のユーザーで clone 以降を回す。run の前に同じコンテナで次を示す: /etc/os-release の PRETTY_NAME、uname -m、dpkg --print-architecture、id -u (0 でない)、python3 --version、git --version、clone の git rev-parse HEAD (ホストの HEAD と一致)
2. ローカルの run: .github/workflows/ci.yml の run を手で写さず grep で抽出して示し、RUNNER_TEMP と GITHUB_PATH を設定したうえで ci.yml の順にすべて実行する。各 run は実行の直前に echo し、直後に rc を出して、すべて rc=0。GITHUB_PATH のファイルに追記された行は PATH の先頭に足し (Actions が行うことの代わり)、command -v gitleaks と gitleaks version を示す。最後に、実行した数と抽出した数の一致を示す。run-python-tests.py は、実行したテスト ID の集合が manifest と一致したと報告すること
3. 変更: ci.yml の runs-on を grep -n で抽出して示し、そのすべてを ubuntu-26.04 にする。runner の label や OS を事実として書いた記述を grep で探し、使ったコマンドと全ヒットを示して、ヒットごとに直すか残すかと理由を書く。観測の記録 (ISSUE-66 の表の /home/runner の行など) は書き換えない
4. 記録: ISSUE-82 に「結果」節を足し、base image の digest と Architecture、1 の版、2 の各 run の rc を書く。CI (26.04) と手元の python3 の版を並べて書く。dev-workflow:in-repo-issue の「クローズ経路: feature PR 同梱を優先」節の手順で、タスクの [x] 化とクローズを 1 コミットにする
5. PR: dev-workflow:pre-merge-quality-gate を通した後の HEAD で pre-commit run --all-files が rc=0 になってから、本文に Closes ISSUE-82 を持つ PR を作る。PR の CI の job がすべて success で、job の数が、この条件の 3 で抽出した runs-on の数と一致し、その run の headSha が gh pr view --json headRefOid と一致することを示す。gh api の jobs で各 job の labels が ubuntu-26.04 であること、各 job の Set up job のログから grep -F 'Image: ubuntu-26.04' の行と grep -A3 -F 'Operating System' の行を示す。同じログで /home/runner が現れるかも grep する。run の ID とそれらの行を、dev-workflow:commit-and-pr-message の手順で PR にコメントする

失敗したときの出口:
- ローカルか実際の runner で落ちたら、原因を直す最小の変更で直してよい。原因がリポジトリの外にある一時的な失敗 (取得の失敗など) は、push せずに gh run rerun <run の ID> --failed で回し直す。直すための push は原因ごとに 1 回にまとめる。テストの削除、期待値の緩和、--update-manifest による焼き直しはしない。変更したファイルを git diff --stat main で示す
- 直すのに設計の判断が要る (Python の版に依存する挙動など) と言えるのは、失敗したテストの ID、ログの該当行、ローカルの非 root の 26.04 のコンテナでの同じテストの結果を並べて示した場合に限る。その場合は直さず、原因を「結果」節に書き、ISSUE-82 は閉じずに (4 のクローズを済ませていたら dev-workflow:in-repo-issue の Phase F の手順で戻して) PR を作って止まる

制約:
- マージしない。ユーザーが ! で行う
- コミット・PR のタイトルと本文・PR のコメントに、ホストの絶対パスも、その区切りをダッシュに置き換えた形も書かない
```

## 結果

2026-09-29 (UTC) にローカルのコンテナで実測した。完了の定義の 1〜4 を満たし、5 は PR のコメントに残す。

### 環境 (完了の定義の 1)

- Docker の server は 29.4.0 (OrbStack、aarch64)。ホストは arm64 なので、`--platform linux/amd64` のコンテナはエミュレーションで動いている。runner の実機 (x86_64) とは CPU の実装が違う
- base image は `ubuntu:26.04`。`docker pull --platform linux/amd64 ubuntu:26.04` の digest は `sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78` で、`docker image inspect` の Architecture は `amd64`
- リポジトリは `/src` に `:ro` で bind mount し、コンテナの中で全履歴 (shallow でない、82 コミット) を clone した。clone に `safe.directory` を渡したのは、`/src` がホストの uid の所有で、clone 元の所有者検査に当たるため

Dockerfile:

```
FROM ubuntu:26.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# runner と同じく非 root で回す (root は chmod で作った権限エラーを無視して読める)
RUN useradd --create-home --uid 1001 --shell /bin/bash runner
USER runner
WORKDIR /home/runner
CMD ["sleep", "infinity"]
```

各段は別々に実行し、直後の `echo "rc=$?"` で rc を取った。docker の段は出力を `.cache/<段>.log` へ向けた。`/src/.cache/` の下のスクリプトは、bind mount 越しに見えるリポジトリの `.cache/` に置いた書き捨てである。

```
docker pull --platform linux/amd64 ubuntu:26.04   # rc=0
docker build --platform linux/amd64 -f .cache/Dockerfile.ubuntu2604 -t act-ubuntu2604:issue82 .cache   # rc=0
docker run -d --name act-issue82 --platform linux/amd64 -v "$PWD":/src:ro act-ubuntu2604:issue82   # rc=0
docker exec act-issue82 bash /src/.cache/clone.sh   # rc=0 (中身は git -c safe.directory=/src clone --no-local /src /home/runner/work/repo と、shallow かとコミット数の表示)
docker exec act-issue82 bash /src/.cache/env.sh   # rc=0 (下の表の値)
docker exec act-issue82 bash /src/.cache/run-ci.sh   # rc=0 (次の節)
```

同じコンテナで run の前に取った値:

| 項目 | 値 |
|---|---|
| `/etc/os-release` の PRETTY_NAME | `Ubuntu 26.04.1 LTS` |
| `uname -m` | `x86_64` |
| `dpkg --print-architecture` | `amd64` |
| `id -u` | `1001` (`runner`) |
| `python3 --version` | `Python 3.14.4` |
| `git --version` | `git version 2.53.0` |
| clone の `git rev-parse HEAD` | `0818e2c10e674a0a1246fd08d564e3e9f1e82615` (ホストの HEAD と一致) |

clone は runs-on を変える前の HEAD なので、ci.yml の run の行は変更後と同じである (この変更は runs-on の 4 行だけ)。

### ci.yml の run (完了の定義の 2)

`grep -E '^[[:space:]]+(- )?run: ' .github/workflows/ci.yml` で 12 件を抽出し、`run:` を含んで `runs-on` を含まない行の数 (12) と一致したので、複数行の `run: |` の取りこぼしは無い。抽出した行から `run: ` までを落とし、ci.yml の順に Actions の既定の shell (`bash --noprofile --norc -eo pipefail -c`) で実行した。`RUNNER_TEMP` と `GITHUB_PATH` はコンテナの中の書き込める場所を指し、各 run の前に `GITHUB_PATH` のファイルの行を PATH の先頭に足した。4 job を 1 つの clone で続けて回したので、job ごとの checkout (package-shape などの fetch-depth 1) は再現していない。

`run-ci.sh`:

```
#!/usr/bin/env bash
# ci.yml の run を手で写さず grep で抽出し、ci.yml の順に実行する。
# 各 run は Actions の既定 shell (bash --noprofile --norc -eo pipefail) で回す。
repo=/home/runner/work/repo
cd "$repo" || exit 1
export RUNNER_TEMP=/home/runner/work/_temp
export GITHUB_PATH=/home/runner/work/_github_path
mkdir -p "$RUNNER_TEMP"
: > "$GITHUB_PATH"

mapfile -t runs < <(grep -E '^[[:space:]]+(- )?run: ' .github/workflows/ci.yml | sed -E 's/^[[:space:]]+(- )?run: //')
echo "extracted=${#runs[@]}"

executed=0
failed=0
for cmd in "${runs[@]}"; do
    # GITHUB_PATH に追記された行を PATH の先頭へ足す (Actions が step 間で行うことの代わり)
    while IFS= read -r p; do
        [ -n "$p" ] || continue
        case ":$PATH:" in *":$p:"*) ;; *) PATH="$p:$PATH" ;; esac
    done < "$GITHUB_PATH"
    export PATH
    echo "=== RUN[$((executed + 1))]: $cmd"
    bash --noprofile --norc -eo pipefail -c "$cmd"
    rc=$?
    executed=$((executed + 1))
    echo "=== RC[$executed]=$rc"
    [ "$rc" -eq 0 ] || failed=$((failed + 1))
    case "$cmd" in
        scripts/ci/install-gitleaks.sh)
            echo "--- GITHUB_PATH:"; cat "$GITHUB_PATH"
            while IFS= read -r p; do
                [ -n "$p" ] || continue
                case ":$PATH:" in *":$p:"*) ;; *) PATH="$p:$PATH" ;; esac
            done < "$GITHUB_PATH"
            export PATH
            echo "--- command -v gitleaks: $(command -v gitleaks)"
            echo "--- gitleaks version: $(gitleaks version)"
            ;;
    esac
done
echo "executed=$executed extracted=${#runs[@]} failed=$failed"
[ "$executed" -eq "${#runs[@]}" ] && [ "$failed" -eq 0 ]
```

RUNNER_TEMP と GITHUB_PATH を 4 job で共有したので、10 の `install-gitleaks.sh` は同じ行を `GITHUB_PATH` に 2 度目として追記した (PATH には重複して足さない)。

| # | run | rc |
|---|---|---|
| 1 | `scripts/ci/install-gitleaks.sh` | 0 |
| 2 | `python3 scripts/check-leak-guard-rules.py` | 0 |
| 3 | `gitleaks git ... -c .../leak-guard.gitleaks.toml` | 0 |
| 4 | `gitleaks git ... -c .../leak-guard-default.gitleaks.toml` | 0 |
| 5 | `python3 scripts/check-package-shape.py` | 0 |
| 6 | `python3 .../issue-id.py --check` | 0 |
| 7 | `python3 scripts/check-related-refs.py` | 0 |
| 8 | `python3 scripts/check-issue-closure.py` | 0 |
| 9 | `python3 scripts/gen-readme.py --check` | 0 |
| 10 | `scripts/ci/install-gitleaks.sh` | 0 |
| 11 | `python3 --version` | 0 |
| 12 | `python3 scripts/run-python-tests.py` | 0 |

- 実行した数 12、抽出した数 12、rc が 0 でないもの 0
- 1 と 10 のあと、`command -v gitleaks` は `RUNNER_TEMP` の下の `gitleaks`、`gitleaks version` は `8.30.1`
- 3 と 4 はどちらも `82 commits scanned.` / `scanned ~3174030 bytes` / `no leaks found`
- 12 は `検査した Python テスト: 17 ファイル / テスト 1073 件 (manifest と一致) / 違反なし`

### runner を事実として書いた記述 (完了の定義の 3)

`grep -n 'runs-on:' .github/workflows/ci.yml` は 14・43・75・82 行の 4 件で、すべて `ubuntu-latest` だったので `ubuntu-26.04` にした。workflow は ci.yml の 1 本だけである。

記述は次の 2 つの `git grep` で探した (この Issue 自身は検索から外した)。

- `git grep -nIE 'ubuntu-latest|ubuntu-2[0-9]\.04|ubuntu:2[0-9]\.04|Ubuntu 2[0-9]|runs-on|/home/runner|ImageOS|runner-images'`
- `git grep -nIE 'runner|3\.12|Linux の CI|CI は Linux|x86_64|linux_x64' -- . ':!docs/issues'`

1 つ目のヒットは ci.yml の 4 行のほかに 11 件で、すべて残した。

| ヒット | 扱い | 理由 |
|---|---|---|
| ISSUE-66 の 17・27・38・51・57 行 (`/home/runner` と `ubuntu-latest`) | 残す | 27 行の表は main の run での観測の記録。ほかは合成した入力の例示とルールの検討で、57 行はすでにこの Issue へ PR のコメントを指している |
| ISSUE-83 (closed) の 13・16・51 行 (`ubuntu-latest` (24.04) の 3.12 系) | 残す | 閉じた Issue の、その時点の状態の記録 |
| ISSUE-83 (closed) の 81 行 (`ubuntu-26.04`) | 残す | 関連節からこの Issue を指す行で、移行先の名前を持つだけ。移したあとも真 |
| ISSUE-84 の 36 行 (`runs-on` の数) | 残す | 判定役の読み違いの記録 |
| `scripts/test_leak_guard_attachment.py` の 78 行 (`SCAN_JOB_KEYS` の `runs-on`) | 残す | job のキーの名前で、label の値を持たない |

2 つ目のヒットは 80 件で、大半は `runner` を「テストの runner (`run-python-tests.py`)」や変数名の意味で使っている。CI の runner を指すのは ci.yml のコメント、CLAUDE.md の 59 行、`install-gitleaks.sh`、`run-python-tests.py` の docstring、`test_macvm.py` と `test_winvm.py` の 4 行、`test_macvm.py` の 338 行 (`CI は Linux`)、`test_jevlint_fs.py` の 25 行 (非 root で走る)、`test_jevlint_tree.py` の 767 行 (`CI の git の版は分からない`)、in-repo-issue の `SKILL.md` の 30 行 (`CI runner も skill を読み込まない`) で、どれも label と版を持たず、26.04 でも成り立つので残した。`ci-runner` の許可ケース (`check-leak-guard-rules.py` の 171 行、`leak-guard.gitleaks.toml` の 72 行の許可の `runner`、`leak-guard-cases-manifest.txt` の 5 行) は macOS の runner のホームの形 (`/Users/runner`) を許可するもので、Ubuntu の label とも版とも関係しないので残した。`install-gitleaks.sh` の `linux_x64` は runner の arch で、26.04 でも x86_64 なので残した。3.12 のヒット (`jevlint_fs.py` などの実測の版の列挙、`markdown-to-pdf` の `requires-python`) は CI の runner と関係しない。

### python3 の版

| 経路 | 版 |
|---|---|
| CI (ubuntu-26.04、python-tests の job の `python3 --version`) | `Python 3.14.4` (PR 75 の最初の run 36603235212) |
| ローカルの ubuntu:26.04 のコンテナ | `Python 3.14.4` |
| 手元 (開発機の pre-commit が使う `python3`) | `Python 3.14.7` |

## 関連

ISSUE-66 (本文が CI の runner の label を観測の記録として書いている)
ISSUE-83 (CI の python3 が手元と揃い、版で分けたテストの分岐が自動の経路から外れる)
ISSUE-84 (完了の定義の共通の条項をひな形にする)
