---
status: open
---

# gate の Phase 0 とレーンの差分が ASCII 以外のパスを 8 進のエスケープで出す

## 背景

`dev-workflow:pre-merge-quality-gate` の Phase 0 は、変更ファイル一覧にだけ `-c core.quotepath=off` を付けている。`--stat` と、コードレビューのレーンへ渡す `git diff <base>...HEAD` には付いていない。git の既定では ASCII 以外を含むパスが `"\347\224\273..."` の形の 8 進のエスケープで出るので、日本語のパスを持つリポジトリでは、レーンが受け取る変更概要と diff のヘッダが、変更ファイル一覧と同じパスに見えない (ISSUE-105 と ISSUE-106 の PR のゲートで、使い捨てのリポジトリで再現した)。

gate の散文は付ける範囲を「最初の行」と限っていて、宣言としては正しい。その PR は gate の SKILL.md を触っていないので、こちらで扱う。e2e-scenario-impact-check の SKILL.md は、その PR ですべての git diff に付けた。

## タスク

- [ ] gate の Phase 0 の `--stat` と、レーンの prompt で渡す git diff の形を、変更ファイル一覧と同じく `-c core.quotepath=off` を付けた形に揃える
- [ ] 揃えた後も、Phase 0 の説明の文と e2e skill からの参照 (理由は gate の Phase 0 が持つ) が食い違わないことを確かめる

## 関連

- ISSUE-106
- ISSUE-105
