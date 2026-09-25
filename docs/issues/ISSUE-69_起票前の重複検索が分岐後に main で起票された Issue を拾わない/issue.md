---
status: open
---

# fix: 起票前の重複検索が分岐後に main で起票された Issue を拾わない

## 背景

`in-repo-issue` の Phase A.0 (起票前の内容の重複検索) は、`docs/issues` を作業ツリーで grep するだけになっている。feature ブランチで作業していると、分岐した後に main で起票された Issue は検索に入らない。採番の `--next` は全 ref を走査するのに、重複検索だけが現ツリーで止まっている。

実例 (2026-09-25): ISSUE-58のブランチで ISSUE-67を起票したとき、同じ判定の免除の側を扱う ISSUE-63が main にしか無く、A.0で拾えなかった。マージ前ゲートの Phase 0で `origin/main..HEAD` の差分に見慣れないファイルが混ざっていたことで気づき、main を取り込んでから相互参照を足した。

main にしか無い Issue を `## 関連` で名指すには、先に main を取り込む必要もある。関連節の検査 (`scripts/check-related-refs.py`) は、識別子が実在するかを作業ツリーで見るため。

## 決めたこと (2026-09-25、ユーザー裁定)

- A.0は作業ツリーに加えて origin の default branch も引く
- 起票はすべて、起票だけの PR にして即マージする。feature の作業中に見つけたものも feature のブランチに同梱しない。main が常に Issue の全集合になり、A.0の検索は「origin の default branch + 作業ツリー」で閉じる

即マージを採らずに A.0を全 ref の走査にする案もあった。こちらはマージの手間は増えないが、未マージのブランチにある Issue は別のセッションや別のリポジトリから見えないまま残る。

## 直すときに決めること

- 検索の前に `git fetch` を置く。取り込んでいない古い origin の default branch で引くと、その0件は「重複なし」ではなく「見ていない」になる
- default branch の名前を手順に literal で書かない (skill の「クローズ経路」節と同じ理由)。origin の HEAD から引くか、`gh` から引く
- ファイル名に日本語を含むので `git -c core.quotePath=false grep` の形にする。検索語の渡し方は A.0の既存の注意 (主題語はタイトルの語をそのまま使わない) を保つ
- feature の作業中に起票するときの手順。いまのブランチを汚さないように、origin の default branch から worktree を切って起票する形が候補になる
- 本リポジトリの main は PR 必須の ruleset を持ち、`gh pr merge` は auto mode の classifier に止められる。即マージはユーザーが毎回マージを実行する前提になる。main への直 push を許すプロジェクトでは、直 push が即マージにあたる。配布する skill の文面は両方の場合に通じる形にする
- 起票だけの PR でも CI は PR とマージで回る。同時に見つけた複数の Issue は1本の起票 PR にまとめてよいか
- 起票だけの PR にマージ前ゲートをどこまで通すか
- 「起票は単独の PR」を検査に落とせるか。たとえば新しい Issue ディレクトリの追加と、それ以外の変更が同じ PR に同居していたら赤にする

## タスク

- [ ] A.0に、取り込んだうえで origin の default branch を引く手順を足す
- [ ] Phase A に「起票は起票だけの PR で即マージする」を足す。feature の作業中に起票する手順と、マージの後に feature 側で main を取り込む手順を含める
- [ ] 関連節の git-branch-switcher の記述と、クローズ経路の「feature PR 同梱を優先」との整合を取る。同梱はクローズの話で、起票には及ばないことを明確にする
- [ ] 起票の単独 PR を検査に落とせるかを判断し、落とせるなら入れる
- [ ] 分岐した後に main で起票された Issue を、A.0の新しい手順で拾えることを実例で確かめる。対照として、取り込む前の古い origin の default branch では拾えないことも並べる

## 関連

ISSUE-67 (feature のブランチで起票し、main にだけあった ISSUE-63を見落とした実例)
ISSUE-63 (見落とされた側)
ISSUE-58 (closed。見落としが起きたときに作業していた feature)
ISSUE-56 (closed。起票 PR のマージでも、squash の subject を明示する規約に従う)
