"""YAML の文書を読む。safe_load だけを使い、任意の Python のオブジェクトを作らせない。"""

import yaml


def load(text: str) -> object:
    # 入口は PyYAML の例外の型を名指しできない (本体と入口の上の層は yaml を import しない) ので、
    # protocols.YamlLoader の約束どおり ValueError に揃える。型は問わずに受ける。YAMLError のほかに constructor が
    # 値を含む文の ValueError・KeyError なども上げる (`!!int` や `!!bool` を付けた値など) ため。文には約束どおり
    # 文書の中身を入れず、位置 (problem_mark の line と column、どちらも0始まり) だけを入れる
    # (どれも PyYAML 6.0.3 で確かめた)
    try:
        return yaml.safe_load(text)
    except Exception as e:
        mark = getattr(e, "problem_mark", None)
        where = f" ({mark.line + 1}行目 {mark.column + 1}列目)" if mark is not None else ""
        raise ValueError(f"YAML として読めません{where}") from None
