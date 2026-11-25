"""tech-briefing 全体で共有する定数。他の module はここから import し、値を再掲しない。"""

# 処理済みを示す印。コメント本文の先頭に置き、次回 run の判定に使う
MARKER = "<!-- tech-briefing-triage -->"
# 書き込みの名義
BOT_LOGIN = "github-actions[bot]"
# 対象 Issue のタイトルの接頭辞
TITLE_PREFIX = "[tech-briefing] "
# 「当日」を決める時刻帯
TIMEZONE = "Asia/Tokyo"
# URL を本文から落とすときの置き換え。ホスト名だけを残す
URL_REPLACEMENT = "[link: {host}]"
# ホストを取り出せない URL の host 部。原文の一部を残さないための固定表現
URL_UNKNOWN_HOST = "unknown"

MODEL = "claude-opus-5-5"
EFFORT = "high"
BETA = "server-side-fallback-2026-07-01"
FALLBACKS = "default"
WEB_FETCH_TOOL = "web_fetch_20260318"
WEB_SEARCH_TOOL = "web_search_20260209"

# select の結果の値
SELECT_NO_ISSUE = "no-issue"
SELECT_WRONG_AUTHOR = "wrong-author"
SELECT_TITLE_MISMATCH = "title-mismatch"
SELECT_ALREADY_PROCESSED = "already-processed"
SELECT_TRIAGE = "triage"
SELECT_UNEXPECTED = "unexpected"
SELECT_INPUT_TOO_LARGE = "input-too-large"
SELECT_CONFIG_ERROR = "config-error"
SELECT_RESULTS = (
    SELECT_NO_ISSUE,
    SELECT_WRONG_AUTHOR,
    SELECT_TITLE_MISMATCH,
    SELECT_ALREADY_PROCESSED,
    SELECT_TRIAGE,
    SELECT_UNEXPECTED,
    SELECT_INPUT_TOO_LARGE,
    SELECT_CONFIG_ERROR,
)
# 赤 (job 失敗) で終わる結果
SELECT_RED_RESULTS = (
    SELECT_WRONG_AUTHOR,
    SELECT_TITLE_MISMATCH,
    SELECT_CONFIG_ERROR,
)
# post へ進む結果。unexpected と input-too-large は triage を飛ばして post が定型のコメントにする。
# 各値の本文は render.plan_post が決める
POSTED_RESULTS = (
    SELECT_TRIAGE,
    SELECT_UNEXPECTED,
    SELECT_INPUT_TOO_LARGE,
)

# 報告の分類。報告の schema の enum と、投稿の本文に載せてよい値の両方がここから作る
CLASSIFICATIONS = ("A", "B", "C", "D")

# 失敗の種類
FAIL_API_ERROR = "api-error"
FAIL_LOOP_LIMIT = "loop-limit"
FAIL_TOKEN_LIMIT = "token-limit"
FAIL_REFUSAL = "refusal"
FAIL_NO_REPORT = "no-report"
FAIL_INVALID_REPORT = "invalid-report"
FAIL_INTERNAL_ERROR = "internal-error"
FAILURE_KINDS = (
    FAIL_API_ERROR,
    FAIL_LOOP_LIMIT,
    FAIL_TOKEN_LIMIT,
    FAIL_REFUSAL,
    FAIL_NO_REPORT,
    FAIL_INVALID_REPORT,
    FAIL_INTERNAL_ERROR,
)

# official_sources に入れてはならないホスト。誰でも書き込める場所なので、許可すると
# 検索と取得の対象が攻撃者の投稿まで広がる。親ドメインも同じ理由で拒否する
DENY_HOSTS = (
    "github.com",
    "gist.github.com",
    "raw.githubusercontent.com",
    "gitlab.com",
    "bitbucket.org",
)
# 配下のサブドメインを第三者が取得できるホスティングの接尾辞。接尾辞自身と配下をすべて拒否する
DENY_SUFFIXES = (
    "github.io",
    "githubusercontent.com",
    "gitlab.io",
    "pages.dev",
    "netlify.app",
    "vercel.app",
)
