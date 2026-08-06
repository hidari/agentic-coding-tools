---
name: issue-scoped-artifacts
description: superpowers の brainstorming や writing-plans が spec / plan を書き出す直前に使う。成果物を docs/superpowers/ ではなく Issue ディレクトリ配下 (docs/issues/<NNN>_<title>/<NNN>-spec.md と <NNN>-plan.md) へ置く規約と、採用手順・移行手順を持つ。プロジェクトの CLAUDE.md にこの skill を指すポインタがある場合にのみ適用し、ポインタが無いプロジェクトでは何もせず既定の置き場に従う。
---

# Issue-Scoped Artifacts

## 適用条件

本 skill が適用されるのは、作業対象プロジェクトの CLAUDE.md に本 skill (`dev-workflow:issue-scoped-artifacts`) を指すポインタが存在するときだけである。

適用前に必ず次の順で確認する。

1. プロジェクトの CLAUDE.md を読み、`issue-scoped-artifacts` という文字列を含む行を探す
2. 見つからなければ、本 skill は何もしない。superpowers の brainstorming / writing-plans の既定の置き場 (`docs/superpowers/specs/` / `docs/superpowers/plans/`) にそのまま従う。ファイルの作成・移動、CLAUDE.md や `.pre-commit-config.yaml` の編集は一切行わず、「本プロジェクトに issue-scoped-artifacts のポインタが無いため既定の置き場に従います」とだけ伝えて終わる
3. ポインタが見つかった場合のみ、以下の規約を適用する

副作用ゼロで終わることが本節の要件である。ポインタの有無を確認する 1 手順だけで判断がつき、それ以外の探索や推測は行わない。

## 規約

Issue ディレクトリ配下に置くファイルは次のとおり。

| ファイル | 必須/任意 | 書き手 |
|---|---|---|
| `docs/issues/<NNN>_<title>/issue.md` | 必須 | `dev-workflow:in-repo-issue` |
| `docs/issues/<NNN>_<title>/<NNN>-spec.md` | 任意 | `superpowers:brainstorming` |
| `docs/issues/<NNN>_<title>/<NNN>-plan.md` | 任意 | `superpowers:writing-plans` |
| `docs/issues/<NNN>_<title>/notes/<name>.md` | 任意 | 手動 |

`<NNN>` はディレクトリ名先頭の番号と一致させる。

## なぜ番号を前置するか

番号を前置すれば、期待されるファイル名はディレクトリ名 (`<NNN>_<title>`) の純粋関数になる。ディレクトリ名の番号を読むだけで、そこに置くべき `<NNN>-spec.md` / `<NNN>-plan.md` が一意に決まり、機械的に検査できる。

これに加えて、番号前置は sdd (subagent-driven-development) の衝突も避ける。sdd の workspace 名は plan ファイルの basename から導出される観測可能な挙動を持ち (`sdd-workspace` は `basename "$plan" .md` で workspace 名を決める)、全 Issue で plan ファイル名を `plan.md` にすると全 Issue の workspace が `.superpowers/sdd/plan/` へ集中し、上流が「plan ごとのサブディレクトリ化」で潰したばかりの衝突を再現してしまう。ファイル名に番号を前置して `<NNN>-plan.md` にすれば basename が大域一意になり、この衝突は起きない。

自己完結する前者の理由を主とするのは、sdd の workspace 名の導出方法が上流の実装詳細であり、将来 sdd の命名規則が変わってもファイル名がディレクトリ名の純粋関数であるという性質自体は無傷で残るためである。

## 起票のタイミング

Issue を起票するのは spec を書き出す直前である。ブレインストーミングの対話フェーズ自体は Issue を必要としない。対話が終わるまでタイトルもスコープも確定しておらず、探索の結果「作らない」と決まることもあるため、対話開始時点で起票すると空の Issue が残ってしまう。

既に対応する Issue がある作業 (その Issue に追加の spec / plan を書くだけの回) では、新規起票せずその Issue をそのまま使う。

## 採用手順

プロジェクトへ opt-in するには次の 3 手順を行う。

1. プロジェクトの CLAUDE.md に次のポインタを 1 行足す

```markdown
- superpowers の spec / plan は `dev-workflow:issue-scoped-artifacts` skill の規約に従って Issue ディレクトリ配下へ置く
```

2. `.pre-commit-config.yaml` に次の hook を足す。既に `repo: local` エントリがあるプロジェクトでは、その `hooks:` 配下に `- id:` から下だけを足す

```yaml
  - repo: local
    hooks:
      - id: issue-scoped-artifacts
        name: spec と plan は Issue ディレクトリ配下へ置く
        language: fail
        entry: "この成果物は docs/issues/<NNN>_<title>/<NNN>-spec.md または <NNN>-plan.md へ置く"
        files: '^docs/superpowers/(plans|specs)/'
```

3. `.gitignore` に `.superpowers/` を足す

(3) が必要な理由は、補助スクリプトが自動生成する ignore の範囲が揃っていないため。subagent-driven-development の補助スクリプトは `.superpowers/sdd/.gitignore` を自動生成するが、brainstorming の visual companion が書く `.superpowers/brainstorm/` は ignore 対象にならず `git status` に出続ける。`.superpowers/` をプロジェクト側で ignore すればこの非対称を一括で吸収できる。

## 検出の範囲

本 hook が捕捉するのは「CLAUDE.md の上書きが効かず、成果物が上流 skill の既定パスへ落ちる」という失敗モードだけである。既定パスは `docs/superpowers/plans/` と `docs/superpowers/specs/` の 2 つしか存在しない (superpowers 6.2.0 時点) ため、この失敗モードは漏れなく捕捉される。

一方で、Issue ディレクトリ配下に置かれたファイルの名前違反 (番号の無い `spec.md`、`15_` 配下に置かれた `16-spec.md` のような番号不一致) は検出しない。ファイル名まで検証するにはディレクトリ名から番号を抽出するようなリポジトリ固有のロジックが必要になり、「全プロジェクトで同一の hook」という性質を失う。したがって意図的に見送っている。

## language: fail を選ぶ理由

移植性のため。`language: fail` は `ENVIRONMENT_DIR` が `None` で `install_environment` が no_install になり、環境構築が発生しない。Python も Node もインストール済みである必要がない。

実行可能スクリプトを配って呼び出す方式は次の 3 点で塞がっているため採らない。

- plugin の実体パスをシェルから解決する手段が無い (`CLAUDE_PLUGIN_ROOT` はシェルに export されない)
- 絶対パス直書きは gitleaks の macos-user-path ルールに抵触する
- pre-commit の外部 repo 参照は private リポジトリの clone 認証で詰まる

## 既存プロジェクトの移行手順

1. 対応する Issue が 1 対 1 で明確な成果物だけを `git mv` で Issue ディレクトリ配下へ移す
2. 対応が曖昧なものは判断を要するので `docs/superpowers/archive/` へ退避する。判断を要する対応付けは、忘れが検出できない人手のリンクと同じ性質を持つため、機械的に移せるものとは明確に分ける
3. `docs/superpowers/plans/` と `docs/superpowers/specs/` を空にする
4. 移行で切れる参照を探す。Markdown リンクはリンクチェッカーが守るが、コード内のコメントや文字列のようなリンクでない参照はリンクチェッカーの対象外なので、`git grep` で旧パスが残っていないか確認する

## 関連

- `dev-workflow:in-repo-issue`: Issue の起票・更新・クローズ。補助資料をディレクトリ内に置いてよいという規定を持つ
- `superpowers:brainstorming` / `superpowers:writing-plans`: 出力先を規定し、ユーザー設定による上書きを明示的に許可している
- `superpowers:subagent-driven-development`: workspace 名の導出元
