---
name: release
description: このリポジトリ (agentic-coding-tools) のリリースを切る。annotated tag と GitHub release を作り、消費側へ pin の更新を促すところまでを持つ。「リリース切って」「タグ打って」「release して」と指示された時、および pin を上げる目的で消費側から参照される版を確定させたい時に使う。バージョン規則・事前検査・切ったあとの後始末を持ち、本文の渡し方は dev-workflow:commit-and-pr-message に委ねる。
---

# Release (agentic-coding-tools)

このリポジトリは apm 経由で skill と plugin を配る。消費側は commit SHA で pin するので、tag は固定の機構ではない。tag と release は「その SHA で何が変わったか」をレビュー可能な単位にするための器である。

この非対称を最初に押さえること。tag を pin の機構と誤解すると、`rev` や `#<ref>` を tag に置き換えたくなるが、それは可変参照への退行になる。

## 何が release になるか

消費側が pin を上げる判断をする単位。つまり配布物が変わったときである。

`docs/issues/` だけの変更は配布物を変えないので、単独では release にしない。ただし配布物の変更に同伴するのは構わない。

## バージョン規則

版は `vYYYY.M.N` の CalVer で付ける。例えば 2026 年 10 月に切る最初の release は `v2026.10.0` になる。

- `YYYY` と `M` は tag を打つ時点の UTC の年と月。誰がどのタイムゾーンで切っても同じ版になるように UTC に固定する
- `M` は 0 で埋めない。`v2026.01.0` は semver の数値の先頭に 0 を置く形で、semver として不正になる。3 つの数に収めているのも、版を semver として比べる道具に誤読させないため
- `N` はその月の通し番号で、0 から始める。同じ日に何本切っても衝突しない

次の版は tag から求める。数える前に tag を取り込むこと。手元に無い tag を数え落とすと既にある版を名乗ることになり、手元の `git tag -a` はそれを通してしまう。気づくのは手順 3 の `git push` が既存の tag として拒んだときで、その時点では手元に tag ができている。

```bash
git fetch --tags
y=$(date -u +%Y)
m=$(date -u +%m | sed 's/^0//')
n=$(git tag -l "v$y.$m.*" | wc -l | tr -d ' ')
echo "v$y.$m.$n"
```

月の 0 は `sed` で外す。`%-m` の `-` も `$((10#$m))` の基数の指定も POSIX には無い拡張で、シェルと `date` の実装に依る。glob の `v$y.$m.*` は月の直後の `.` までを含むので、1 月の採番は 10 月の tag を数えない (macOS の sh、bash、zsh で実測)。

版の番号は互換性を表さない。表すのは切った時期だけで、壊れる変更の伝え方は手順 4 の release note が持つ。

v0.20.0 までの tag は、以前の pre-1.0 の semver 風の規則で付けたもの。`git tag --sort=version:refname` で並べると、CalVer の tag はすべてその後ろに日付順で並ぶ (実測)。素の `git tag` は文字列順なので、`v0.10.0` が `v0.9.0` より前に来るのと同じく、`v2026.10.0` が `v2026.9.0` より前に来る。

`plugins/*/.claude-plugin/plugin.json` の `version` は plugin 個別の版であって、リポジトリの tag とは別の数列である。両者を揃えようとしないこと。消費側の lockfile が記録するのは plugin 個別の版で、単体 skill には版の概念が無いため `unknown` が入る。

## 手順

### 1. 事前検査

main が最新で clean であることを確かめ、検査を全部通す。何が走るかの canonical は `.pre-commit-config.yaml` で、ここでは検査を列挙しない。

```bash
git checkout main && git pull
git status --porcelain
pre-commit run --all-files
```

CI も main の最新 SHA で確認する。run の success だけでは足りない。このリポジトリの CI は 4 job だが、Issue 記法と関連節の検査は「パッケージの形と命名規約」job の中のステップなので、job 名一覧には現れない。job 名は job が何をするかの申告であって、何を実行したかの申告ではない。

```bash
sha=$(git rev-parse HEAD)
gh run list --commit "$sha" --json name,status,conclusion
gh run view <run-id> --json jobs --jq '.jobs[] | .steps[] | "\(.conclusion) \(.name)"'
```

### 2. 前回の release からの差分を読む

前回の tag は `version:refname` で並べた末尾から取る。素の `git tag` の末尾は文字列順なので、前回の版にならないことがある (「バージョン規則」の並びの項)。

```bash
prev=$(git tag --sort=version:refname | tail -n 1)
git log --oneline --first-parent "$prev"..HEAD
git diff --stat "$prev"..HEAD -- plugins/ skills/
```

2 つ目で配布物の差分だけを見る。ここが空なら release にしない (上の「何が release になるか」)。

### 3. annotated tag を打つ

以下の `<版>` は、「バージョン規則」の求め方で出した版 (`v` を含む) に置き換える。

本文はファイル経由で渡す。理由と書式の canonical は `dev-workflow:commit-and-pr-message`。

渡す前に漏洩検査の入口へ通す。タグ本文は全履歴の `gitleaks` 走査 (ファイル内容だけを見る) の面の外にあり (実測)、コミットメッセージを見る hook も届かない。この面を見る機会はこの手順にしか無いので、飛ばすと誰も見ないまま公開される。しかも push した後で本文を直すには打ち直すしかなく、参照が変わる。

```bash
python3 plugins/dev-workflow/skills/commit-and-pr-message/scripts/check-outgoing-text.py .cache/tag-<版>.txt; echo "rc=$?"
```

このリポジトリは PUBLIC なので `--target-private` は付けない。終了コードと `result=` の組ごとの行動、検査のあとでファイルを変えたときのやり直し、渡す前に本文を自分で読む理由は、リポジトリ内の `plugins/dev-workflow/skills/commit-and-pr-message/SKILL.md` の「送る前の検査」節に従う。

読み込まれている `dev-workflow:commit-and-pr-message` へ委ねるだけにしないのは、消費側がこの skill を SHA で pin しているためである。読み込まれるのは pin の時点の版で、このリポジトリの版より古いことがあり、入口を持たない版もある。入口も手順も、このリポジトリの作業ツリーにある版を使う。

```bash
git tag -a <版> -F .cache/tag-<版>.txt
git tag -n99 -l <版>
git push origin <版>
```

tag の push は ruleset に妨げられない。`protect-main` の target は branch なので、tag は対象外である。

### 4. GitHub release を作る

release note も手順 3 と同じ検査を通す。理由も同じで、この面を見る機会がここにしか無い。タイトルは `.cache/notes-<版>.title` に 1 行で書き、ノートと一緒に 1 回の呼び出しで通す。

```bash
python3 plugins/dev-workflow/skills/commit-and-pr-message/scripts/check-outgoing-text.py .cache/notes-<版>.md .cache/notes-<版>.title; echo "rc=$?"
```

```bash
test -s .cache/notes-<版>.title && gh release create <版> --title "$(cat .cache/notes-<版>.title)" --notes-file .cache/notes-<版>.md
gh release view <版> --json name,body,tagName,url
```

タイトルの渡し方 (`test -s` を前に置く理由を含む) は、手順 3 で名指した SKILL.md の「ワークフロー」節の「渡す」と「C.4 その他の面」に従う。

作ったら、一覧の先頭が新しい release で、`Latest` が付いていることを見る。

```bash
gh release list --limit 3
```

release note に必ず入れるもの:

- 消費側の呼び出しが壊れる変更 (あれば冒頭に置く)
- 配布物の変更一覧。`docs/issues/` だけの変更は「同伴」として末尾にまとめる
- 前回 tag からの commit range

### 5. 消費側へ pin の更新を促す

このリポジトリは自分では消費側を書き換えない。release を切ったら、消費側 (dotfiles の `home/apm.yml`) で pin を新しい tag の SHA へ揃える。

消費側での手順は消費側が持つが、このリポジトリ側の約束として次の 2 つを守る。

- pin は SHA で書く。tag では書かない
- pin の行末に対応する tag を注記する。SHA だけでは人が版を読めないため

注記は値の再掲だが、canonical (tag) が別リポジトリにあり、消費側から機械的に引けないので許容する。逆に「消費側の lockfile が version を持つから注記は不要」と読むのは誤り。lockfile が持つのは plugin 個別の版で、リポジトリの tag とは別の数列である。

促したら、消費側の確認やリリースを待っている Issue を探す。消費側でしか確かめられないタスクを持つ Issue は、release を切っても自分では閉じられず、戻る手順が無いと in_progress のまま残る。そうした Issue は、未チェックの箱を残したまま PR がマージされたときに in-repo-issue の Phase C.3 が `in_progress` にしたものなので、まず status で絞り、未チェックの箱の行に語が現れるものを拾う。箱の形は C.3 の判定と同じもので、その書き方の理由は C.3 が持つ。

```bash
find docs/issues -mindepth 2 -maxdepth 2 -name issue.md -not -path '*/templates/*' \
  -exec grep -qx 'status: in_progress' {} \; \
  -exec grep -lE '^[[:blank:]]*[-*+] \[( |　)\].*(消費側|リリース)' {} \;
```

見つかった Issue は本文を読み、この release で確かめられるタスクを消費側への連絡に含める。確認が返ってきたら、その Issue を閉じる。

## 落とし穴

- 「tag で pin すれば版が読める」: tag は動かせる。annotated でも `pre-commit autoupdate` 相当の操作で参照が変わりうる。pin は SHA のまま
- 「CI が success だから全部通った」: job 名に現れない検査がステップとして埋まっている。ステップまで降りて数える
- 「plugin.json の version を tag に揃える」: 別の数列である。plugin 個別の版と、コレクション全体の版は独立に動く
- 「`docs/issues/` を直したから release」: 配布物が変わっていない。消費側が pin を上げる理由が無い
- 「release note は commit を並べれば足りる」: 消費側が知りたいのは「自分の呼び出しが壊れるか」。版の番号からは読めないので、壊れる変更を冒頭に置く
- 「gitleaks が全履歴で緑だからタグ本文も見られている」: 全履歴の走査が見るのはファイル内容だけである。タグ本文・コミットメッセージ・author はいずれも面の外 (実測)。手順 3 と 4 で入口を通さないと、この面は誰も見ない
- 「読み込まれている commit-and-pr-message に従えば検査も済む」: 読み込まれるのは消費側の pin の版で、入口を持たないことがある。入口と「送る前の検査」節は、このリポジトリ内のパスで使う

## 関連

- `dev-workflow:commit-and-pr-message`: tag メッセージと release note の渡し方の canonical。送る前の検査の行動は、このリポジトリ内の同 skill の SKILL.md を読む (手順 3)
- `dev-workflow:in-repo-issue`: 手順 5 で探す Issue の status の遷移と、未チェックの箱の判定 (Phase C.3)
- `dev-workflow:git-branch-switcher`: 作業前のブランチ選択
- `dev-workflow:pre-merge-quality-gate`: release へ含める変更をマージする前のゲート
