# agentic-coding-tools

Coding Agent のための自作 skill と自作 plugin を管理するリポジトリ。

配布は [apm](https://github.com/microsoft/apm) 経由で取得する形を想定している。

## 使い方

`apm.yml` の依存に次の形で書く。`<sha>` でバージョンを固定することを推奨する。

```yaml
dependencies:
  apm:
  - hidari/agentic-coding-tools/<パッケージのパス>#<sha>
```

`apm install` を実行すると `.claude/skills/` 配下へ配置される。

各 plugin の root には `SKILL.md` と `.claude-plugin/plugin.json` の両方を置いてある。

apm は前者を見てディレクトリ全体をコピーし、Coding Agent は後者を見て plugin として読み込む。この 2 つは互いを知らず独立に判定されるため、1 つの plugin ディレクトリが両方を兼ねる。

## plugin

skill・agent・command をまとめたパッケージ。skill と agent は `<plugin 名>:<component 名>` の修飾名で呼び、command は `/<command 名>` で呼ぶ。

| パス | 説明 |
|---|---|
| `plugins/dev-workflow` | 個人開発のワークフローを支える skill バンドルの入口。ブランチ運用、リポジトリ内 Issue 管理、マージ前の品質ゲート、振り返りのルール化、E2E 影響の静的検出、コミットや PR の本文の漏洩検査を集約する。個別の作業は component skill を直接呼ぶ。 |
| `plugins/security-blue-red-team` | Red Team (攻撃者視点の能動検証) と Blue Team (防御者視点の改善計画) を profile 駆動で運用する skill バンドル。 |
| `plugins/web-monkey-qa` | Web アプリを重み付きランダム操作で探索し、コンソールエラーや HTTP 4xx/5xx、レイアウト崩れ、行き止まり遷移などを検出する monkey test バンドル。 |

## skill

単体の skill。component を持たない。

| パス | 説明 |
|---|---|
| `skills/devops/macos-vm-verification` | Parallels Desktop 上の macOS 検証 VM を操作・検証する CLI (macvm)。GUI アプリをホストの作業を止めずに VM で起動して目視したいとき、VM の健全性確認・接続診断・スクリーンショット・ファイル転送・任意のコマンド実行をしたいときに使う。 |
| `skills/devops/windows-vm-verification` | Parallels Desktop 上の Windows 検証 VM を操作・検証する CLI (winvm)。ローカル変更を VM へ同期して cfg(windows) コードを検証するとき、VM の健全性確認・接続診断・スクリーンショット・ファイル転送・任意の pwsh コマンド実行をしたいときに使う。 |
| `skills/meta/context-loading-mechanics` | Claude Code が session_start で常時ロードするものと条件付きでロードするもの (paths 付き rules など) の実測結果と、常時ロード層が膨らんだときの移設判断。CLAUDE.md や rules が重いとき、規範の置き場所を決めたいとき、paths 付き rules が発火しない・指示が効いていないときに使う。 |
| `skills/meta/session-handoff` | セッションの作業状態を引き継ぎ書 <リポルート>/.cache/handoff.md に書き出す。hook (handoff-sentinel) の通知がこの skill を名指ししたとき、または「引き継ぎ書いて」「handoff して」「セッション切り替えたい」と頼まれたときに使う。次のセッションへの注入は別に配線する hook の責務。 |
| `skills/tooling/chrome-devtools-debugger` | 公式 chrome-devtools-mcp plugin の skill 群で集めたネットワーク/コンソール/パフォーマンス/UI の調査結果を、機密をマスクした日本語のデバッグレポートに整形する。既知のエラーパターンに対応づけ、優先度付きでレポートにまとめたいときに使う。 |
| `skills/tooling/jev-lint-curated` | pin した版の jev-lint を、実測で選んで固定した rule の一覧だけで check / review / compat 実行するラッパ。コードの名前・コメント・docstring が中身と食い違っていないかを advisory に確かめたいときに使う。rule の新規作成や cutoff の較正は上流の skill jev-lint を使う。 |
| `skills/tooling/markdown-to-pdf` | Markdown を表組み・シンタックスハイライト・ヘッダー/フッター・ページ番号付きの整形済み PDF に変換する。日本語のビジネス文書・技術ドキュメント・契約書ドラフト・計画書などを PDF で配布・アーカイブしたいときに使う。 |

## 構造の規約

パッケージの形と命名には、破ってもエラーにならず静かに壊れる規約がいくつかある。

散文の約束にすると必ず drift するため、すべて `scripts/check-package-shape.py` の検査に落としてある。検査する項目の一覧は同スクリプトの docstring が canonical なので、ここには再掲しない。

`plugin.json` のフィールド定義そのものは `claude plugin validate --strict` が canonical。

## ライセンス

MIT
