"""形を外部が決める値 (API の応答や LLM の提出) を読む小さな関数。

想定と違う型は空として読み、例外にしない。読む側は欄が欠けた値と同じ扱いで先へ進める。
"""


def as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def as_int(value: object) -> int | None:
    # bool は int の subclass だが、JSON の true / false を数や番号として読まない
    return value if isinstance(value, int) and not isinstance(value, bool) else None
