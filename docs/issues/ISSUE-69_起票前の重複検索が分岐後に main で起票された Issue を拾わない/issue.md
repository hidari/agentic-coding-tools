---
status: open
---

# fix: 起票前の重複検索が分岐後に main で起票された Issue を拾わない

## 背景

`in-repo-issue` の Phase A.0 (起票前の内容の重複検索) は、`docs/issues` を作業ツリーで grep するだけになっている。feature ブランチで作業していると、分岐した後に main で起票された Issue は検索に入らない。採番の `--next` は全 ref を走査するのに、重複検索だけが現ツリーで止まっている。

実例 (2026-09-25): ISSUE-58のブランチで ISSUE-67を起票したとき、同じ判定の免除の側を扱う ISSUE-63が main にしか無く、A.0で拾えなかった。マージ前ゲートの Phase 0で `origin/main..HEAD` の差分に見慣れないファイルが混ざっていたことで気づき、main を取り込んでから相互参照を足した。

main にしか無い Issue を `## 関連` で名指すには、先に main を取り込む必要もある。関連節の検査 (`scripts/check-related-refs.py`) は、識別子が実在するかを作業ツリーで見るため。

追記 (2026-10-16): 逆向きの実例が別のリポジトリから届いた。こちらは「ブランチにしか無い Issue が default branch から見えない」側で、下の裁定が塞ぐ方の穴にあたる。

- セッションが PR ブランチの上で、その PR が Closes する実装 Issue と一緒に、調査中に見つけた派生 Issue を起票した。PR は長く未マージのまま残り、その間、派生 Issue は default branch に無かった
- 派生 Issue の 1 本を先に着手する必要が出て、Issue だけを default branch へ移す PR を別に作ることになった
- 後から移すときに副作用が出た。実装 Issue も一緒に移すと、未マージのコード変更を `[x]` で主張する形になり、各行に未マージである旨の注記を足す必要があった。起票だけの PR で即マージしていれば起きない形で、下の裁定を支える根拠になる

報告側は「Closes する Issue だけは PR ブランチへ同梱してよい」という案を添えていた。下の裁定より緩い (Closes する Issue も、PR が長く残れば同じく default branch から見えない) ので採らない。

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
  - 追記 (2026-10-16): ISSUE-89 は feature の PR の中で起票し、同じコミットで closed/ の下に置いた。今の裁定は例外を置いていないので、この形も同梱しない側に入る (skill の「クローズ経路: feature PR 同梱を優先」節が書くのは既にある Issue を閉じる操作だけで、起票と同時のクローズは書いていない)。open のまま default branch の外に残る期間は無いが、PR が長く残れば closed の記録も A.0 の検索から見えない。open か closed かで扱いを分けるか (見えないことの害は open な Issue の重複起票と着手漏れが主で、closed の記録は小さいと見るか)、分けるなら検査が見るのを「open のまま default branch に無い」ことにするかを、検査の形と一緒に決める
  - 追記 (2026-10-16、上の報告に添えられた案): `issue-id.py` に「default branch に無く、他の ref にしか無い open な Issue」を列挙する入口を足す。さらに、`--next` は既に全 ref を歩いているので、入口を別々に作るより、全 ref を横断した Issue の索引 (各 Issue がどの ref にあるか) を `--next` と共有して持ち、この検出と A.0 の検索 (origin の default branch を引く) をどちらもその索引への問い合わせにする形も挙がった
  - ただし `issue-id.py` は docstring で規則 1 つ・エンジン 1 つを掲げ、採番と記法検査だけを担っている。記法の規則とエンジンを共有しない機能を足すと、将来ほかのツールへ切り出すときの surface が広がる。索引が採番の走査の副産物として持てるのか、別の責務として別の置き場が要るのかを先に決める

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
ISSUE-89 (closed。feature の PR で起票と同時に closed で置いた記録。例外にするかを検査の形と一緒に決める例)
