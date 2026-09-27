---
status: closed
---

# fix: issue-id.py の --root にサブディレクトリを渡すと黙って配下だけを見る

## 背景

`issue-id.py` の `resolve_root` は、明示された `--root` を解決するだけで、そこがリポジトリの top-level かを確かめない。一方、Issue ディレクトリの列挙は root を起点に `docs/issues/` を相対で引くので、サブディレクトリを渡すと配下しか見ない。`--next --root <リポジトリ>/plugins` は既存と重複する識別子 (`ISSUE-1`) を終了コード 0 で返し、`--check --root <リポジトリ>/plugins` は「検査した Issue ディレクトリ: 0 個」「違反なし」の 0 になった (2026-09-27 に実測)。

pre-commit と CI は `--root` を渡さないので、今の実害はテストや手で起動したときに限られる。層 2 の `resolve_root` は、`git ls-files` が cwd 相対でサブディレクトリから起動すると配下しか返さないことを理由に、`git rev-parse --show-toplevel` で top-level を求めている。

着手時は、top-level でない `--root` を終了コード 2 で拒否する形にしていた。マージ前ゲートで拒否側の弱点が見つかり、2026-09-28 のユーザー裁定で、渡された場所から top-level を求め直す形 (層 2 と同じ) に変えた。拒否には `--root` と top-level が同じディレクトリかの比較が要る。大小を区別しない APFS では、大小を変えた `--root` を git は正規の綴りで返すので、文字列で比べると正しい root を拒否する (実測)。inode で比べればこれは避けられるが、その違いが出るのは大小を区別しない FS だけで、CI (Linux) では押さえられない。テストの runner は skip も赤にする。比較が投げる OSError も、借用する側の 2 本 (`scripts/check-related-refs.py` と `scripts/check-issue-closure.py`) で traceback の終了コード 1 になった (空の `--root`、名前が空白で終わるディレクトリ。実測)。正規化なら比較そのものが要らず、範囲は広がる向きにしか動かない。

## タスク

- [x] 明示された `--root` で `git -C <root> rev-parse --show-toplevel` を呼び、その結果を root にする。git でない root は rev-parse の失敗として終了コード 2 にする。rev-parse の出力から落とすのは git が足す改行だけにする (`strip()` は名前の末尾の空白まで落とし、存在しないパスになった。実測)
- [x] サブディレクトリを渡す形を `--next` と `--check` でテストし、`--next` が既存と重複しない識別子を返すことと、`--check` が全体の Issue ディレクトリを見ることを押さえる。名前が空白で終わるディレクトリもテストする。正規化をやめる変異と `strip()` へ戻す変異で赤くなることを確かめる
- [x] git でない root を渡す既存のテスト 2 本 (`--next` と `--check` の `test_non_git_root_is_exit_2`) の期待を rev-parse の失敗へ書き換える。これまでこの 2 本が押さえていた for-each-ref と ls-files の失敗の経路は、git でない root からは届かなくなるので、到達性を注入で作るテストへ移す。失敗を握りつぶす変異で赤くなることを確かめる
- [x] 同じ `resolve_root` を借りる 2 本の `--root` の help と、借りる側のテストの説明を、変わった挙動に合わせる

## 関連

ISSUE-77 (この経路を見つけたマージ前ゲートの対象。空の `--root` と繰り返しの `--root` はあちらで拒否した)
