---
name: session-handoff
description: セッションの作業状態を引き継ぎ書 <リポルート>/.cache/handoff.md に書き出す。hook (handoff-sentinel) の通知がこの skill を名指ししたとき、またはユーザーが手動で依頼したとき (「引き継ぎ書いて」「handoff して」「セッション切り替えたい」等) に使う。何を検知して通知するかは hook 側が持ち、ここでは数えない。書き出した引き継ぎ書を次のセッションへ載せるのは対になる SessionStart hook の責務で、hook が配線されていない環境では自動注入は起きない。このスキルは書き出しと案内までを持つ。
---

# Session Handoff

セッションの作業状態を引き継ぎ書に外部化する。この skill が持つのは書き出しまで。
次のセッションへ載せるのは対になる hook (handoff-sentinel) の責務で、hook はこの skill に同梱されない (Claude Code の設定側の資産で、配布経路が別)。hook が無い環境でも引き継ぎ書を書く価値は変わらないが、自動注入は起きないので次のセッションで `.cache/handoff.md` を手で読ませる。

## 手順

1. `git rev-parse --show-toplevel` でリポルートを解決する (リポ外なら cwd を使う)
2. `<ルート>/.cache/` を `mkdir -p` で確保する
3. このスキルと同じディレクトリの `template.md` を読み、各セクションをコメントのガイドに従って埋め、`<ルート>/.cache/handoff.md` に書き出す (既存の handoff.md があれば上書きする。最新の引き継ぎが常に正)
4. 読み込み側の hook が取り付けてあるかを確かめてから provenance を記録する。hook はこの配布物に含まれないので、有ることを前提にしない
   - Claude Code の settings.json (user scope。project scope の `.claude/settings*.json` に置く運用ならそちらも) の `hooks.SessionStart` に `handoff-sentinel.py` を呼ぶ command があるかを確認する。実体の有無ではなく登録の有無で判定するのは、実体があっても登録が無ければ読み手が居ないため
   - 有れば、その command が指すパスをそのまま使い、リポルートを cwd にして `python3 <そのパス> record` を実行する。終了コードが 0 でなければ「取り付け無し」として扱う (登録はあるが実体が無い形がこれで落ちる)
   - 無ければ record を実行せず「取り付け無し」として 5 へ進む。読み手の居ない provenance を書いても効かない
   - 記録が無いと hook は注入しない (fail-closed)。第三者が置いた handoff.md を信頼された引き継ぎとして注入しないための防御で、規則と record が何を見るかは hook 側が canonical
5. 締めは呼び出し元に従う:
   - hook の通知で呼ばれた: 通知の文面が求める行動に従う。行動の canonical は通知側にあり、ここに再掲しない
   - 手動依頼: 書き出した旨とファイルパスだけ報告する
   - どちらの経路でも、4 が「取り付け無し」だったときは「自動では引き継がれない。次のセッションで `.cache/handoff.md` を読ませること」を添える

## 書き方の要点

- 埋める内容の構造は template.md が canonical。セクションの追加・削除はしない
- 検知条件・発動経路・注入の規則は通知を出す hook が canonical で、この skill は受ける側なのでここには書かない。例外は手順 4 が依存する fail-closed の一点だけで、そこでも規則の中身は hook を指している。その hook はこの配布物の外にある (所在と取り付けの確認は手順 4)。ここに値を写すと、hook の無い配布先では写した値だけが残って canonical に見える
- 具体的で実行可能な「次の一手」を最優先で書く (再開後のセッションが最初に読む)
