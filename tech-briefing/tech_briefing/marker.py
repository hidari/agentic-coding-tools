"""印付きのコメントの判定。select の処理済みの判定と入力の絞り込み、post の直前の再確認がここを使う。

判定を1か所に置き、select が投稿の整形 (render) を import せずに済むようにする。
"""

from tech_briefing.constants import BOT_LOGIN, MARKER
from tech_briefing.protocols import CommentRef


def is_marked(comment: CommentRef) -> bool:
    """本文が (先頭の空白を除いて) 印で始まるか。どのアカウントのコメントかは見ない。"""
    return comment.body.lstrip().startswith(MARKER)


def is_bot_report(comment: CommentRef) -> bool:
    """書き込みの名義の、印で始まるコメントか (処理済みの印)。名義は完全一致で比べる。

    select の処理済みの判定と post の直前の再確認の両方がこれを使い、2つの判定を食い違わせない。
    """
    return comment.author == BOT_LOGIN and is_marked(comment)
