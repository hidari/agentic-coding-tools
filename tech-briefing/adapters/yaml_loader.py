"""YAML の文書を読む。safe_load だけを使い、任意の Python のオブジェクトを作らせない。"""

import yaml


def load(text: str) -> object:
    # 入口は yaml.YAMLError を名指しできない (本体と入口の上の層は yaml を import しない) ので、
    # protocols.YamlLoader の約束どおり ValueError に揃える
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML として読めません: {e}") from None
