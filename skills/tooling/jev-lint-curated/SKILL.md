---
name: jev-lint-curated
description: pin した版の jev-lint を、複数のコードベースの実測から選んで固定した rule の一覧だけで check / review / compat 実行したいときに使う。rule の新規作成や cutoff の較正、一般の jev-lint 運用は上流の skill jev-lint を使うこと。
---

# jev-lint 厳選ラッパ

pin した版の jev-lint を、複数のコードベースの実測から選んで固定した rule の一覧だけで動かす薄いラッパ。
一覧はどの消費側リポジトリでも変えられず、消費側が調整できるのは対象のパス、`--exclude`、厳選した rule に限った `--threshold` の値だけである。
版と厳選した rule の一覧は `scripts/jevlint.py` が唯一の場所として持ち、check・review・compat の 3 つのサブコマンドを提供する。

## いつ使うか

コミット済みのコードの名前・コメント・docstring が中身と食い違っていないかを、人やエージェントが判断材料として advisory に確かめたいとき。
手動実行に限る。pre-commit にも CI にも組み込まない。
rule の新規作成や cutoff の較正、一般の jev-lint 運用は上流の skill jev-lint を使うこと。ここではその内容を再掲しない。

## 呼び出しの形

```
python3 -E -s -B "${CLAUDE_SKILL_DIR}/scripts/jevlint.py" <サブコマンド> ...
```

`-E` を使うのは、消費側リポジトリにコミットされた Claude Code のプロジェクト設定の `env` が `PYTHONPATH` などをこのラッパ自身のプロセスへ注入しうるためである。
`-s` は利用者の site-packages をこのラッパのプロセスから外す。
`-B` は、`-E` が `PYTHONDONTWRITEBYTECODE` も無視するため、兄弟モジュールの import のたびに skill の置き場へ `scripts/__pycache__/` が書かれるのを止める (実測: Python 3.14.7)。
`-I` は使わない。Python 3.11 以降 `-I` は `-P` を含意し、スクリプトのディレクトリが `sys.path` から落ちて兄弟モジュール (`jevlint_tree` など) の import が壊れる。

## エージェントが自分で実行してよい範囲

キーを使わないサブコマンドに限る。

- `check --dry-run <path>...`
- `review --dry-run --base <ref> [<path>...]`
- `compat <版>`

これ以外 (`--dry-run` を付けない `check` / `review`) はコードの中身を上流へ送るため、キーを持つユーザーが `!` で実行する。エージェントはキーを読む・表示する・export する・ユーザーに尋ねる、いずれも行わない。この境界はラッパの外側にある裁定なので、迂回する手段を探さないこと (環境によっては分類器がエージェント側のキー取り扱いを止めることもあるが、それを前提にはしない)。

ユーザーに促す形は次のプレースホルダで示し、保管庫のパスやアカウント名のような個人の値は書かない。

```
! TYPESAFE_API_KEY="$(<キーを取り出すコマンド>)" python3 -E -s -B "${CLAUDE_SKILL_DIR}/scripts/jevlint.py" check <path>
```

キーはこの 1 回の起動にだけ渡り、export はしない。

## 前提環境

git は `scripts/jevlint_tree.py` の `_MIN_GIT_VERSION` が要求する版以上が要る。満たない git は `check` / `review` の最初の git 呼び出しで拒否される (`compat` は本体のリポジトリの git を呼ばないのでこの検査を通らない)。
node の版は host に取得した jev-lint の `package.json` の `engines` を実行時に読んで判定する。数値はここに書かない。
pnpm は host (上流本体) を取得するときだけに要る。起動そのものは node が直接行い pnpm を経由しない。

## 送る範囲と送らないもの

送るのは指定したコミットの中身だけである。git の blob から生のバイトを書き出した一時 worktree の中で実行し、hook・filter・symlink の復元・lazy fetch・`refs/replace` の差し替えは働かない。

- `review` の上流は worktree の中で自分の `git diff` を利用者の git 設定で呼ぶため、コミットされた `.gitattributes` が選ぶ textconv の driver がキーを含む env で起動しうる。この限界はラッパの側では塞げない (詳細は `scripts/jevlint.py` の `main` の docstring)
- `--exclude` は判定する対象を絞るだけで、paired の arm が読む慣例のテストディレクトリの抜粋 (指定したパスの外にあってもよい) には効かない
- 上流の env に proxy の変数は渡さない
- host の取得の段では `NODE_OPTIONS` なども効かない (落とす変数と、proxy のように残す変数の理由は `scripts/jevlint_host.py` の `install_env` の docstring)
- Claude Code のプロジェクト設定の `env` は、プロジェクトの hooks と同じ信頼度で扱う。相対な `PATH` / `XDG_CACHE_HOME` / `HOME` はラッパが弾き、host の取得の段ではパッケージマネージャの設定変数も落とすが、信頼したプロジェクトが絶対パスの値で仕込む経路までは防げない
- MoonBit 向けの rule は一覧に載っているが、parser が無いので走らない (別 Issue で扱う)
- submodule の中身は書き出さない
- 採点は非決定的で、cutoff の近くでは判定が実行ごとに入れ替わりうる

## 結果の読み方

終了コードの意味は `scripts/jevlint.py` のモジュール docstring が唯一の場所なので、ここでは数値を再掲しない。
「0 件」を信じる前に、要約に出る「見た対象」と「未回答」の件数を見て、検査が実際に何件見て何件答えが無かったかを確かめること。
finding は人かエージェントが判断する候補であって断定ではない。直すのはこのラッパの仕事ではなく、報告を見たあとのエージェントの仕事である。

## 版を上げる手順

1. `compat <新しい版>` をキー無しで回す
2. 報告された変化を上流の CHANGELOG と git の差分で読む
3. `scripts/jevlint.py` の `UPSTREAM_VERSION` を書き換える
4. 必要ならユーザーが `!` で `--json-out` を付けて測り直す。記録には cutoff 未満の答えも残るので、このリポジトリの外に持つ自分のラベルと突き合わせられる

## 後始末

実行が中断されると worktree の登録が残ることがある。後始末の git が失敗して残したときは、ラッパが stderr でそう告げる。どちらも本体のリポジトリで `git worktree prune` を実行して消す。
