# agentic-coding-tools

Coding Agent のための 自作skill と 自作plugin を管理するリポジトリ。

配布は [apm](https://github.com/microsoft/apm) 経由で取得する形を想定している。

## 使い方

`apm.yml` の依存に次の形で書く。`<sha>` でバージョンを固定することを推奨する。

```yaml
dependencies:
  apm:
  - hidari/agentic-coding-tools/<パッケージのパス>#<sha>
```

`apm install` を実行すると `.claude/skills/` 配下へ配置される。

各pluginの root には `SKILL.md` と `.claude-plugin/plugin.json` の両方を置いてある。

apm は前者を見てディレクトリ全体をコピーし、Coding Agent は後者を見て plugin として読み込む。この2つは互いを知らず独立に判定されるため、1つのpluginディレクトリが両方を兼ねる。

## plugin

複数のコンポーネント、skill、agent をまとめたもの。 `<plugin 名>:<component 名>` の修飾名で呼び、command は `/<command 名>` で呼ぶ。

| パス | 説明 |
|---|---|
| `plugins/dev-workflow` | 個人開発のワークフローを支える skill バンドルの入口。ブランチ運用、リポジトリ内 Issue 管理、マージ前の品質ゲート、振り返りのルール化、E2E 影響の静的検出、コミットと PR 本文の作法を集約する。 |
| `plugins/security-blue-red-team` | Red Team (攻撃者視点の能動検証) と Blue Team (防御者視点の改善計画) を profile 駆動で運用する skill バンドル。 |
| `plugins/web-monkey-qa` | Web アプリを重み付きランダム操作で探索し、コンソールエラーや HTTP 4xx/5xx、レイアウト崩れ、行き止まり遷移などを検出する monkey test バンドル。 |

## skill

単体の skill。component を持たない。

| パス | 説明 |
|---|---|
| `skills/devops/macos-vm-verification` | Parallels Desktop 上の macOS 検証 VM を繋ぐ/調べる/検証する generic CLI (macvm)。SSH 越しの健全性確認 (OS / arch / ディスク / GUI セッション / 任意ツールの有無)、ホスト側からの画面キャプチャ (screenshot)、prlctl からの IP 解決、繋がらないときのホスト側診断 (doctor)、任意ファイルの転送 (push/pull)、クォート/パイプ安全な任意コマンド実行 (exec) を扱う。GUI アプリをホストの作業を止めずに起動して目視したい時や、Parallels Desktop の macOS VM を操作・検証する時に使う。 |
| `skills/devops/windows-vm-verification` | Parallels Desktop 上の Windows 検証 VM を繋ぐ/調べる/検証する generic CLI (winvm)。SSH 越しの NTFS/health 確認、cfg(windows) コードの remote 検証 (ローカル変更を scp 同期して remote コマンド実行)、prlctl からの IP 解決、繋がらないときのホスト側診断 (doctor)、画面のスクリーンショット (screenshot)、任意ファイルの転送 (push/pull)、クォート/パイプ安全な任意 pwsh コマンド実行 (exec) を扱う。Parallels Desktop の Windows VM を操作・検証する時に使う。 |
| `skills/meta/context-loading-mechanics` | Coding Agent が session_start で何を必ずロードし、何を条件付きでロードするかの実測結果と、常時ロード層が膨らんだときの移設判断。公式ドキュメントに無い挙動を live probe で確定させたものを持つ。CLAUDE.md や rules が重いと感じたとき、規範を追加する前に置き場所を決めたいとき、paths 付き rules が発火しているか確かめたいとき、「指示を書いたのに効いていない」ときに使う。移設の判断と手順までが責務。 |
| `skills/meta/session-handoff` | セッションの作業状態を引き継ぎ書 `<リポルート>/.cache/handoff.md` に書き出す。hook (handoff-sentinel) の通知がこの skill を名指ししたとき、またはユーザーが手動で依頼したとき (「引き継ぎ書いて」「handoff して」「セッション切り替えたい」等) に使う。何を検知して通知するかは hook 側が持つ。書き出した引き継ぎ書を次のセッションへ載せるのは対になる SessionStart hook の責務で、hook が配線されていない環境では自動注入は起きない。このスキルは書き出しと案内までを持つ。 |
| `skills/tooling/chrome-devtools-debugger` | 公式 chrome-devtools-mcp plugin の skill 群で収集したデバッグ結果を、標準化された日本語レポート (docs/debug-reports/) へ整形・機密マスクするレイヤー。ネットワーク/コンソール/パフォーマンス/UI の調査結果を既知エラーパターンに対応づけ、優先度付きでレポート化する際に使用する。 |
| `skills/tooling/herdr` | herdr を内部から操作するためのツール。ワークスペースやタブの管理、ペインの分割、エージェントの起動、出力の読み取り、状態変化の待機まで、すべて CLI コマンドで対応する。各コマンドはローカルの Unix ソケット経由で、実行中の herdr インスタンスと通信する。herdr 内での実行時（HERDR_ENV=1）に使用する。 |
| `skills/tooling/markdown-to-pdf` | Markdownファイルを整形して PDF 化したいときに使用する。日本語ビジネス文書・技術ドキュメント・契約書ドラフト・計画書などを uv 経由のスタンドアロン Python スクリプト (render.py) で PDF に変換する。表組み・シンタックスハイライト・ヘッダー/フッター・ページ番号を含む整形済み PDF が必要なケース全般で使用する。 |

## 構造の規約

パッケージの形と命名には、破ってもエラーにならず静かに壊れる規約がいくつかある。

散文の約束にすると必ず drift するため、すべて `scripts/check-package-shape.py` の検査に落としてある。検査する項目の一覧は同スクリプトの docstring が canonical なので、ここには再掲しない。

`plugin.json` のフィールド定義そのものは `claude plugin validate --strict` が canonical。

## ライセンス

MIT
