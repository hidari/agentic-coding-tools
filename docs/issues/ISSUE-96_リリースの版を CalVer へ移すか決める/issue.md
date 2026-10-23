---
status: in_progress
---

# chore: リリースの版を CalVer へ移すか決める

## 背景

v0.20.0 を切るときに、Hidari から「バージョンは CalVer の方がいいかもしれない」と提案があった。v0.20.0 は今の規則 (pre-1.0 の semver 風) のまま切り、移すかどうかはこの Issue で決めることにした (2026-10-22)。

今の規則の canonical は `.claude/skills/release/SKILL.md` の「バージョン規則」節で、この Issue は値を再掲しない。

## 確かめたこと (2026-10-22、main が 2673c03 の時点)

- tag は v0.1.0 (2026-08-25) から v0.20.0 までの 20 本で、どれも minor の bump だった。patch の版は 1 本も無い
- 規則の表は、消費側の呼び出しが壊れる変更と、壊さない追加を、同じ bump に割り当てている。そのため今の版番号から、互換性が壊れるかどうかは読めない。それを読み手に伝える役目は release note が担い、release skill は壊れる変更を冒頭に置くと決めている。ただし、過去の note の書き方は揃っていない。release は 19 本ある (v0.1.0 は tag だけで release が無い)。見出し「消費側の呼び出しが壊れる変更」の節で始まるものが 6 本、別の文言の見出しで始まるもの (v0.5.0) が 1 本、冒頭の段落の散文で書いたもの (v0.6.0、v0.7.0、v0.15.0〜v0.17.0) が 5 本ある。残る v0.8.0〜v0.14.0 の 7 本は、配布物の変更の段落から始まる
- 消費側は SHA で pin し、行末に tag を注記する。tag は pin の機構ではなく、レビューの単位につけた名前である (release skill の冒頭)。版の方式を変えても、pin の機構は変わらない
- tag の作成日で数えると、8 月 (25 日から) が 5 本、9 月が 6 本、10 月 (22 日まで) が 9 本だった。同じ日に 2 本以上切った日が 4 日あり、多い日は 3 本だった
- `plugins/*/.claude-plugin/plugin.json` の `version` は plugin ごとの版で、リポジトリの tag とは別の数列である。消費側の lockfile が記録するのはこちらである。release skill はこの 2 つを揃えないと決めている

## 決めること

- CalVer へ移すか。移す側の利点は、今の番号が互換性を運んでいない実態に、名前を合わせられること。残す側の利点は、規則の書き換えも消費側の注記の読み替えも要らないこと。semver を守るなら、1.0 以降の規則 (release skill に予告がある) で互換性を番号に載せ直す道もある
- 移すなら形式。同じ日に複数切ることがあるので、日付だけでは足りない。日付の粒度 (日か月か) と、同じ期間の中の通し番号の形を決める。semver として解釈される形 (3 つの数) に収めるかも決める。収めないと、版を semver として比べる道具が誤読しうる
- 移すなら前後の並び。新しい形式の tag が、`v0.x` の tag より後ろに並ぶか (tag の一覧の並び、`git tag --sort=version:refname`、GitHub の releases の表示) を実測で確かめる
- 移すなら、何を書き換えるか。release skill の規則の表と手順の例、release note の書式、消費側 (dotfiles) の pin の注記の読み方が対象になる。plugin.json の `version` は別の数列なので、対象に含めるかも決める

## 決めたこと (2026-10-23)

Hidari の判断で CalVer へ移すことにした。形式は `vYYYY.M.N` で、形式の細部 (年と月の基準、月の書き方、N の始まり) と次の版の求め方、その理由は `.claude/skills/release/SKILL.md` の「バージョン規則」節が持つ (ここには再掲しない)。

- 移す理由は、20 本の tag がどれも minor の bump で番号が互換性を運んでいなかったこと、pin は SHA で tag はレビューの単位の名前であること、10 月だけで 9 本切っていて時期が読める名前の方が役に立つこと
- release skill の書き換えで、bump の表と 1.0 以降の予告を消した。互換性の伝え方は release note の側に残した
- `plugins/*/.claude-plugin/plugin.json` の `version` は別の数列のままにし、対象に含めない
- 同じ書き換えで、release skill の手順 5 に、消費側の確認を待つ Issue へ戻る手順を足した。ISSUE-76 が、リリースのあと消費側の確認を待って in_progress のまま残った実例による
- 消費側へは、このセッションのまとめとして dotfiles へ伝えた。pin の行末の注記が CalVer の形になること (pin の機構は SHA のままで、今の dotfiles に注記を読むコードは見つからなかった)、dotfiles の ISSUE-101 の鳴る条件の論点が 0.x の minor bump を前提にしていること、同じ Issue の `apm outdated` の確認を CalVer の tag でもすること、の 3 点

## 並びの実測 (2026-10-23)

`.cache/` の下の使い捨てのリポジトリに v0.1.0〜v0.20.0 と CalVer の tag 5 本 (v2026.9.0、v2026.10.0、v2026.10.1、v2026.11.0、v2027.1.0) を打ち、sh、bash、zsh で同じ結果を得た。

- `git tag --sort=version:refname` では、CalVer の 5 本がすべて v0.20.0 の後ろに日付順で並んだ (25 本中、v0.20.0 が 20 番目、v2026.9.0 が 21 番目)
- 素の `git tag` は文字列順で、CalVer は `v0.x` の後ろに来るが、v2026.10.0 が v2026.9.0 より前に来る。v0.10.0 が v0.9.0 より前に来る今の並びと同じ性質である。このリポジトリでも、素の `git tag` の末尾は v0.20.0 ではなく v0.9.0 だった。release skill の手順 2 の前回の tag は、`version:refname` の末尾から取る形にした
- release skill の求め方で採番すると、その月の tag が 0 本なら N が 0 になり、1 月の採番は 10 月の tag を数えなかった。月の式は 01 から 1 を返した

手順 5 の検索も測った (2026-10-23 の作業ツリー)。未チェックの箱の行に「消費側」「リリース」「pin」「release」のどれかが現れる Issue を探す最初の形は 12 件を拾い、`pin` と `release` を外しても 5 件で、待っているのは ISSUE-96 だけだった。このリポジトリでは「消費側」も「pin」も設計の一般語として現れるためである。`status: in_progress` で先に絞る形に改め、拾うのは ISSUE-96 の 1 件になった (in_progress の Issue は 2 件)。対照の木で、in_progress だったころの ISSUE-76 (03d76f0 の版) を空白を含むパスに置くと拾い、同じ行を持つ open の Issue、closed/ の下の Issue、templates は拾わないことを、sh と zsh で確かめた。

GitHub の releases の表示 (並びと Latest の付き方) は測っていない。本物の release を作らないと見られないので、最初の CalVer の release で確かめ、そのときにこの Issue を閉じる。release skill の手順 4 に、`gh release list` で見る確認を足してある。

## タスク

- [x] 移すか残すかを決める
- [ ] 移すなら、形式と前後の並びを決め、並びを実測で確かめる (git 側は確かめた。GitHub の releases の表示は最初の CalVer リリースで確かめる)
- [x] 移すなら、release skill を書き換え、消費側へ注記の変更を伝える

## 関連

ISSUE-32 (層 0 で semver の tag と release note を導入した。pin の機構は SHA のまま)
