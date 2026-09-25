---
status: open
---

# fix: user-path が Linux のホームディレクトリ形のパスに当たらない

## 背景

層 1の custom ルール `user-path` は、パス要素 `Users` の後ろに続く名前を撃つ。ルールの canonical は `plugins/dev-workflow/skills/commit-and-pr-message/scripts/leak-guard.gitleaks.toml` で、形はここへ再掲しない。macOS の `/Users/<name>` と Windows の `C:\Users\<name>` 系は当たるが、Linux のホームディレクトリ形 `/home/<name>` は当たらない。

ISSUE-58の計画レビューがこの形を指摘し (D-3)、ユーザーの裁定で ISSUE-58の範囲から外してこの Issue へ分けた。ISSUE-58では、`commit-and-pr-message` の SKILL.md と入口 `check-outgoing-text.py` の docstring の既知の限界に、この形が層 1に当たらないことだけを書いてある。

この形が効いてくるのは、手順のコマンド行を本文へ貼る経路である。Skill ツールで読み込んだ skill の本文では、skill のディレクトリを指す変数がコードブロックの内外を問わず絶対パスへ置換される (ISSUE-58の spec の前提 8)。この開発機では置換後のパスがホームディレクトリの下にあったので、置換された手順のコマンド行をそのまま本文へ貼ると、ユーザー名を含むパスが本文に載る。macOS ならそれを user-path が捕まえるが、Linux の配布先で同じ形になった場合は捕まえない。`commit-and-pr-message` の手順は入口のコマンド行と出力を本文へ貼らない規則を持つが、規則を踏まなかった本文で、パスの形を見るのは層 1だけである (層 2は禁止語リストの語を見る)。

## 確かめたこと (2026-09-24、gitleaks 8.30.1)

- 合成した名前で次の行を作り、custom の config へ `gitleaks stdin --ignore-gitleaks-allow` で通した。`/home/<name>/.claude/skills/...`、`/home/runner/work/...`、`/home/linuxbrew/.linuxbrew/bin/...`、`python3 "/home/<name>/.claude/skills/commit-and-pr-message/scripts/check-outgoing-text.py" ...` はどれも検出されず、同じ名前の `/Users/<name>/...` と `C:\Users\<name>\...` は同じ実行で検出された (対照)。既定ルールの config ではどの行も検出されない
- 置換の根拠は ISSUE-58の spec の前提 8と、計画レビューの B-15と D-7 (それぞれ別に再現している)。2026-09-24にも Skill ツールで `dev-workflow:in-repo-issue` を読み込み、散文とコードブロックの両方で変数がホームディレクトリの下の絶対パスへ置換されることを確かめた。確かめたのは macOS の開発機だけで、Linux の配布先で置換後のパスが `/home/<name>/...` の形になるかは確かめていない
- この Issue を起票する前の HEAD の追跡ファイルに `/home/` を含むものは無い (対照の `/Users/` は19本のファイルにある)。同じ時点の全 ref の差分とコミットメッセージにも `/home/` を含む行は無い (対照の `/Users/` は差分に132行、コミットメッセージを含めると137行)。この Issue 自身は `/home/` と `/Users/` を literal で持つので、コミットしたあとに同じ数え方をすると、どちらも数が増える。これは literal の数で、広げたルールの検出数と同じとは限らない

## 広げる場合に要る許可と対照

広げたときに当たる正当な値のうち、確かめたもの:

| 値 | 何か | 確かめた方法 |
|---|---|---|
| `/home/runner` | GitHub Actions の ubuntu の runner のホームディレクトリ | このリポジトリの CI (`ubuntu-latest`) の main の run のログに現れる |
| `/home/linuxbrew` | Homebrew の Linux の既定の prefix (`/home/linuxbrew/.linuxbrew`) | Homebrew のソースの定数 |

確かめていないもの:

- コンテナのイメージが既定で作る一般ユーザーのホーム。どの名前があるかは確かめていない
- `/home` の外に置かれるホーム (root ユーザーのものなど) を撃つか。未検討

形を決めるときに効く既存の事情:

- 既存の user-path は、パス要素 `Users` の大文字を手がかりに、REST API の `/api/users/<id>` のような小文字のパスを避けている (canonical の config のコメント)。Linux のパス要素 `home` は小文字なので、同じ手はそのまま使えない。URL の経路に現れる `/home/...` を許可側の対照に並べる必要があるかは確かめていない
- 既存の許可は `runner` を名前の値で持つが、`Users` の形に限った許可なので、`/home/runner` には届かない

## タスク

- [ ] user-path を Linux のホームディレクトリ形へ広げるかを決める。広げない場合は、既知の限界の記述をこの Issue の結論に合わせる
- [ ] 広げる場合は、ルールの形と許可を決め、検出すべき例と許可すべき例の両方を実際に gitleaks へ通す。対照は `scripts/check-leak-guard-rules.py` へ入れる。別の id のルールとして足すなら入口の canary も要る (同スクリプトが config の id・対照・canary の3つの集合の一致を見る)
- [ ] 広げたルールで全履歴を走査し、件数と内訳を ISSUE-58の基準値と比べる (`.gitleaksignore` を消した clone で取る)。この Issue 自身が、広げたルールに当たりうる例示 (`/home/runner/...` と `/home/linuxbrew/...` の行) を持つので、それを許可に入れるか、例示の書き方を変えるかを先に決める

## 関連

ISSUE-58 (計画レビューの D-3でこの形を見つけ、ユーザーの裁定で範囲から外した元。既知の限界にこの形を書いてある)
ISSUE-61 (配布物が配布先で成立しているかを見る検査層。skill の変数の置換で本文に絶対パスが載る経路を扱う)
