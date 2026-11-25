"""MessagesClient の実物。anthropic の SDK の beta の messages.create を呼び、応答を API の JSON の形の dict で返す。

beta の側を使うのは、server-side fallback の `betas` と `fallbacks` を渡すため (claude-api skill 2.1.331 の
Python の例、2026-11-24 確認)。再試行は SDK の既定 (2回、429・5xx・接続の失敗) に任せる。

使う SDK の API は anthropic 2.7.0 の wheel を読んで確かめた (2026-11-25): `anthropic.Anthropic()` が鍵を
ANTHROPIC_API_KEY から読むこと、`client.beta.messages.create` が betas・fallbacks・output_config・thinking・最上位の cache_control を
受けて BetaMessage を返すこと、BetaMessage の基底の BaseModel が `to_dict(mode=, use_api_names=,
exclude_unset=)` を持つこと、状態コードを持つ例外が `status_code` を持つこと。版を上げたら確かめ直す。
"""

import sys

import anthropic


class SdkMessages:
    def __init__(self, client=None):
        # 鍵は SDK が ANTHROPIC_API_KEY から読む
        self._client = client if client is not None else anthropic.Anthropic()

    def create(self, params: dict) -> dict:
        try:
            resp = self._client.beta.messages.create(**params)
        except Exception as e:
            # ループは例外を種類を問わず api-error にするので、原因 (固定した版の SDK が引数を知らない、など) は
            # ここで内部のログに残す。本文は API の誤りの文面を含みうるので、型と状態コードだけを出す
            print(f"Messages API の呼び出しが失敗しました: {type(e).__name__} {getattr(e, 'status_code', '')}", file=sys.stderr)
            raise
        # 応答はそのまま次の要求の assistant の content に戻すので、API の名前 (from_ ではなく from) で、
        # 届いていない欄を足さない形にする
        return resp.to_dict(mode="json", use_api_names=True, exclude_unset=True)
