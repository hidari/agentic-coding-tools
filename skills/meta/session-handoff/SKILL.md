---
name: session-handoff
description: セッションの作業状態を引き継ぎ書 <リポルート>/.cache/handoff.md に書き出し、新しいセッションへ引き継ぐ。発動経路は3つ。(1) hook からのコンテキスト超過通知を受けたとき (2) hook からのツール呼び出し破損通知を受けたとき (3) ユーザーが手動で依頼したとき (「引き継ぎ書いて」「handoff して」「セッション切り替えたい」等)。新セッション側の読み込みは SessionStart hook (handoff-sentinel) が自動で行うため、このスキルは書き出しと案内までが責務。
---

# Session Handoff

セッションの作業状態を引き継ぎ書に外部化する。書き出した handoff.md は
次のセッションの開始時に hook が自動注入するので、読み込み側の作業は不要。

## 手順

1. `git rev-parse --show-toplevel` でリポルートを解決する (リポ外なら cwd を使う)
2. `<ルート>/.cache/` を `mkdir -p` で確保する
3. このスキルと同じディレクトリの `template.md` を読み、各セクションを
   コメントのガイドに従って埋め、`<ルート>/.cache/handoff.md` に書き出す
   (既存の handoff.md があれば上書きする。最新の引き継ぎが常に正)
4. リポルートを cwd にして `python3 "$HOME/.claude/hooks/handoff-sentinel.py" record` を実行し、
   provenance を記録する (この記録が無いと新セッションで注入されない = fail-closed。
   第三者作成の handoff.md を信頼された引き継ぎとして注入しない prompt injection 防御)
5. 発動経路に応じて締める:
   - コンテキスト超過通知: 「引き継ぎを書き出した。/clear するか新セッションを
     開いて再開して。開始時に自動で引き継がれる」と案内し、以後の作業を打ち切る
   - ツール呼び出し破損通知: 「引き継ぎを書き出した。Claude Code の再起動を推奨」
     と案内し、以後の作業を打ち切る
   - 手動依頼: 書き出した旨とファイルパスだけ報告する

## 書き方の要点

- 埋める内容の構造は template.md が canonical。セクションの追加・削除はしない
- しきい値などの検知条件は hook (handoff-sentinel.py) が canonical。ここには書かない
- 具体的で実行可能な「次の一手」を最優先で書く (再開後のセッションが最初に読む)
