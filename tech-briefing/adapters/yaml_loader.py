"""YAML の文書を読む。safe_load だけを使い、任意の Python のオブジェクトを作らせない。"""

import yaml


def load(text: str) -> object:
    # 入口は yaml.YAMLError を名指しできない (本体と入口の上の層は yaml を import しない) ので、
    # protocols.YamlLoader の約束どおり ValueError に揃える。PyYAML の例外の文は誤りの行の断片を含むので、
    # 文には位置 (problem_mark の line と column、どちらも0始まり) だけを入れる (PyYAML 6.0.3 で確かめた)
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" ({mark.line + 1}行目 {mark.column + 1}列目)" if mark is not None else ""
        raise ValueError(f"YAML として読めません{where}") from None
