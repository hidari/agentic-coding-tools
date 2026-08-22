---
status: open
---

# test: cmd_run に注入点が無くコマンドレベルのテストが存在しない

## 背景

PR #3 のマージ前レビューで発見した。

`cmd_run` は winvm の中で最も複雑で、最も破壊的なコマンドである。VM 上で
`git checkout -- . && git clean -fd` を撃つ唯一の経路で、かつ

1. reset
2. 削除の同期
3. 親ディレクトリの作成
4. scp
5. リモート実行

という**順序そのものが仕様**になっている (SKILL.md が 5 ステップとして明文化している)。

にもかかわらず `cmd_run` はモジュールレベルの `ssh_capture` / `run_capture` / `git_local` /
`run_ssh` / `scp` を直呼びしており、シグネチャを変えない限りテストできない。
`test_winvm.py` に `CmdRun` クラスは存在しない。

新規追加した `cmd_screenshot` / `cmd_push` / `cmd_pull` / `cmd_exec` はすべて `run=` /
`copy=` / `capture=` を持つのに、既存で最も危ないものだけが持っていない。

### なぜ危険か

純粋ヘルパ (`files_to_sync` / `files_to_delete` / `remote_delete_commands` /
`parent_mkdir_commands` / `remote_reset_command`) は手厚くテストされている。そのため
**「テストがある」ように見える**。しかし覆えているのは文字列生成だけで、順序は 1 つも
pin されていない。

scp のループを reset より前に来るよう並べ替えると、同期したファイルが reset で全部
消える。この変更を入れても 144 件のテストは緑のまま通る。

## タスク

- [ ] `cmd_run(args, *, run=run_ssh_code, copy=scp, capture=ssh_capture, git=git_local)` に
      注入点を足す
- [ ] 5 ステップの順序をコマンドレベルで pin する (reset が scp より前であること)
- [ ] 各ステップの失敗が非 0 で返り、後続のステップが走らないことを pin する
- [ ] `resolve_diff_base` のフォールバック判定 (`git cat-file -e` のゲート) を
      `git=` 注入で pin する
- [ ] 変異注入で確認する。特に **scp ループと reset の順序を入れ替えて赤くなること**
      (今は緑のまま通る)
- [ ] full chain を live smoke する (`cmd_run` は cmd.exe 経路なので ISSUE-4 の
      pwsh 問題には阻まれない)

## 関連

- `skills/devops/windows-vm-verification/winvm.py` — `cmd_run` (542 行)
- `skills/devops/windows-vm-verification/SKILL.md` — `run` の 5 ステップ
- 本 Issue は PR #3 のスコープ外 (既存コード) だが、同 PR が新規 4 コマンドすべてに
  注入点を入れた結果、`cmd_run` だけが取り残された形になったので起票した
