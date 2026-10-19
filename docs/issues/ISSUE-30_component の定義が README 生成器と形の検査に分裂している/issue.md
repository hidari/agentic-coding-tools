---
status: open
---

# refactor: component の定義が README 生成器と形の検査に分裂している

## 背景

ISSUE-27 で 2 bundle から sub-skills を撤去したとき、README の「component 数」が 0 になる
問題に対処するため `scripts/gen-readme.py` に `count_components()` を新設した。これで
skill・agent・command の 3 種が数えられるようになったが、**「何が component か」の定義が
リポジトリ内で 2 つに分裂した**。

CLAUDE.md は canonical の表で「パッケージの形と命名」を
`scripts/check-package-shape.py` の docstring と定めている。component の定義は
パッケージの形に属するので、canonical はそちら側にあるべきで、README 生成器は表示層に
すぎない。

### 実測した食い違い

同じ `skills/` について、既に 2 つの規則が併存している。

| | 数え方 | SKILL.md を持たないディレクトリ |
|---|---|---|
| `gen-readme.py` の `count_components()` | SKILL.md を持つディレクトリだけ | 数えない |
| `check-package-shape.py` の `inner` 構築 | `os.listdir` の生の結果 | 数える |

後者は root SKILL.md との名前衝突検査に使われている。SKILL.md を持たない置き忘れの
ディレクトリが root SKILL.md と同名だと、**存在しない衝突を報告する**。ISSUE-27 の撤去で
2 bundle から `skills/` が消えたため、両 plugin ではこの検査が空振りになっており、分裂が
露見しないまま残る。

さらに `--check` はこの分裂を捕まえられない。生成側と検査側が同じ関数を通るので、数え方が
壊れても両者は一致したまま緑になる。ISSUE-27 で足した `scripts/test_gen_readme.py` が
守っているのは `gen-readme.py` 側だけで、`check-package-shape.py` 側の定義は誰も pin して
いない。

### 前段に import 安全化が要る

`check-package-shape.py` は `__main__` ガードを持たず、トップレベルで検査を実行して
`sys.exit` する。`importlib` で読み込んだ瞬間に検査本体が走るため、`gen-readme.py` から
借りる形が取れない。この制約は ISSUE-12 が既に「テストを書く前に import 安全化が要る」
として記録している。両 Issue は前段を共有する。

借用そのものの手本はリポジトリ内にある。`scripts/check-related-refs.py` が
`issue-id.py` を importlib で借り、借用名の実在を起動時に検査して rename されたら
exit 2 で落ちる形を取っている。

追記 (2026-10-20): ISSUE-64 で README の「component 数」列を消し、`gen-readme.py` の `count_components()` とそのテストも消した。生成器の側の定義は無くなったので、2 つの定義が食い違うという分裂は無くなった。残るのは `check-package-shape.py` の側の問題で、`inner` が SKILL.md を持たないディレクトリまで数えて、存在しない名前の衝突を報告しうること (上の表の 2 行目) と、下の「併せて拾える面」である。借用する側が居なくなったので、import 安全化はこの Issue では要らなくなった (ISSUE-12 の側にはテストを書く前段として残る)。下のタスクのうち生成器に関わるものは、これに合わせて外した。

ただし frontmatter を読む関数は、今も `gen-readme.py` と `check-package-shape.py` に 1 つずつある。前者は字下げした継続行を値へつなぐが、後者はつながずに捨てる。`description` を複数行で書くと、README には全文が載るのに、形の検査は 1 行目しか見ない。これも下のタスクに加えた。

### 併せて拾える面

ISSUE-27 で常時ロード層の主役が SKILL.md の frontmatter から command の frontmatter へ
移った。`check-package-shape.py` の frontmatter 検査 (`name` と `description` の存在) は
SKILL.md だけを対象にしているため、command の `description` 欠落や agent の `name` と
ファイル名の不一致は静かに未登録・未マッチになる。component を扱えるようになれば同じ
経路で検査できる。

## タスク

- [ ] `check-package-shape.py` の名前衝突検査が、SKILL.md を持たないディレクトリを数えない形にする
- [ ] command と agent の frontmatter を検査対象へ広げるかを決める。広げる場合、command は
      `name` を持たない (ファイル名が name になる) ので、SKILL.md と同じ規則は当てられない
- [ ] 単体 skill の側でも同じ数え方の問題が起きる (2026-09-26 に実測)。ある branch にだけある skill のディレクトリに git が無視するファイル (`__pycache__/`) が残ったまま別の branch へ切り替えると、無視するファイルだけのディレクトリが `skills/<category>/<name>/` として残り、`check-package-shape.py` が「SKILL.md がない」と報告してローカルの pre-commit が止まる。CI は clean な checkout なので起きない。1 つ目のタスク (名前衝突検査の修正) と合わせて、追跡されているファイルを持たないディレクトリを数えない形にする
- [ ] frontmatter を読む関数を 1 つにするか、`check-package-shape.py` の側も継続行をつなぐ形に揃える

## 関連

- ISSUE-12: `scripts/` の検査スクリプトが自分自身のテストを持たない。`check-package-shape.py` の import 安全化を前段として共有していたが、ISSUE-64 以降こちらでは要らない。あちらはテストの不在、こちらは定義の所在を扱う
- ISSUE-27: `count_components()` を新設した PR。分裂はそこで生まれた
- ISSUE-64: README の「component 数」列と `count_components()` を消した。この Issue が扱う分裂の生成器側はそこで消えた
