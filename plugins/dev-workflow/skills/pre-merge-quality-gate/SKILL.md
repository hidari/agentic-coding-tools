---
name: pre-merge-quality-gate
description: PR を作成する直前 (gh pr create) とマージする直前 (gh pr merge) に必ず使う。simplify、コードレビュー、ボーイスカウト、E2E 影響チェックを並列で通し、CI が捕捉できない問題を直してから作成・マージする。「マージして」「CI 通り次第マージ」と指示された時も通す。
---

# Pre-Merge Quality Gate

CI 通過 ≠ マージ可。CI が通ってもこのスキルを通してから `gh pr merge` / `gh pr create` を実行する。

## 目的

- **CI が見逃す品質問題を捕捉する**: a11y バグ (aria-* の重複参照など)、UX 問題 (要素幅・ホバー視認性など)、デッドコード、テストの冗長性、不要コメント
- **後追い修正の摩擦をゼロにする**: マージ後に気づくと follow-up PR で 2 倍の手間 (新ブランチ / 新 PR / 再レビュー / 再マージ)
- **CLAUDE.md MUST ルールを構造的に守る**: 認知負荷で飛ばないようにチェックリスト化する

## いつ使うか

### 必ず使う

- ユーザーが「マージして」「PR 出して」「CI 通り次第マージ」「PR 作成して」と指示したとき
- `gh pr merge` を実行する直前
- `gh pr create` を実行する直前
- 自分の判断で「これでマージ準備 OK」と思ったとき

### 使わない

- WIP コミット (`feat(wip):` 等) を push するだけのとき
- ユーザーが明示的に「simplify と review はスキップして」と指示したとき (理由を確認する)
- リバート / 緊急 hotfix で時間が許さないとき (理由をユーザーに明示してから判断)

## ワークフロー

### Phase 0: コンテキスト収集 (並列前の準備)

後続の Phase が使う材料をここで揃える。Phase 1 の並列レビューに渡す差分情報と、Phase 2 以降の判断に要る事実の両方が対象。`Bash` で:

```bash
git diff <base>..HEAD --name-only   # 変更ファイル一覧
git diff <base>..HEAD --stat        # 変更概要
gh pr view <num> --json title,body  # PR メタ情報
git diff <base>..HEAD | wc -l       # 差分の行数
git diff -z --name-only --diff-filter=d <base>..HEAD | xargs -0 wc -l  # 変更ファイルの行数
```

後ろの 2 つは Phase 1 の 7 で読了範囲を突き合わせる相手になる。`--stat` が出すのは追加と削除の行数で、差分の行数ではない。`-z` と `xargs -0` は、空白を含むパスを分割させないため。

`<base>` は通常 `main`、`<num>` は対象 PR 番号。

Issue のクローズをこの PR へ同梱するかの判定に要る事実 (main への直 push を禁じているか) もここで集める。作成経路とマージ経路のどちらから入っても集める。マージ直前でも対象ブランチにはまだコミットを積めるので同梱は選択肢のままで、経路で条件を付けると Phase 2 の判断が事実の無いまま黙って飛ぶ。判定の手順は `dev-workflow:in-repo-issue` の「クローズ経路: feature PR 同梱を優先」節が持つので、写さずにそちらを読む。

### Phase 1: 並列でレビュー実行

simplify / コードレビュー / boy-scout-sweep / dev-workflow:e2e-scenario-impact-check / PUBLIC 漏洩スイープ は独立しているので **同じメッセージ内で並列に launch する**。最後の 1 つはリポジトリが PUBLIC のときだけ起動する。`Agent` tool で起動するレーンのうち指摘を返したものには、6 の反証役を 1 体ずつ付ける。

起動する agent の総数は、`Agent` tool で起動するレーンの数と、それに付く反証役の数と、`Skill` tool で起動するレーンが内部で起動する数 (`simplify` と e2e skill がそれぞれ持つ) の和になる。反証役は 6 のとおりレーン 1 本に 1 体までなので、`Agent` tool で起動する側はレーンの数の 2 倍を超えない。起動数に上限を設けている運用では、launch の前にこの和を見積もる。

`Agent` tool で起動するレーンと反証役には、何を何件読んだか (ファイル数と行数、読めなかった範囲はファイルと行の範囲) を、指摘の有無にかかわらず報告に書かせる。各 prompt にこの要求を入れる。「指摘 0 件」が全部を読んだ結果なのか、一部しか読めなかった結果なのかは、報告がこれを持たないと区別できない (実測: 差分を取れなかった reviewer が作業ツリーの一部だけを読み、「部分的なレビュー」と自己申告したので見落としに気づけた)。返った範囲は 7 で突き合わせる。

同じ prompt には、作業ツリーを変更しないことも入れる。`general-purpose` は Edit と Write を持つので、レーンや反証役がファイルを書き換えると、`simplify` の適用や Phase 3 の修正と競合し、誰が何を変えたかも追えなくなる。実測が要るときは、リポジトリから切り離したコピーで行わせる。

1. **`Skill` tool で `simplify` を起動**
   - args: 変更概要 + 変更ファイルのリスト
   - 変更ファイルは `git diff <base>..HEAD --name-only` で抽出
   - `simplify` 自体が reuse / simplification / efficiency / altitude の 4 並列内部実行を行う

2. **`Agent` tool で `general-purpose` を起動 (コードレビュー)**
   - prompt: 変更概要 + 「`git diff <base>..HEAD` を自分で取って全部読む」 + レビュー観点 (バグ / セキュリティ / a11y / 関数型 / DRY / テスト網羅性) + 「confidence-based filtering で本当に修正したい指摘のみ返してほしい」
   - prompt には Phase 1 冒頭の読了範囲の報告と、作業ツリーを変更しないことも入れる
   - `feature-dev:code-reviewer` を使わないのは、この agent 型が Bash を持たず `git diff` を実行できないため (実測)。差分を取れない reviewer は変更ファイルの全文を読む方へ乗り換えることがあり、削除された行は差分にしか無いので「消した内容が正しいか」が黙って落ちる
   - プロジェクトの CLAUDE.md が `feature-dev:code-reviewer` を指定しているときは、親が差分をファイルへ書き出し、その絶対パスを prompt で渡して Read させる。書き出し先は、プロジェクトの CLAUDE.md が書き捨ての置き場を定めていればそこに従い、定めが無ければ `git check-ignore` で ignore されることを確かめたディレクトリにして、レビューの後に消す。追跡下に置くと差分のファイルが誤ってコミットされる。ignore されたディレクトリの下のファイルも、絶対パスなら Read で読めた (実測)。それでも読めなかったという報告が別の環境からあり、原因は切り分けていないので、読めた行数を報告させて突き合わせる

3. **`Agent` tool で `general-purpose` を起動 (Boy Scout Sweep)**
   - prompt: 「**触ったファイル** (今回 PR で diff のあるファイル) を読み、CLAUDE.md ボーイスカウトルールに該当する箇所のみ抽出。
     - 触ったファイルは差分の周辺だけでなく全部を通して読む
     - **抽出対象は『触ったファイル』のみ**。 同 directory の隣接ファイル (`<sibling>.tsx` など) は **抽出対象ではなく**、 触ったファイル内の表記揺れ検出のための **コンテキスト参照** として読む (隣接ファイル自体に boy scout 候補があっても、それは別 PR で扱う / 別 issue 提案として返す)。
     - **必ず検出する対象**:
       - タスク参照コメント (`// Issue #N: ...`, `// PR #N`, `// Closes #N` のような GitHub の番号記法に加え、 in-repo Issue の識別子を書いたコメントも同じ) — PR description にあるべき情報、コードでは rot する。 参照先が既に復元できないものは推測で番号や識別子を割り当て直さず、 参照を伴わない記述へ書き換える
       - 履歴説明コメント (`// 旧実装は ...`, `// 元々は ...`, `// 以前 ...`) — git log で追える、 rot する
       - 識別子で表現済みの WHAT 説明コメント (関数名・型名で意図が明らかなのに WHAT を書いてあるもの)
       - 隣接 panel / コンポーネントとの表記揺れで **触ったファイル側に正当な WHY が無い** もの (`<div>` vs `<p>` のような同等用途タグの不統一など)。 触ったファイル側に「機能差を生む WHY コメント」が明示されていれば設計差として温存する。
     - **抽出しない対象**:
       - PR スコープを大きく超える大規模リファクタ (これは follow-up issue 提案にする)
       - WHY を説明する正当なコメント (設計判断、非自明な制約、library 仕様の捕捉、規約逸脱の正当化など)
       - 機能的に異なる兄弟ファイルの設計差 (例: empty state を持つ panel と持たない panel の差は意図的)
     - **出力ルール** (Boy Scout Sweep の自己レポート出力。 skill 全体の最終アグリゲート出力 = Phase 4 の `## Pre-Merge Quality Gate Result` 節は別物で、 そちらは出力フォーマット節に従う):
       - 検出 1 件以上の場合: `<file>:<line> → <削除 or 修正案>` の箇条書き。Confidence 80+ のみ
       - **検出 0 件の場合は『検出なし』と明示する**。0 件が全部を読んだ結果であることは、この明示ではなく報告に書く読了範囲が示す」
   - prompt には Phase 1 冒頭の読了範囲の報告と、作業ツリーを変更しないことも入れる
   - 観点の根拠: CLAUDE.md のコメントの規範 (WHAT は識別子で示し、タスク参照や rot するコメントを書かない) とボーイスカウトルール。ボーイスカウトルールの定義は CLAUDE.md が canonical で、ここでは写さない。抽出の対象が触ったファイルに限られ、隣接ファイルは参照にとどまることは上の prompt が持つ

4. **`Skill` tool で `dev-workflow:e2e-scenario-impact-check` を起動**
   - args: 変更概要 + base branch (通常 `main`)
   - skill 内部でフロント変更検出 → 無ければ早期 skip、有れば agent 経由で E2E への影響を静的検査
   - 検出対象: `getByRole` / `getByLabel` 整合性、画面構造変化（保存ボタン2個など strict mode violation 系）、`<button>` ↔ `<Link>` 化、role / aria-label 変更、ルート変更、同じ画面を別経路で叩く別テストの取りこぼし
   - staging E2E が唯一の自動 E2E 検出網になっている構成 (ローカル E2E を CI で回さない) では、shift-left で爆発前に検出する価値が特に高い

5. **`Agent` tool で `general-purpose` を起動 (PUBLIC 漏洩スイープ)** — **リポジトリが PUBLIC のときだけ**
   - 起動可否は公開範囲で決まる。`gh repo view --json visibility` で確かめてから判断する (グローバル CLAUDE.md が「リポジトリの公開範囲は常に確認する」を規定している)
   - prompt: 「差分に含まれる文書とコードを読み、PUBLIC なリポジトリへ入れてよい状態かを判定してほしい。判断規範はプロジェクトの CLAUDE.md が canonical。
     - **検査層の緑を根拠にしないこと。** 漏洩検査が見るのは形の決まったものと、禁止語リストに載っている語だけである。載っていないものは 0 件で通る (実測: 15 ファイルすべてが 0 件を返したが、実際には 4 つのプロダクト名が残っていた)
     - **語単位の照合では原理的に捕まらない 2 形を特に見ること**:
       - **集合が指紋になる形**: 個々は残してよい公開 OSS 名なのに、列挙の組み合わせが対象を特定してしまう。1 語ずつの可否しか測らない照合では、どの語も「正しく残す」と判定されたまますり抜ける
       - **解釈を反転させる一行**: 「この文書は特定の対象を念頭に書いた」と明かすメタ記述。それ自体は固有名詞を 1 つも含まないのに、文書中の全ての例示を架空のものから実在の答えへ読み替えさせる
     - **例なのか実運用なのかで判定が逆になる。** 同じ固有名詞が出ていても、例の列挙の一員なら残してよく、実際にそうしていると読める書き方なら落とす。この見分けは機械検査には原理的に持てない
     - 公開 OSS の名前 (誰でも検索して辿り着けるもの) と、既に公開済みの情報 (LICENSE の著作権表示、`plugin.json` の author、リポジトリ参照) は対象外。隠しても露出は減らず、文章が不自然になるだけ
     - 出力は `<file>:<line> → <何が露出しているか> / <一般化の案>` の形で、確信度 80 以上のみ。検出 0 件ならそう明示する」
   - prompt には Phase 1 冒頭の読了範囲の報告と、作業ツリーを変更しないことも入れる
   - 観点の根拠: グローバル CLAUDE.md のユーザーデータを守るセキュリティの優先と、プロジェクト CLAUDE.md の PUBLIC 制約。機械検査の層を作っても、**読解を要する分は落とせない**ことが実測で確定している
   - 一般化するときは発火条件の具体性を残し、出所だけを落とす。削ると文書の価値が目減りする

6. **`Agent` tool で `general-purpose` を起動 (反証役)** — `Agent` tool で起動したレーンのうち、指摘を返したものごとに 1 体
   - `Agent` tool のレーンの結果が返ったら、指摘を返したレーンの反証役を同じメッセージで並列に起動する。`simplify` の手順を進めている間に起動してよい。反証役の分だけ所要時間は延びる
   - 渡す前に、親が指摘へ ID を振る (レーンの名前と連番)。突き合わせにレーンが書いた見出しや要約を使わない。モデルが書き直す文字列は、同じ指摘でも呼び出しごとに揺れる
   - prompt: ID 付きの指摘の一覧 + そのレーンへ渡した prompt の観点の部分 (検出対象と除外対象) をそのまま + 差分の取り方 + 「指摘を 1 件ずつ一次資料 (差分、コード、仕様の canonical) で検証し、事実か、直さないと実害があるか、修正案に欠陥や不足 (直すと別の箇所と食い違うなど) が無いかを判定して、根拠を書く。修正案に欠陥や不足があれば代案を書く。同じ観点の見落としがあれば返す」。観点を渡さないと、反証役はレーンと違う基準で事実かを判定し、見落としも探せない
   - prompt には Phase 1 冒頭の読了範囲の報告と、作業ツリーを変更しないことも入れる
   - 判定に迷ったときの既定値 (「確信が無ければ反証済みとする」など) を渡さない。既定値はそのまま結論を片側へ寄せる
   - 落とすのは、事実と実害の両方が反証された指摘だけにする。修正案に欠陥や不足があるとされた指摘は、代案と見比べてから直す (実測: 反証役が、レーンの修正案のままだと別の箇所と食い違う不足を補った)
   - 反証役が返した見落としは、誰も検証していない新しい指摘なので、親が一次資料で確かめてから Phase 2 の分類へ入れる。2 体目の反証役は付けない
   - `simplify` と e2e skill には付けない。どちらも `Skill` tool で起動するレーンで、自分の手順を持つ (`simplify` は修正の適用まで行う)
   - 利用者が Workflow tool の起動を許している環境では、レーンから反証役への流れをそちらの pipeline で組んでよい。形 (観点ごとのレーン、反証役の付け方と ID の振り方は上の箇条のとおり) は同じにする

7. 揃った結果を **アグリゲート** する
   - `Agent` tool のレーンが報告した読了範囲を、Phase 0 で控えた数と突き合わせる。差分を読んだと報告したレーン (コードレビューは prompt がそう求める) は差分の行数と、ファイルの全文を読んだと報告したレーン (Boy Scout Sweep は prompt がそう求める) は変更ファイルの一覧と行数と比べる。差分を `feature-dev:code-reviewer` へファイルで渡したときも、そのファイルは差分そのものなので比べる相手は同じ。ファイル数が一致しても、ツールの出力が途中で切れて最後のファイルの後半を読んでいないことがあり、行数まで比べないとこれは見えない
   - 反証役については、検証した指摘の根拠を読めたかだけを見る。反証役は差分の全体を読む役ではないので、差分の行数とは比べない
   - レーンにも反証役にも、欠けがあればやり直すか、出力フォーマットの読了範囲の行に欠けを書く

### Phase 2: 指摘事項の判断

Phase 1 の指摘と gate 自身の判断を、次の軸で分類する:

| 分類 | 対応 |
|---|---|
| **Critical** (a11y / security / data loss / 機能バグ) | 必ず修正 |
| **Important** (UX / 明確なバグ / 仕様逸脱) | 修正 |
| **Boy Scout** (rot コメント / タスク参照 / 履歴説明 / 識別子で表現済み WHAT / 隣接ファイルとの軽微な表記揺れ) | 触ったファイル内なら今回 PR で修正、 PR スコープを大きく超えるなら別 issue 起票。CLAUDE.md MUST ルール |
| **Nice-to-have** (簡素化 / dead code 除去 / テスト fixture) | スコープに余裕があれば修正、なければ follow-up issue を起票 |
| **False positive / 過剰抽象化** | スキップ。短い理由を明記 |
| **クローズ同梱** (この PR が Issue のタスクを全消化するか) | 同梱するなら Phase 3 で `dev-workflow:in-repo-issue` の「クローズ経路: feature PR 同梱を優先」節が規定する手順を適用する。判定は Phase 0 で集めた事実に基づく |

3 箇所程度の重複は定数化を強制しない (早すぎる抽象化を避ける)。

#### 観点どうしが正面衝突したときの裁定

並列レビューは観点ごとに独立して走るので、同じ箇所に正反対の結論が返ることがある。これは並列化の産物であって失敗ではない。片方だけを読んで従うと単一レビュアーと同じ盲点に戻るため、必ず両方の根拠を読んでから裁定し、採らなかった側は理由を記録する。

裁定は印象や confidence の大小ではなく、次の順に引いて決める。先に引いたもので決まれば、後ろは引かない。

1. その論点に規約の canonical (規約を持つ skill の節、スクリプトの docstring、検査の定義) があれば、それに従う。実例: 関連節を「識別子だけにする」と「リンクに戻す」が衝突したとき、`dev-workflow:in-repo-issue` の「Issue 間の参照」節で決着した
2. 説明や定義の置き場 (テストに置くかプロダクトに置くかなど) が割れたときは、参照の向きで決める。プロダクトからテストを指さない
3. どちらでも決まらなければ、**その変更で検証力が増えるか減るか** で決める。既定は「pin が 1 本減るなら採らない」

3 の実例: `simplification` が「全行が同じ値のタプルは平坦化しろ (YAGNI)」、`altitude` が「将来の分岐余地として残せ」と正反対を返した。どちらも confidence 80 台で印象では決められなかったが、平坦化すると分類表と実装の食い違いを突合していたテストがその能力を失う (pin を 1 本失う) と分かって決着した。

`simplify` の指摘には挙動を変える提案が混ざる。マージ直前のブランチでは、挙動を変える提案は既定で別 issue へ回す。`simplify` は品質の改善であって仕様変更の場ではない。

### Phase 3: 修正反映 + 再検証

1. 修正を適用 (Edit / Write)
2. 同梱すると判断したなら `dev-workflow:in-repo-issue` の「クローズ経路: feature PR 同梱を優先」節が規定する手順を feature ブランチで適用する
   - Phase D を直接名指ししないのは、同梱経路の射程が Phase D 全体とは一致せず、狭める側の canonical が同梱節だから。ここで Phase D を名指しすると gate の宣言だけが canonical より広くなる
   - 「コミットへ含める」ではなく「適用する」と書くのは、同梱節の射程がコミットを作る操作だけではないため。次の Phase を起動する手順のようにコミットへ入る変更を作らないものが混ざるので、動詞をコミットに縛ると gate の宣言だけが canonical より狭くなる (広くする側と同じだけ危険で、狭い側は「やらなくてよい」に読める)
   - Phase 4 (執行) ではなくここへ置くのは、同梱が issue.md の編集と `git mv` と相対リンクの補正というファイル変更を伴うため。Phase 4 に置くと、この Phase の再検証を通らない変更が PR へ入る
   - マージ経路から入ったときは、同梱で作ったコミットをリモートの対象ブランチへ push してから Phase 4 へ進む。`gh pr merge` がマージするのはリモート側の内容なので、手元に積んだだけだと同梱が黙って落ちる
   - その push は必須チェックを pending へ戻す。待たずに Phase 4 へ進むと `gh pr merge` が拒否されるので、`gh pr checks <num> --watch` で緑を見てから進む。同梱をマージ経路で行うとこの再走のぶん CI が余計に走る (作成経路には無い費用で、同梱節が書く費用の比較は作成経路の話)
3. フォーマッタを走らせる (該当エリアのみ)
4. 全テストが pass することを確認する
   - どちらも入口はプロジェクトごとに違う。canonical はプロジェクトの CLAUDE.md なので、ここでは特定のコマンドを既定にしない
   - CLAUDE.md が書いていなければリポジトリのルートにあるタスクランナーの定義 (`Makefile` / `justfile` / `package.json` / `.pre-commit-config.yaml` など) を読んで確かめる。この列挙は手がかりであって網羅ではない
   - エリアで切られているプロジェクトでは、触ったエリアの target を選ぶ。エリアの切り方はデプロイ単位であることが多い
5. UI 変更があり、プロジェクトの CLAUDE.md が実ブラウザでの確認を求めていれば、**chrome-devtools-mcp で実ブラウザ動作確認**
   - 主要ツール: `mcp__plugin_chrome-devtools-mcp_chrome-devtools__navigate_page`, `take_snapshot`, `take_screenshot`, `list_console_messages`, `click`, `hover`, `fill`
   - フロントエンドを再起動した後は `isolatedContext` パラメータを使う
6. 必要なら追加コミット (同じブランチに積むか、既にマージ済みなら follow-up ブランチ)

### Phase 4: マージ / PR 作成実行

ここで初めてマージまたは PR 作成を実行する。どちらもコマンドの形と本文の渡し方は `dev-workflow:commit-and-pr-message` の Phase C に従う。Tirith が止めるのは本文をインラインで渡したときだけで、フラグの形は見ていない。

マージ側の形をここへ再掲しないのは、squash の subject が in-repo Issue の識別子規約に縛られており、その canonical が本 skill の外にあるため。写すと規約が変わったときに写した側だけが取り残される。しかも subject はサーバ側で生成されるので commit-msg hook が届かず、どの経路も赤くならないまま履歴へ違反が残る。`--subject` を省いた形でマージすると実際に違反が 1 件履歴へ入った (実測)。

ブランチ削除だけは本 skill が決める。Phase C が示す形に `--delete-branch` を足して渡すこと。このフラグはローカルとリモートの両方を消すので、リモート側の `delete_branch_on_merge` を有効にしているリポジトリでも要る (設定が消すのはリモートだけで、ローカルは残る)。

### Phase 5: Issue クローズ処理 (gh pr merge 実行時のみ)

`gh pr merge` 成功後、 `dev-workflow:in-repo-issue` skill の Phase C (自動クローズ) を起動する。 起動可否を gate 側で判定しないこと。 対象識別子の抽出も「抽出 0 件なら close 対象なしで終了」の判定も C.1 が持っており、 同じ条件を gate 側へ写すと識別子の記法が変わったときに写した側だけが取り残されて起動が静かに止まる。 起動時は呼び出し元が gate であることを明示し、 dev-workflow:in-repo-issue 側 C.2 のフォールバック CI 確認をスキップさせる。 詳細手順は dev-workflow:in-repo-issue SKILL.md 参照。

## 落とし穴 (Red flags)

| 思考の罠 | 実態 |
|---|---|
| 「ユーザーが『CI 通り次第マージ』と言った = simplify はスキップ可」 | NO。CI は code review の代替ではない。「CI 通り次第マージ」は CI を確認してマージする手順の指示であり、品質ゲートの省略許可ではない。指示の文字通りの解釈で MUST ルールを飛ばさない |
| 「小さな変更だから simplify は不要」 | 小さい変更ほど a11y や境界条件のバグが隠れやすい。例: 1 行の `aria-describedby` 追加でスクリーンリーダー読み上げが二重化 |
| 「マージ後に simplify すれば良い」 | follow-up PR の摩擦は大きい (新ブランチ / 新 PR / 再レビュー / 再マージ + Reviewer の手間) |
| 「CLAUDE.md に書いてあるから覚えてる」 | タスク完了モードでは MUST ルールが認知から落ちやすい。skill 化で構造的に防ぐ |
| 「simplify は表面的な改善だけ」 | simplify は 4 並列で reuse / simplification / efficiency / altitude を見る。dead code、leaky abstraction、hot path bloat、redundant state に加え、特殊ケースの積み重ねや集合の二重管理といった構造の問題も検出される |
| 「テストが pass してるから OK」 | テスト pass は仕様通り動くことの証明だが、a11y バグ・UX 問題はテストで検出されにくい |
| 「ボーイスカウト確認はユーザーに毎回聞けば良い」 | NG。CLAUDE.md MUST ルール「ボーイスカウト」を skill 内で構造化するためにこの skill がある。Phase 1 の Boy Scout Sweep を毎回必ず動かす |
| 「Boy Scout Sweep は触ったファイルだけ読めば充分」 | 抽出対象は触ったファイルのみだが、表記揺れを判断するには同 directory の隣接ファイルを **コンテキストとして読む** 必要がある。empty state パターンの不統一など、隣接ファイルとの一貫性は touched files だけ見ても気付けない |

## 出力フォーマット

タスク完了の前に、自分への確認として以下を出力する:

```
## Pre-Merge Quality Gate Result

- simplify: [completed / skipped (reason)]
- code-review: [completed / skipped (reason)]
- boy-scout-sweep: [completed / skipped (reason)]
- verifiers: [N 体 (指摘 M 件のうち K 件を落とした、見落として足した L 件) / not needed (reason)]
- read coverage: [Phase 1 の 7 の突き合わせで欠けなし / 欠け: <レーンまたは反証役の ID> <ファイルと範囲>]
- dev-workflow:e2e-scenario-impact-check: [completed / skipped (reason: e.g., no frontend changes)]
- public-leak-sweep: [completed / skipped (reason: e.g., private repository)]
- Findings applied: [N items (incl. M boy scout, K e2e impact, L leak) / no findings]
- Re-verification: [全テスト pass / manual QA done / N/A]

→ Merge OK
```

「Merge OK」を出力していない状態で `gh pr merge` を呼ばない。
ユーザーに「ボーイスカウト適用余地は？」と追加で聞かれた場合は **本 skill が既にカバーしているはず** なので、漏れた候補があれば skill の Phase 1 (Boy Scout Sweep) のプロンプト改善対象として記録する。

## 関連

- `dev-workflow:commit-and-pr-message` (sibling skill): 本文の書き方と渡し方、および `gh pr create` / `gh pr merge` へ渡すフラグの形。Phase 4 はどちらの実行もここへ委ねる。同 skill は「何をどう書いて渡すか」だけを持ち「いつ実行してよいか」は持たないので、判断は本 skill に残る
- `dev-workflow:in-repo-issue` (sibling skill): Issue のライフサイクル。同梱の判定 (Phase 0 で事実収集、Phase 2 で判断、Phase 3 で適用) と、マージ後の自動クローズ (Phase 5) がここを参照する
- `simplify` (built-in skill): 4 並列レビューと修正適用
- `feature-dev:code-reviewer` (subagent): Phase 1 の 2 では既定にしない。理由と、指定されたときの渡し方は同項目が持つ
- `dev-workflow:e2e-scenario-impact-check` (sibling skill): フロント変更が E2E シナリオテストを壊す可能性を静的検出。Phase 1 の 4 つ目並列タスク
- `verification-before-completion` (superpowers): 完了主張前の検証
- `requesting-code-review` (superpowers): レビュー依頼の作法

## 関連 CLAUDE.md ルール

- `~/.claude/CLAUDE.md`: 「Simplify、レビュー、ボーイスカウトルール全てを適用後の Push」
- 各プロジェクトの `CLAUDE.md` に「PRのマージ前には必ず /simplify と feature-dev:code-reviewer」がある場合はそれが優先 (差分の渡し方は Phase 1 の 2)
