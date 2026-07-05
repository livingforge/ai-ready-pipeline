"""LLM プロバイダごとの HTTP 呼び出し (標準ライブラリのみ)。

配布物 (スキルバンドル) に余計な依存を持ち込まないため、各社 SDK は使わず
``urllib.request`` で REST API を直接叩く。対応プロバイダと使用 API:

- openai:     POST {base}/chat/completions            (Authorization: Bearer)
- azure:      POST {endpoint}/openai/deployments/{dep}/chat/completions?api-version=…
- gemini:     POST {base}/models/{model}:generateContent   (x-goog-api-key)
- anthropic:  POST {base}/v1/messages                 (x-api-key + anthropic-version)

失敗時は :class:`ProviderError` に **status と応答本文の先頭だけ**を載せる
(リクエストヘッダ=API キーは決して含めない)。429/5xx は 1 回だけ再試行する。
テストや --dry-run では ``transport`` (url, headers, payload, timeout) -> dict を
差し替えて HTTP を発生させない。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .settings import LLMConfig

Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]

DEFAULT_TIMEOUT = 120.0  # 秒。長文要約は生成に時間がかかるため長め
_RETRY_STATUSES = {429, 500, 502, 503, 529}
_RETRY_WAIT = 5.0
_BODY_SNIPPET = 500  # エラー本文をこの文字数で切り詰めて載せる


class ProviderError(Exception):
    """LLM 呼び出しのユーザー向けエラー (API キー等の秘密は含めない)。"""


def _http_post_json(url: str, headers: dict[str, str],
                    payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """既定 transport。JSON を POST し、JSON を返す。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        snippet = e.read().decode("utf-8", errors="replace")[:_BODY_SNIPPET]
        raise ProviderError(
            f"LLM API がエラーを返しました (HTTP {e.code}): {snippet}"
        ) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ProviderError(
            f"LLM API へ接続できません: {e}。ネットワークとエンドポイント設定"
            " (BASE_URL / ENDPOINT) を確認してください"
        ) from e
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise ProviderError(
            f"LLM API の応答が JSON として読めません: {body[:_BODY_SNIPPET]}"
        ) from e


def _status_of(error: ProviderError) -> int | None:
    cause = error.__cause__
    return cause.code if isinstance(cause, urllib.error.HTTPError) else None


def _build_request(cfg: LLMConfig, system: str, user: str,
                   max_output_tokens: int) -> tuple[str, dict[str, str], dict]:
    """(url, headers, payload) を組み立てる。"""
    v = cfg.values
    if cfg.provider == "openai":
        url = v["OPENAI_BASE_URL"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {v['OPENAI_API_KEY']}"}
        payload = {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        return url, headers, payload
    if cfg.provider == "azure":
        url = (
            v["AZURE_OPENAI_ENDPOINT"].rstrip("/")
            + f"/openai/deployments/{cfg.model}/chat/completions"
            + f"?api-version={v['AZURE_OPENAI_API_VERSION']}"
        )
        headers = {"api-key": v["AZURE_OPENAI_API_KEY"]}
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        return url, headers, payload
    if cfg.provider == "gemini":
        url = (
            v["GEMINI_BASE_URL"].rstrip("/")
            + f"/models/{cfg.model}:generateContent"
        )
        headers = {"x-goog-api-key": v["GEMINI_API_KEY"]}
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_output_tokens},
        }
        return url, headers, payload
    if cfg.provider == "anthropic":
        url = v["ANTHROPIC_BASE_URL"].rstrip("/") + "/v1/messages"
        headers = {
            "x-api-key": v["ANTHROPIC_API_KEY"],
            "anthropic-version": v["ANTHROPIC_VERSION"],
        }
        payload = {
            "model": cfg.model,
            "max_tokens": max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        return url, headers, payload
    raise ProviderError(f"未対応のプロバイダです: {cfg.provider}")


def _extract_text(provider: str, data: dict[str, Any]) -> str:
    """プロバイダごとの応答から本文テキストを取り出す。"""
    try:
        if provider in ("openai", "azure"):
            return data["choices"][0]["message"]["content"] or ""
        if provider == "gemini":
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        if provider == "anthropic":
            if data.get("stop_reason") == "refusal":
                raise ProviderError(
                    "モデルが安全上の理由で応答を拒否しました (stop_reason=refusal)。"
                    " 対象文書を確認するか別のモデルを指定してください"
                )
            return "".join(
                b.get("text", "") for b in data.get("content", [])
                if b.get("type") == "text"
            )
    except ProviderError:
        raise
    except (KeyError, IndexError, TypeError) as e:
        raise ProviderError(
            f"LLM API の応答形式が想定と異なります ({provider}): "
            f"{json.dumps(data, ensure_ascii=False)[:_BODY_SNIPPET]}"
        ) from e
    raise ProviderError(f"未対応のプロバイダです: {provider}")


def complete(cfg: LLMConfig, system: str, user: str,
             max_output_tokens: int = 4096,
             timeout: float = DEFAULT_TIMEOUT,
             transport: Transport | None = None) -> str:
    """要約 1 件分の生成を実行し、本文テキストを返す。

    429/5xx (過負荷・一時障害) は ``_RETRY_WAIT`` 秒おいて 1 回だけ再試行する。
    """
    send = transport or _http_post_json
    url, headers, payload = _build_request(cfg, system, user, max_output_tokens)
    try:
        data = send(url, headers, payload, timeout)
    except ProviderError as e:
        status = _status_of(e)
        if status not in _RETRY_STATUSES:
            raise
        time.sleep(_RETRY_WAIT)
        data = send(url, headers, payload, timeout)
    text = _extract_text(cfg.provider, data).strip()
    if not text:
        raise ProviderError(
            f"LLM から空の応答が返りました ({cfg.provider}/{cfg.model})。"
            " モデル指定と入力サイズを確認してください"
        )
    return text
