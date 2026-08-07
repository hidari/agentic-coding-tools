# agentic-coding-tools

Claude Code のための skill と plugin を集めたリポジトリ。個人の開発ワークフローを
そのまま置いてあり、誰でも参考にできる状態にしておくことを目的にしている。

配布は [apm](https://github.com/microsoft/apm) 経由。宣言だけを自分のリポジトリへ置き、
実体は clone 後に取得する形を想定している。

## 使い方

`apm.yml` の依存に次の形で書く。`<sha>` は追随したいコミットで固定する。

```yaml
dependencies:
  apm:
  - hidari/agentic-coding-tools/<パッケージのパス>#<sha>
```

`apm install` を実行すると `.claude/skills/` 配下へ配置される。

パッケージの root には `SKILL.md` と `.claude-plugin/plugin.json` の両方を置いてある。
apm は前者を見てディレクトリ全体を verbatim でコピーし、Claude Code は後者を見て
plugin として読み込む。この 2 つは互いを知らず独立に判定されるため、1 つのパッケージが
両方を兼ねる。

## plugin
component を持つパッケージ。呼び出しは `<plugin 名>:<component 名>` の修飾名で行う。

| パス | component 数 | 説明 |
|---|---|---|
| `plugins/dev-workflow` | 7 | 個人開発のワークフローを支える skill バンドルの入口。ブランチ運用、リポジトリ内 Issue 管理、マージ前の品質ゲート、振り返りのルール化、E2E 影響の静的検出、コミットと PR 本文の作法を集約する。個別の作業は component skill を直接呼ぶ。 |
| `plugins/security-blue-red-team` | 3 | Red Team (攻撃者視点の能動検証) と Blue Team (防御者視点の改善計画) を profile 駆動で継続運用する skill バンドルの入口。個別の実行は security-red-team / security-blue-team / security-vulnerability-assessment を直接呼ぶ。 |
| `plugins/web-monkey-qa` | 1 | Web アプリを重み付きランダム操作で探索し、コンソールエラーや HTTP 4xx/5xx、レイアウト崩れ、行き止まり遷移などを検出する monkey test バンドルの入口。実行は monkey-qa を直接呼ぶ。 |

## skill
単体の skill。component を持たない。

| パス | 説明 |
|---|---|
| `skills/devops/windows-vm-verification` | Parallels Desktop 上の Windows 検証 VM を繋ぐ/調べる/検証する generic CLI (winvm)。SSH 越しの NTFS/health 確認、cfg(windows) コードの remote 検証 (ローカル変更を scp 同期して remote コマンド実行)、prlctl からの IP 解決、繋がらないときのホスト側診断 (doctor) を扱う。Parallels Desktop の Windows VM を操作・検証する時に使う。 |
| `skills/meta/session-handoff` | セッションの作業状態を引き継ぎ書 <リポルート>/.cache/handoff.md に書き出し、新しいセッションへ引き継ぐ。発動経路は3つ。(1) hook からのコンテキスト超過通知を受けたとき (2) hook からのツール呼び出し破損通知を受けたとき (3) ユーザーが手動で依頼したとき (「引き継ぎ書いて」「handoff して」「セッション切り替えたい」等)。新セッション側の読み込みは SessionStart hook (handoff-sentinel) が自動で行うため、このスキルは書き出しと案内までが責務。 |
| `skills/tooling/chrome-devtools-debugger` | 公式 chrome-devtools-mcp plugin の skill 群で収集したデバッグ結果を、標準化された日本語レポート (docs/debug-reports/) へ整形・機密マスクするレイヤー。ネットワーク/コンソール/パフォーマンス/UI の調査結果を既知エラーパターンに対応づけ、優先度付きでレポート化する際に使用する。 |
| `skills/tooling/herdr` | "Control herdr from inside it. Manage workspaces and tabs, split panes, spawn agents, read output, and wait for state changes — all via CLI commands that talk to the running herdr instance over a local unix socket. Use when running inside herdr (HERDR_ENV=1)." |
| `skills/tooling/markdown-to-pdf` | Use when Markdown ファイルを整形して PDF 化したいとき。日本語ビジネス文書・技術ドキュメント・契約書ドラフト・計画書などを uv 経由のスタンドアロン Python スクリプト (render.py) で PDF に変換する。表組み・シンタックスハイライト・ヘッダー/フッター・ページ番号を含む整形済み PDF が必要なケース全般で使用する。 |

## 構造の規約

パッケージの形と命名には、破ってもエラーにならず静かに壊れる規約がいくつかある。散文の
約束にすると必ず drift するため、すべて `scripts/check-package-shape.py` の検査に落として
ある。検査する項目の一覧は同スクリプトの docstring が canonical なので、ここには再掲しない。

`plugin.json` のフィールド定義そのものは `claude plugin validate --strict` が canonical。

## ライセンス

MIT
