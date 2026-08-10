---
status: open
---

# fix: push の remote パスがバックスラッシュ区切りだと親ディレクトリ作成が無言でスキップされる

## 背景

`winvm push --remote "C:\Users\Public\x.msi"` のようにバックスラッシュ区切りで渡すと、
親ディレクトリの作成が行われないまま scp に進む。親が存在しなければ scp が失敗するので
黙って壊れたデータが残るわけではないが、出るエラーが `error: scp 失敗: <local>` なので
「パスの区切りが原因」であることが読めない。

`remote_parent_mkdir_command` は `PurePosixPath` で親を取る。

```python
# winvm.py:766
parent = str(PurePosixPath(remote_posix).parent)
if parent in (".", "/") or re.fullmatch(r"[A-Za-z]:", parent):
    return None
```

`PurePosixPath` はバックスラッシュを区切りとして扱わないため、パス全体が 1 つの名前に
なって親が `.` になる。実測:

```
'C:/Users/Public/x.msi'   -> parent: 'C:/Users/Public'
'C:\\Users\\Public\\x.msi' -> parent: '.'
```

`.` はドライブ直下と同じガードに吸われて `None` を返し、mkdir が発行されない。

### 到達可能であること

`cmd_push` は `args.remote` を正規化せずそのまま渡す。

```python
# winvm.py:791
remote = args.remote
mk = remote_parent_mkdir_command(remote)
```

一方、同じ関数の 801 行はサイズ確認のために `to_windows_path(remote)` を通している。
**同一関数内で正規化の有無が割れている** のがこの問題の実体で、mkdir 側にだけ正規化が
無い。

`remote_parent_mkdir_command` の docstring は「remote パス (/ 区切り)」と書いており
契約としては `/` 区切りを要求しているが、`SKILL.md` は `/` で書けるとしか書いておらず
`\` を禁じてはいない。CLI の利用者が Windows のパスをそのまま貼るのは自然なので、
契約で守るのではなく入力側で正規化するのが妥当と考えられる。

## タスク

- [ ] 正規化をどこで行うか決める (`cmd_push` の入口で `to_windows_path` の逆変換をかけるか、
      `remote_parent_mkdir_command` が両方の区切りを受けるか)
- [ ] バックスラッシュ区切りの remote パスで親ディレクトリの mkdir が発行されることをテストで pin する
- [ ] `.` に落ちるケースと、ドライブ直下 (`C:/x`) で正しく `None` を返すケースが
      区別されていることを pin する (今は両方が同じ分岐に入っている)
- [ ] `cmd_pull` の remote 側にも同じ経路が無いか確認する
- [ ] 変異注入で確認する (正規化を外すと当該テストが赤くなること)

## 関連

- `skills/devops/windows-vm-verification/winvm.py` — `remote_parent_mkdir_command` (760 行)、
  `cmd_push` (782 行)
- 同ファイル `to_windows_path` — 既にある変換
