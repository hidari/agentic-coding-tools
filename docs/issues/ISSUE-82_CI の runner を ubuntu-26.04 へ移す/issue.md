---
status: open
---

# ci: CI の runner を ubuntu-26.04 へ移す

## 背景

GitHub の告知 (main の CI の注釈と https://github.com/actions/runner-images/issues/14748) によると、`ubuntu-latest` は 2026-10-19 から Ubuntu 26 へ移り始め、2026-11-19 までに移り終える予定である。その間、`ubuntu-latest` の job が 24.04 と 26.04 のどちらに当たるかは分からない。

このリポジトリの CI は runner の `python3` をそのまま使う (`setup-python` を置かない理由は `.github/workflows/ci.yml` のコメント)。2026-09-28 に `ubuntu:26.04` の image (`Ubuntu 26.04.1 LTS`) で `apt-cache policy` を引くと、python3 の候補は 3.14 系、git の候補は 2.53 系だった。24.04 の runner (python3 は 3.12 系) から版が上がり、手元の pre-commit が使う python3 (開発機では 3.14 系) と揃う。版が揃うと、`sys.version_info` で期待値を分けたテストの 3.13 未満の側は、どの自動の経路でも走らなくなる。テスト ID の集合は変わらないので、manifest の照合は緑のままになる。この分岐を自動の経路へ戻すかは設計の判断が要るので、ISSUE-83 で扱う。この Issue では、どの分岐がどこで走るかを記録するところまでを行う。

2026-09-28 のユーザー裁定で、ローカルの `ubuntu:26.04` のコンテナで確かめたうえで、runs-on を `ubuntu-26.04` にした PR で実際の runner も踏み、そのまま `ubuntu-26.04` でマージすることにした。切り替え期間の混在を避けられる代わりに、次の LTS へ上げる手間が 1 つ残る。ローカルのコンテナは最小の image なので、runner image との差 (git の版、preinstalled のツール、HOME) は埋まらない。実際の runner の PR はその差を埋めるために置く。runner は非 root で動くので、ローカルでも非 root で回す (root は `chmod` で作った権限エラーを無視して読めるので、権限を扱うテストが Ubuntu 26.04 と関係なく落ちる)。

`main` の ruleset は PR と 4 job の required checks を要求するが、owner は常に bypass できる (2026-09-28 に ruleset の詳細で確認)。この Issue を閉じるコミットは実際の runner の CI より先に入るので、緑を確かめてからマージすることはユーザーの運用で担保し、実際の runner の結果は PR へのコメントに残す。

## タスク

- [ ] 完了の定義の 1〜2 (ローカルの確認) を満たす
- [ ] 完了の定義の 3 (runs-on の変更と、runner を事実として書いた記述の扱い) を満たす
- [ ] 完了の定義の 4 が挙げる項目を「結果」節に記録する

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
4. 記録: ISSUE-82 に「結果」節を足し、base image の digest と Architecture、1 の版、2 の各 run の rc を書く。CI (26.04) と手元の python3 の版を並べ、sys.version_info で期待値を分けたテストのどちらの分岐がどこで走るかも書く。dev-workflow:in-repo-issue の「クローズ経路: feature PR 同梱を優先」節の手順で、タスクの [x] 化とクローズを 1 コミットにする
5. PR: dev-workflow:pre-merge-quality-gate を通した後の HEAD で pre-commit run --all-files が rc=0 になってから、本文に Closes ISSUE-82 を持つ PR を作る。PR の CI の job がすべて success で、job の数が 3 で抽出した runs-on の数と一致し、その run の headSha が gh pr view --json headRefOid と一致することを示す。gh api の jobs で各 job の labels が ubuntu-26.04 であること、各 job の Set up job のログから grep -F 'Image: ubuntu-26.04' の行と grep -A3 -F 'Operating System' の行を示す。同じログで /home/runner が現れるかも grep する。run の ID とそれらの行を、dev-workflow:commit-and-pr-message の手順で PR にコメントする

失敗したときの出口:
- ローカルか実際の runner で落ちたら、原因を直す最小の変更で直してよい。原因がリポジトリの外にある一時的な失敗 (取得の失敗など) は、push せずに gh run rerun <run の ID> --failed で回し直す。直すための push は原因ごとに 1 回にまとめる。テストの削除、期待値の緩和、--update-manifest による焼き直しはしない。変更したファイルを git diff --stat main で示す
- 直すのに設計の判断が要る (Python の版に依存する挙動など) と言えるのは、失敗したテストの ID、ログの該当行、ローカルの非 root の 26.04 のコンテナでの同じテストの結果を並べて示した場合に限る。その場合は直さず、原因を「結果」節に書き、ISSUE-82 は閉じずに (4 のクローズを済ませていたら dev-workflow:in-repo-issue の Phase F の手順で戻して) PR を作って止まる

制約:
- マージしない。ユーザーが ! で行う
- コミット・PR のタイトルと本文・PR のコメントに、ホストの絶対パスも、その区切りをダッシュに置き換えた形も書かない
```

## 関連

ISSUE-66 (本文が CI の runner の label を観測の記録として書いている)
ISSUE-83 (CI の python3 が手元と揃い、版で分けたテストの分岐が自動の経路から外れる)
ISSUE-84 (完了の定義の共通の条項をひな形にする)
