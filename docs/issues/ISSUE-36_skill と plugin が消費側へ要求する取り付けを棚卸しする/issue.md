---
status: open
---

# docs: skill と plugin が消費側へ要求する取り付けを棚卸しする

## 背景

`dev-workflow:in-repo-issue` が「配っただけでは検査が走らない」問題を持つことは
ISSUE-22 と ISSUE-32 が扱っている。同じクラスの欠陥が他の skill と plugin にも
あるかを調べたので、その結果を 1 箇所に記録する。

探したのは次の 1 クラスに限る。

  skill や plugin が消費側リポジトリに何かを置くこと、あるいは配線することを
  要求しているが、(a) 置かれていない / 配線されていない状態が「エラー」ではなく
  「緑」や「既定動作」として現れる、または (b) 置いた写しが canonical から
  drift しても、それを検出する経路がどこにも無い

## 方法と、その限界 (2026-08-27)

3 つの surface を並列に走査した。読んだのは 31 ファイル。

| surface | 対象 | findings |
| --- | --- | --- |
| S1 | dev-workflow の 7 component と bundle | 8 |
| S2 | security-blue-red-team と web-monkey-qa | 17 |
| S3 | スタンドアロン skill 6 本 | 7 |

反証を 1 本走らせて 0 件が反証された。ただし**全員が同じ側へ寄った結果を
そのまま採らない**ため、起票側が 2 件を対照付きで独立に再現した。

- profile を指す語の参照が配布側で 0 件であること
  (対照: 同じ範囲で `issue-id.py` は 24 件ヒットする)
- schema のテスト 2 本を走らせる経路が無いこと
  (対照: 同じファイルで `python` は 9 件ヒットする)

さらに 3 件については、subagent の報告と起票側の再測が食い違った。
いずれも subagent 側が過小で、原因は走査の深さと母集団の切り方だった。
**この棚卸しは見つけた分だけが載るので、必ず過小評価に外れている。**

## 6 つの型

### 型 1: ゲートが自己申告で、機構が無い

`pre-merge-quality-gate` の出力書式が全レーン skipped でも合格を許す。
2 つの plugin の production ガードが profile の自己申告だけに依存する。

ISSUE-33 と ISSUE-34 へ切り出した。

### 型 2: 要求されたのに配線されておらず、緑で通る

`issue-scoped-artifacts` はポインタを持つ 11 リポのうち hook を持つのが 3 リポで、
9 リポ 115 ファイルが上流既定パスに残っている。

ISSUE-35 へ切り出した。

### 型 3: 写しの drift

- in-repo-issue の template を持つ 12 リポが全て canonical と不一致 (ISSUE-32)
- issue-scoped-artifacts の hook の `entry:` が消費側で canonical と食い違い (ISSUE-35)
- windows-vm-verification の ssh config テンプレートが drift 済み
- security 側の profile テンプレートが schema の cleanup 定義を落としている。
  テンプレートをそのまま写すと Layer 3 の能動テストが 1 件も走らず静的所見へ縮退するが、
  abort ではないので完走扱いでレポートが出る
- profile テンプレートが schema の rate budget 2 値を持たない

### 型 4: テストが在るのに走らせる経路が無い

`schemas/tests/` 配下の 2 本。schema の不変条件を pin しているが、
pre-commit にも CI にも実行系の言及が無い。

ISSUE-34 のタスクへ含めた。

### 型 5: 0 件を健全の根拠にする形

- `retrospective-codify` の重複検索の glob が到達するのは 16 ファイルで、
  `find -L` だと 27。11 が不可視で、その中に dev-workflow の component が全部入る。
  自分自身も兄弟も見えないので、盲点はすべて「0 件 = 新規」として返る
- `e2e-scenario-impact-check` はパス literal が消費側の実配置と一致するかを見ないまま
  「能動的に判定した健全な skip」と断言する。走査した 34 ディレクトリのうち
  literal に一致する配置を持つのは 1 件だけだった
- `chrome-devtools-debugger` は公式 plugin が無い環境でも収集レイヤー不在のまま
  「問題なし」のレポートを生成しうる

### 型 6: 配布層自体

消費側の pin が上流 main より 7 コミット古く、鮮度を見る機構が無かった。
結果として配布中の in-repo-issue の SKILL.md に ISSUE-24 の規約節が届いていなかった。
消費側の apm.yml が手書きしていた version コメント 3 件は全て実体と食い違っていた。

pin の更新は消費側で対応済み。鮮度の検査は ISSUE-32 の層 3 が引き取る。

## この Issue の担当

型 1 と型 2 は個別 Issue へ切り出した。この Issue が持つのは残りの記録と、
型 3 と型 5 の未着手分である。着手するときは分割する。

## タスク

- [ ] 型 3 の未着手分 (ssh config テンプレート、profile テンプレートと schema の乖離) を
      個別に切り出す
- [ ] 型 5 の 3 件を切り出す。`retrospective-codify` の glob は最も影響が広い
- [ ] 棚卸しの件数を数え直す。この表は見つけた分だけなので過小評価に外れている

## 関連

- ISSUE-32: in-repo Issue の検査を配布先で走る状態にする。この棚卸しの出発点
- ISSUE-33: pre-merge-quality-gate の出力が全レーン skipped でも合格を許す。型 1
- ISSUE-34: production ガードが profile の自己申告だけに依存する。型 1 と型 4
- ISSUE-35: issue-scoped-artifacts の取り付けが配布先の一部にしかない。型 2
- ISSUE-13: 両取り付けの同時撤去を機構で検出できない。6 つの型に共通する原因
- ISSUE-31: monkey-explorer-agent が production ガードの写しを持たない。型 3 の一例
- ISSUE-29: 2 部ある Issue テンプレートの一致を検査する。型 3 のリポジトリ内版
- dotfiles の ISSUE-46: 両リポジトリの Issue をマイルストーンへ整理し着手順を決める
