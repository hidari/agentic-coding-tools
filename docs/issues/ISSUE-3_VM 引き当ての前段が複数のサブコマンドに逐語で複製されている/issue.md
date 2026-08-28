---
status: open
---

# refactor: VM 引き当ての前段が複数のサブコマンドに逐語で複製されている

## 背景

`prlctl` を使うサブコマンドは、本題に入る前に同じ前段を踏む。

1. `--vm` (または `WINVM_VM`) の有無を確かめる
2. `_load_vms` で一覧を取る
3. `find_vm` で該当を引く
4. 見つからなければ登録済みの名前一覧を添えて落とす

この 16 行が `cmd_resolve_ip` (196 行) と `cmd_screenshot` (388 行) で**逐語同一**になっている。

```python
    vm_id = _env_or(args.vm, "WINVM_VM")
    if not vm_id:
        print("error: --vm (または WINVM_VM) が必要です", file=sys.stderr)
        return 2
    vms, err = _load_vms(run)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    vm = find_vm(vms, vm_id)
    if vm is None:
        print(
            f"error: VM が見つかりません: {vm_id} / 登録済み: {_known_names(vms)}",
            file=sys.stderr,
        )
        return 1
```

`cmd_doctor` にも同種の引き当てがあるので、実装時に合わせて確認する。

### なぜ問題か

エラーメッセージと exit code の対応 (引数不備は 2、引き当て失敗は 1) が複数箇所に散っている。
片方だけ直すと、同じ失敗がサブコマンドによって違う出方をする。ユーザーから見ると
「screenshot では登録済み一覧が出るのに resolve-ip では出ない」のような差になるが、
テストは各コマンド個別に書かれているのでどちらも緑のまま通る。

今は 2 箇所なので実害は小さい。prlctl を使うサブコマンドが 3 つ目に増えるときが分岐点で、
そのとき初めて直すと 3 箇所の突き合わせが要る。

## タスク

- [ ] 共通ヘルパの形を決める (`_lookup_vm(vm_id, run) -> tuple[dict | None, int]` 相当か、
      例外か、既存の `_load_vms` が返す `(vms, err)` 形に揃えるか)
- [ ] `cmd_resolve_ip` / `cmd_screenshot` / `cmd_doctor` を新ヘルパへ寄せる
- [ ] 引数不備が 2、引き当て失敗が 1 という exit code の対応が全コマンドで同じであることを
      1 箇所でテストする (今はコマンドごとに個別 pin)
- [ ] 変異注入で確認する (exit code を入れ替えると当該テストが赤くなること)
- [ ] 前段が IP の分類 (正常 / APIPA / 未取得) まで返すべきかを判断する。ISSUE-11 で
      `is_apipa` へ共有したのは APIPA の判定だけで、3 分類から出力を選ぶ分岐は
      `cmd_resolve_ip` と `collect_doctor_checks` が今も独立に書いている。ただし
      `Check.ok=None` (観測できなかった) と「観測できた不在」は意味が違うので、
      両者を同じ型へ畳まないこと

## 関連

- `skills/devops/windows-vm-verification/winvm.py` — `cmd_resolve_ip` (196 行)、
  `cmd_screenshot` (388 行)、`cmd_doctor`
- 同ファイル `_load_vms` / `find_vm` / `_known_names` — 既にある部品
- 本 Issue は PR #3 のレビューで検出した。`cmd_resolve_ip` の制御フローに手が入るため
  同 PR には同梱せず別 Issue とした
