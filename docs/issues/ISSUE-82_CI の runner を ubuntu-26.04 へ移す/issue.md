---
status: open
---

# ci: CI の runner を ubuntu-26.04 へ移す

## 背景

GitHub の告知 (main の CI の注釈と https://github.com/actions/runner-images/issues/14748) によると、`ubuntu-latest` は 2026-10-19 から Ubuntu 26 へ移り始め、2026-11-19 までに移り終える予定である。その間、`ubuntu-latest` の job が 24.04 と 26.04 のどちらに当たるかは分からない。

このリポジトリの CI は runner の `python3` をそのまま使う (`setup-python` を置かない理由は `.github/workflows/ci.yml` のコメント)。2026-09-28 に `ubuntu:26.04` の image (`Ubuntu 26.04.1 LTS`) で `apt-cache policy` を引くと、python3 の候補は 3.14 系、git の候補は 2.53 系だった。今の runner から python3 の版が上がる。

2026-09-28 の裁定で、ローカルの `ubuntu:26.04` のコンテナで確かめたうえで、runs-on を `ubuntu-26.04` にした PR で実際の runner も踏み、そのまま `ubuntu-26.04` でマージすることにした。切り替え期間の混在を避けられる代わりに、次の LTS へ上げる手間が 1 つ残る。ローカルのコンテナは最小の image なので、runner image との差 (git の版、preinstalled のツール、HOME) は埋まらない。実際の runner の PR はその差を埋めるために置く。runner は非 root で動くので、ローカルでも非 root で回す (root は `chmod` で作った権限エラーを無視して読めるので、権限を扱うテストが Ubuntu 26.04 と関係なく落ちる)。

`main` の ruleset は PR と 4 job の required checks を要求するが、owner は常に bypass できる (2026-09-28 に ruleset の詳細で確認)。この Issue を閉じるコミットは実際の runner の CI より先に入るので、緑を確かめてからマージすることはユーザーの運用で担保し、実際の runner の結果は PR へのコメントに残す。

## タスク

- [ ] ローカルの `ubuntu:26.04` のコンテナで `.github/workflows/ci.yml` の全 step を通す
- [ ] runs-on を `ubuntu-26.04` にし、runner の label を事実として書いた記述を実態に合わせる
- [ ] 版と各終了コードをこの Issue の「結果」節に記録する

## 完了の定義

`/goal` の判定役は会話に出た出力しか読まないので、条件は Issue を読まなくても判定できるように自己完結させてある。完了の条件の canonical はこの節で、貼った側を書き換えない。auto mode で回す。ターンの上限で止まったときも `/goal` の記録は achieved になるが、それは完了ではない。「未完了:」の出力が無いことを確かめること。

2026-09-28 に、抜け道と実現不能の 2 観点の批評を 1 回通して直してある。

```
/goal ISSUE-82 を完了させる。CI の 4 job の runs-on を ubuntu-26.04 にし、ローカルの Ubuntu 26.04 のコンテナと実際の runner の両方で全 step が通ることを示して PR を作る。この条件は、下の 1〜5 をすべて満たしたとき、下の「失敗したときの出口」に当たって PR を作ったとき、または 30 ターンに達して「未完了: 残りの番号」を出力したときに満たされる。

進め方の決まり:
- main から新しいブランチを切って作業する
- 各段は別々の Bash 呼び出しにする。rc を取るコマンドはパイプに繋がず、> .cache/<段>.log 2>&1; echo "rc=$?" で取る。ログは短ければ cat で、長ければ要所を grep -n で抜いて示す (Bash ツールの出力は長いと途中で切れる)。スクリプトに set -e を置かない。コンテナで回すスクリプトは .cache/ にファイルで書き、ホスト側のパスは $PWD で書いて $(...) を使わない

満たすこと:
1. ローカルの環境: docker run --rm --platform linux/amd64 で ubuntu:26.04 を起動する (runner は x86_64 で、scripts/ci/install-gitleaks.sh は x64 の gitleaks を取る)。リポジトリは :ro で bind mount し、コンテナの中の書き込める場所へ全履歴で clone する。apt で python3・git・curl・ca-certificates を入れ、非 root のユーザーを作って clone 以降をそのユーザーで回す。run の前に同じコンテナで次を示す: /etc/os-release の PRETTY_NAME、uname -m、dpkg --print-architecture、id -u (0 でない)、python3 --version、git --version、clone の git rev-parse HEAD (ホストの HEAD と一致)
2. ローカルの run: .github/workflows/ci.yml の run を手で写さず grep で抽出して示し、RUNNER_TEMP と GITHUB_PATH を設定したうえで ci.yml の順にすべて実行する。各 run は実行の直前に echo し、直後に rc を出して、すべて rc=0。install-gitleaks.sh の後は RUNNER_TEMP を PATH の先頭に足し (Actions が GITHUB_PATH で行うことの代わり)、command -v gitleaks と gitleaks version を示す。最後に、実行した数と抽出した数の一致を示す。run-python-tests.py は、実行したテスト ID の集合が manifest と一致したと報告すること
3. 変更: ci.yml の runs-on 4 箇所を ubuntu-26.04 にする。runner の label や OS を事実として書いた記述を grep で探し、使ったコマンドと全ヒットを示して、ヒットごとに直すか残すかと理由を書く。観測の記録 (ISSUE-66 の「このリポジトリの CI (`ubuntu-latest`) の main の run のログに現れる」など) は、確かめ直さずに書き換えない
4. 記録: ISSUE-82 に「結果」節を足し、image の digest と Architecture、1 の版、2 の各 run の rc を書く。dev-workflow:in-repo-issue の「クローズ経路: feature PR 同梱を優先」節の手順で、タスクの [x] 化とクローズを 1 コミットにする
5. PR: pre-commit run --all-files が rc=0 になり、dev-workflow:pre-merge-quality-gate を通した後に、本文に Closes ISSUE-82 を持つ PR を作る。PR の CI の 4 job がすべて success で、その run の headSha が gh pr view --json headRefOid と一致することを示す。gh api の jobs で各 job の labels が ubuntu-26.04 であること、各 job の Set up job のログから grep -F 'Image: ubuntu-26.04' の行と grep -A3 -F 'Operating System' の行を示す。run の ID とそれらの行を、dev-workflow:commit-and-pr-message の手順で PR にコメントする

失敗したときの出口:
- ローカルか実際の runner で落ちたら、原因を直す最小の変更で直してよい。実際の runner のための push は原因ごとに 1 回にまとめる。テストの削除、期待値の緩和、--update-manifest による焼き直しはしない。変更したファイルを git diff --stat main で示す
- 直すのに設計の判断が要る (Python の版に依存する挙動など) と言えるのは、失敗したテストの ID、失敗の出力の該当行、ローカルの非 root の 26.04 のコンテナでの同じテストの結果を並べて示した場合に限る。その場合は直さず、原因を「結果」節に書き、ISSUE-82 は閉じない (4 のクローズを済ませていたら、dev-workflow:in-repo-issue の Phase F の手順で戻す)。PR を作った状態で止まり、その場合はそこで完了とする

制約:
- マージしない。ユーザーが ! で行う
- コミット・PR のタイトルと本文・PR のコメントに、ホストの絶対パスも、その区切りをダッシュに置き換えた形も書かない
```

## 関連

ISSUE-66 (本文が CI の runner の label を観測の記録として書いている)
