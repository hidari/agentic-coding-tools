---
status: open
---

# fix: コミットと PR 本文にセッションの URL が入る

## 背景

Claude Code は、cloud と Remote Control のセッションでコミットや PR を作るとき、コミットの末尾に `Claude-Session:` の trailer を、PR 本文の末尾にリンクを付ける。どちらの値もセッションの URL である。制御するのは settings の `attribution.sessionUrl` で、既定は `true`。`false` にすると両方とも付かなくなる (公式の settings reference の `attribution.sessionUrl` の項)。付けるときは、harness が system reminder で指示してくる。

ユーザー設定 (dotfiles が持つ) の `attribution` は `commit` と `pr` を空文字にしてあった。そのため `Co-Authored-By` と PR の定型行は付かないが、`sessionUrl` が無いので既定の `true` のまま残っていた。本リポジトリのセッションでは reminder がこの指示を出しており、指示どおりに付いてきた。

同じ reference には、`attribution` を丸ごと `false` にする形もある。ただしこの値を受け付けない旧版の Claude Code は、その値を持つ settings ファイル全体を読み飛ばす (同 reference の `attribution` の項)。

## 確かめたこと (2026-09-25、origin/main が 66231ad の時点)

- main のコミット66件のうち53件がセッションの URL を持つ。52件は `Claude-Session:` で始まる行を持ち、その行は合計123行。最初のコミットから付いている
- 残る1件は `Claude-Session:` の行ではなく、本文の中に URL を持つ
- squash merge の本文には、ブランチの各コミットのメッセージがそのまま入る。PR #58の squash の本文だけで `Claude-Session:` の行が14行ある
- git の trailer の解釈 (`%(trailers:key=Claude-Session)`) で数えると、123行のうち trailer になるのは38件の38行だけだった。行頭一致で持つ52件のうち、全部の行が trailer になるのは25件、一部だけが13件、1行もならないのが14件。squash の本文では各コミットの行が本文の途中に並ぶため
- PR は状態を問わず58件あり、そのうち40件が本文にセッションの URL を持つ

## 決めたこと (2026-09-25、ユーザー裁定)

- コミットの trailer も PR 本文の URL も付けない
- 既に main に入ったものは書き換えない。消すには main の履歴の書き換えが要る
- 付けないことは、散文の指示ではなく設定で止める。dotfiles のユーザー設定の `attribution` に `sessionUrl: false` を足す。dotfiles のセッションへ依頼済みで、進め方は dotfiles が決める。CLAUDE.md には書かない。同じ制約が2箇所に分かれるため
- 設定が届かない場合の backstop として、本リポジトリの commit-msg の検査にも落とす。たとえばユーザー設定を読まない環境や、managed settings が上書きする環境

## 直すときに決めること

- commit-msg の検査の置き場。既存の commit-msg の hook は禁止語リスト (外部のリストを読む) と Issue の識別子の記法で、どちらも責務が違う。独立した hook にするか、gitleaks の custom ルールに足すか。gitleaks はコミットメッセージを走査しない (ISSUE-15の記録) ので、ルールに足すなら commit-msg の経路で本文を gitleaks に渡す形が要る。また custom ルールの config は `commit-and-pr-message` が配布する skill の一部なので、そこへ足すとこのリポジトリの裁定が配布先の既定の挙動にも及ぶ。独立した hook ならこのリポジトリに閉じる
- 検出の単位。上の実測のとおり git の trailer の解釈では squash の本文の途中の行を取りこぼすので、行頭一致か URL の形で見る
- 配布している `commit-and-pr-message` の文面。コミットの手順 (A.1)、PR の手順 (C.1)、フッタの既定の表、落とし穴の表が、harness の指示するセッションのフッタを付ける前提で書かれている。裁定のあとも残すと、配布物の手順が本リポジトリの検査に弾かれる形になる。フッタを既定から外すか、harness が指示したときだけ付ける形にするか。配布先には付けたい利用者もありうるので、どちらが配布物として正しいかも含めて決める
- 送る前の検査で止めるか。`commit-and-pr-message` の送る前の検査 (ISSUE-58) に形を足せば、コミットメッセージと PR 本文の両方を送る前に止められる。ただし同 skill の書き直しの規定は、harness が指示した行に当たったときは書き直さずにユーザーに聞き、harness の行を黙って落とさないとしている。付けないと決めた行が毎回当たって確認を求める形にならないよう、規定と揃える
- ルールの形。Issue の本文やテストの対照が trailer の名前や URL の形を説明として書く場合に当たらないようにする。検出すべき例と許可すべき例の両方を置く

## タスク

- [ ] commit-msg の検査を入れる。検出すべき例と許可すべき例の両方をテストに置き、squash の本文の途中に並ぶ形も検出すべき例に含める。変異注入で検査を外すと赤になることを確かめる
- [ ] `commit-and-pr-message` のフッタの記述 (A.1、C.1、フッタの既定の表、落とし穴の表) を裁定と矛盾しない形に直す。commit-msg の検査と同じ変更で入れる
- [ ] 送る前の検査で止めるかを決め、止めるなら書き直しの規定 (harness の行を黙って落とさない) と揃える
- [ ] dotfiles 側で `sessionUrl: false` が入ったあと、Remote Control のセッションで、reminder にセッションの URL の指示が出ないこと、実際のコミットと PR に付かないことを確かめる。起動し直さなくてよい
  - 根拠: 2026-09-25 に、読まれている settings のファイルへ `sessionUrl: false` を書き足した約30秒後、起動中のセッションの reminder が付けない側へ切り替わった (実測)。ただしこの観測は reminder だけで、実際のコミットと PR は見ていないので、このタスクの確認の代わりにはならない
  - 逆に、読まれる settings が一時的に `sessionUrl` を持たない版になった間は、reminder が付ける側へ戻った。dotfiles の作業ツリーで pre-commit の stash や `gh pr merge --delete-branch` の checkout が走った間である (dotfiles 側の報告)。観測はその間を避ける。この間に作るコミットと PR には harness の指示で付きうるので、決めたことの backstop が要る場面の1つでもある

## 関連

ISSUE-58 (closed。公開する本文を送る前の漏洩検査。GitHub 側で作られる squash の本文に検査が届かないことの記録がある)
ISSUE-15 (closed。PUBLIC リポジトリの露出の棚卸し。gitleaks がコミットメッセージを走査しないことの記録がある)
ISSUE-75 (commit-and-pr-message の落とし穴の表の言い直しの行を reference へ移すか消すかを決める。フッタの行はこちらのタスク2が直すので、あちらの候補から外してある)
