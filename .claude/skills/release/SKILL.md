---
name: release
description: このリポジトリ (agentic-coding-tools) のリリースを切る。annotated tag と GitHub release を作り、消費側へ pin の更新を促すところまでを持つ。「リリース切って」「タグ打って」「release して」と指示された時、および pin を上げる目的で消費側から参照される版を確定させたい時に使う。バージョン規則・事前検査・切ったあとの後始末を持ち、本文の渡し方は dev-workflow:commit-and-pr-message に委ねる。
---

# Release (agentic-coding-tools)

このリポジトリは apm 経由で skill と plugin を配る。消費側は **commit SHA で pin する**ので、
tag は固定の機構ではない。tag と release は「その SHA で何が変わったか」を
レビュー可能な単位にするための器である。

この非対称を最初に押さえること。tag を pin の機構と誤解すると、
`rev` や `#<ref>` を tag に置き換えたくなるが、それは可変参照への退行になる。

## 何が release になるか

消費側が pin を上げる判断をする単位。つまり**配布物が変わったとき**である。

`docs/issues/` だけの変更は配布物を変えないので、単独では release にしない。
ただし配布物の変更に同伴するのは構わない。

## バージョン規則

pre-1.0 のあいだは次のとおり。

| 変更 | bump |
| --- | --- |
| 消費側の呼び出しが壊れる (skill 名の変更、登録経路の変更、command の撤去) | minor |
| 配布物の追加、既存の挙動を壊さない変更 | minor |
| 誤字修正、内部リファクタ、配布物に届かない変更 | patch |

1.0 以降は通常の semver へ移す。移した時点でこの表を書き換える。

`plugins/*/.claude-plugin/plugin.json` の `version` は **plugin 個別の版**であって、
リポジトリの tag とは別の数列である。両者を揃えようとしないこと。
消費側の lockfile が記録するのは plugin 個別の版で、
単体 skill には版の概念が無いため `unknown` が入る。

## 手順

### 1. 事前検査

main が最新で clean であることを確かめ、検査を全部通す。

```bash
git checkout main && git pull
git status --porcelain
python3 scripts/run-python-tests.py
python3 plugins/dev-workflow/skills/in-repo-issue/scripts/issue-id.py --check
python3 scripts/check-related-refs.py
python3 scripts/check-package-shape.py
python3 scripts/gen-readme.py --check
```

CI も main の最新 SHA で確認する。**run の success だけでは足りない。**
このリポジトリの CI は 4 job だが、Issue 記法と関連節の検査は
「パッケージの形と命名規約」job の**中のステップ**なので、job 名一覧には現れない。
job 名は job が何をするかの申告であって、何を実行したかの申告ではない。

```bash
sha=$(git rev-parse HEAD)
gh run list --commit "$sha" --json name,status,conclusion
gh run view <run-id> --json jobs --jq '.jobs[] | .steps[] | "\(.conclusion) \(.name)"'
```

### 2. 前回の release からの差分を読む

```bash
git log --oneline --first-parent <前回のtag>..HEAD
git diff --stat <前回のtag>..HEAD -- plugins/ skills/
```

2 つ目で配布物の差分だけを見る。ここが空なら release にしない (上の「何が release になるか」)。

### 3. annotated tag を打つ

本文はファイル経由で渡す。理由と書式の canonical は `dev-workflow:commit-and-pr-message`。

**渡す前に禁止語の検査へ通す。** タグ本文は `gitleaks` の走査面の外にあり (ISSUE-15 の実測)、
コミットメッセージを見る hook も届かない。この面を見る機会はこの手順にしか無いので、飛ばすと
誰も見ないまま公開される。しかも push した後で本文を直すには打ち直すしかなく、参照が変わる。

```bash
python3 scripts/check-leak-guard-denylist.py --check-text .cache/tag-v<X.Y.Z>.txt
```

検査が skip された状態は緑ではない。禁止語リストが未設定の環境では静かに通るので、
設定してから打ち直すこと (入口と出力の canonical はスクリプトの docstring)。

この検査が見るのはリストに載っている語だけである。**載っていないものは通る**ので、本文は
自分で読んでから渡す。特に、個々は公開してよい語なのに列挙の組み合わせが対象を特定する形と、
それ自体は固有名詞を含まないまま他の記述の読み方を変えるメタ記述は、リストでは原理的に
捕まらない。

```bash
git tag -a v<X.Y.Z> -F .cache/tag-v<X.Y.Z>.txt
git tag -n99 -l v<X.Y.Z>
git push origin v<X.Y.Z>
```

tag の push は ruleset に妨げられない。`protect-main` の target は branch なので、
tag は対象外である。

### 4. GitHub release を作る

release note も手順 3 と同じ検査を通す。理由も同じで、この面を見る機会がここにしか無い。

```bash
python3 scripts/check-leak-guard-denylist.py --check-text .cache/notes-v<X.Y.Z>.md
```

```bash
gh release create v<X.Y.Z> --title "<1 行>" --notes-file .cache/notes-v<X.Y.Z>.md
gh release view v<X.Y.Z> --json body,tagName,url
```

`--title` はインラインなので句点を入れない。

release note に必ず入れるもの:

- 消費側の呼び出しが壊れる変更 (あれば冒頭に置く)
- 配布物の変更一覧。`docs/issues/` だけの変更は「同伴」として末尾にまとめる
- 前回 tag からの commit range

### 5. 消費側へ pin の更新を促す

このリポジトリは自分では消費側を書き換えない。release を切ったら、
消費側 (dotfiles の `home/apm.yml`) で pin を新しい tag の SHA へ揃える。

消費側での手順は消費側が持つが、**このリポジトリ側の約束**として次の 2 つを守る。

- pin は SHA で書く。tag では書かない
- pin の行末に対応する tag を注記する。SHA だけでは人が版を読めないため

注記は値の再掲だが、canonical (tag) が別リポジトリにあり、
消費側から機械的に引けないので許容する。
逆に「消費側の lockfile が version を持つから注記は不要」と読むのは誤り。
lockfile が持つのは plugin 個別の版で、リポジトリの tag とは別の数列である。

## 落とし穴

| 思考の罠 | 実態 |
| --- | --- |
| 「tag で pin すれば版が読める」 | tag は動かせる。annotated でも `pre-commit autoupdate` 相当の操作で参照が変わりうる。pin は SHA のまま |
| 「CI が success だから全部通った」 | job 名に現れない検査がステップとして埋まっている。ステップまで降りて数える |
| 「plugin.json の version を tag に揃える」 | 別の数列である。plugin 個別の版と、コレクション全体の版は独立に動く |
| 「`docs/issues/` を直したから release」 | 配布物が変わっていない。消費側が pin を上げる理由が無い |
| 「release note は commit を並べれば足りる」 | 消費側が知りたいのは「自分の呼び出しが壊れるか」。壊れる変更を冒頭に置く |
| 「gitleaks が全履歴で緑だからタグ本文も見られている」 | 走査するのはファイル内容だけである。タグ本文・コミットメッセージ・author はいずれも面の外 (ISSUE-15 の実測)。手順 3 と 4 の検査を飛ばすと、この面は誰も見ない |

## 関連

- `dev-workflow:commit-and-pr-message`: tag メッセージと release note の渡し方の canonical
- `dev-workflow:git-branch-switcher`: 作業前のブランチ選択
- `dev-workflow:pre-merge-quality-gate`: release へ含める変更をマージする前のゲート
