"""
Model Sniffer (心跳嗅探器) — polls the local inference endpoint and
reports which character is currently awake.

This is the heart of the "显卡单间" worldview: the bot never lets users
switch characters — it simply detects which model the host loaded and
updates the active character accordingly.

Probe logic
-----------
1. GET <base>/v1/models (or /models) with a short timeout.
2. Inspect the first model ID returned:
   - contains "deepseek" → DEEPSEEK (大肥鱼)
   - contains "qwen"     → QWEN (千问大小姐)
   - otherwise            → ALL_SLEEP (unknown / not mapped)
3. Connection-refused or timeout → ALL_SLEEP (全员断电休眠).

The sniffer is a pure probe; it does NOT persist state. Persistence and
state-change hooks live in state.py, which is fed by this module.
"""

from typing import Optional

import httpx

from config import (
    logger, LLM_BASE_URL, LLM_API_KEY,
    SNIFFER_TIMEOUT_SEC, SNIFFER_PROBE_PATHS,
    CHARACTER_KEYWORDS,
)


def _detect_model_id() -> Optional[str]:
    """
    Query the inference endpoint and return the first model ID string,
    or None if the endpoint is unreachable / empty.

    The base URL may or may not include the /v1 suffix, so we try both
    probe paths against a cleaned base.
    """
    base = LLM_BASE_URL.rstrip("/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}

    for path in SNIFFER_PROBE_PATHS:
        try:
            resp = httpx.get(
                f"{base}{path}",
                timeout=SNIFFER_TIMEOUT_SEC,
                headers=headers,
            )
            if resp.status_code != 200:
                logger.debug("Probe %s%s → HTTP %s", base, path, resp.status_code)
                continue
            data = resp.json()
            models = data.get("data", [])
            if models and isinstance(models, list):
                model_id = models[0].get("id", "")
                if model_id:
                    return model_id
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
            logger.debug("Probe %s%s failed: %s", base, path, type(exc).__name__)
            continue
        except Exception as exc:
            logger.debug("Probe %s%s errored: %s", base, path, exc)
            continue
    return None


# ── Discovered model ID cache ──────────────────────────────────────
# The openai SDK *requires* a model field (Ollama validates it too),
# but we never hard-code one — the sniffer discovers whatever the host
# actually loaded and every chat/presence/memory call uses that.
_last_model_id: Optional[str] = None


def get_sniffed_model() -> Optional[str]:
    """The model ID discovered by the last successful probe (or None)."""
    return _last_model_id


def set_sniffed_model(model_id: Optional[str]) -> None:
    global _last_model_id
    _last_model_id = model_id


def sniff_character() -> str:
    """
    Probe the endpoint and return the active character key:
    'QWEN', 'DEEPSEEK', or 'ALL_SLEEP'.

    This is the single source of truth for "who is awake". A successful
    probe also caches the model ID (see get_sniffed_model).
    """
    model_id = _detect_model_id()
    if not model_id:
        set_sniffed_model(None)
        logger.info("Model sniffer: endpoint unreachable → ALL_SLEEP")
        return "ALL_SLEEP"

    set_sniffed_model(model_id)
    lowered = model_id.lower()
    for keyword, character in CHARACTER_KEYWORDS:
        if keyword in lowered:
            logger.info("Model sniffer: '%s' → %s", model_id, character)
            return character

    logger.info("Model sniffer: '%s' → ALL_SLEEP (unmapped model)", model_id)
    return "ALL_SLEEP"
