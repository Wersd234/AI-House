"""
LLM API interface — thin wrapper around OpenAI-compatible backends.

Supports:
  - Standard chat completions (vLLM / Ollama / SGLang / LMDeploy / DeepSeek)
  - JSON-mode extraction from model output (brace-matching, not regex)
  - Reply sanitising: strips chain-of-thought wrappers, inner-monologue,
    and excess punctuation so the girl "talks like a real group member".

This module is largely inherited from Project Amadeus' llm_api.py.
"""

import datetime
import json
import re
from openai import AsyncOpenAI

from config import (
    logger, LLM_BASE_URL, LLM_API_KEY,
    CHAT_SAMPLING, CHAT_SAMPLING_EXTRA, MEMORY_SAMPLING,
    MAX_REPLY_TOKENS, MAX_MEMORY_TOKENS,
    PRESENCE_MAX_TOKENS, PRESENCE_SAMPLING,
    SYSTEM_PROMPTS,
)
from sniffer import get_sniffed_model

# Single shared client — httpx connection pooling is handled internally.
# Model name is intentionally NOT hard-coded; the caller may override
# via kwargs (see generate_reply's model kwarg).
_llm_client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)


# ═══════════════════════════════════════════════════════════════════
# JSON Extraction
# ═══════════════════════════════════════════════════════════════════

def _extract_json(text: str) -> dict | None:
    """Extract the first valid JSON object using brace-depth balancing."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


# ═══════════════════════════════════════════════════════════════════
# Reply Sanitising
# ═══════════════════════════════════════════════════════════════════

_COT_PATTERNS = [
    (r"<\|channel>thought.*?(?=\n\n|\Z)", "", re.DOTALL),
    (r"<\|assistant>thought.*?(?=\n\n|\Z)", "", re.DOTALL),
    (r"<\|user>thought.*?(?=\n\n|\Z)", "", re.DOTALL),
    (r"<thinking>.*?</thinking>", "", re.DOTALL),
    (r"<reasoning>.*?</reasoning>", "", re.DOTALL),
    (r"\[thinking\].*?\[/thinking\]", "", re.DOTALL),
]


def _strip_fw_narration(text: str) -> str:
    """Strip inner-monologue wrapped in full-width parens （…）/）… lines."""
    text = re.sub(r"（[^（]*?）", "", text, flags=re.DOTALL)
    kept = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if re.fullmatch(r"[（）]+", s):
            continue
        if s.startswith("）"):
            continue
        kept.append(s)
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"（[^（]*?）", "", cleaned)
    cleaned = cleaned.lstrip("（） ").rstrip("（） ")
    return cleaned.strip()


def _clean_punctuation(text: str) -> str:
    """Collapse repeated punctuation, normalise ellipsis, drop stray lead."""
    if not text:
        return text
    text = re.sub(r"\.{3,}", "……", text)
    text = re.sub(r"…+", "……", text)
    text = re.sub(r"([！？，。；：、,;:!?])\1{1,}", r"\1", text)
    text = re.sub(r"^[！？，。；：、,;:\s…]+", "", text)
    return text.strip()


def clean_reply_text(text: str) -> str:
    """Remove leaked chain-of-thought / inner-monologue / stray JSON."""
    if not text:
        return "……"
    cleaned = text
    for pat, repl, *flags in _COT_PATTERNS:
        cleaned = re.sub(pat, repl, cleaned, flags=flags[0] if flags else 0)
    cleaned = cleaned.replace("</thinking>", "").replace("[think]", "")
    cleaned = _strip_fw_narration(cleaned)
    cleaned = _clean_punctuation(cleaned)
    return cleaned.strip() or "……"


# ═══════════════════════════════════════════════════════════════════
# Core Chat Call
# ═══════════════════════════════════════════════════════════════════

async def _chat_once(
    messages: list[dict],
    max_tokens: int,
    sampling: dict,
    extra: dict | None = None,
    model: str | None = None,
) -> str | None:
    """
    Single-turn call to the LLM.

    model : str | None
        Which model field to send. Resolution order:
        explicit arg → the ID discovered by the sniffer → "default".
        (The openai SDK *requires* the field; Ollama validates it too.
        We never hard-code a persona model — the sniffer reports
        whatever the host actually loaded, keeping "no user switching"
        and single-room VRAM semantics intact.)
    """
    kwargs = dict(
        messages=messages,
        max_tokens=max_tokens,
        model=model or get_sniffed_model() or "default",
        **sampling,
    )
    if extra:
        kwargs["extra_body"] = extra

    response = await _llm_client.chat.completions.create(**kwargs)
    if not getattr(response, "choices", None) or len(response.choices) == 0:
        return None
    return response.choices[0].message.content


# ═══════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════

async def generate_reply(
    messages: list[dict],
    model: str | None = None,
) -> dict | None:
    """
    Send conversation to LLM and parse structured JSON response.

    Returns dict with keys: {"reply", "emotion", ...}, or None on total
    failure.

    model : str | None
        Optional explicit model id (see _chat_once). Leave None to use
        whichever model the backend has loaded.
    """
    try:
        raw = await _chat_once(messages, MAX_REPLY_TOKENS, CHAT_SAMPLING, CHAT_SAMPLING_EXTRA, model)
    except Exception as exc:
        logger.error("LLM API error: %s", exc)
        return None

    if not raw:
        return None

    parsed = _extract_json(raw.strip())
    if parsed is not None:
        if isinstance(parsed.get("reply"), str):
            parsed["reply"] = clean_reply_text(parsed["reply"])
        # Optional reaction (DC replacement for QQ's poke) — validated,
        # dropped if malformed so a hallucinated value never reaches the API.
        from preprocess import clean_reaction
        react = clean_reaction(parsed.get("reaction"))
        if react:
            parsed["reaction"] = react
        else:
            parsed.pop("reaction", None)
        return parsed

    # Model didn't output JSON — use the raw text as the reply
    return {"emotion": "other", "reply": clean_reply_text(raw.strip())}


async def generate_presence(
    character: str,
    lang: str = "zh",
    model: str | None = None,
    now: datetime.datetime | None = None,
) -> str | None:
    """
    Ask the on-duty girl to invent her own Discord signature status.

    Uses her role card (SYSTEM_PROMPTS[character][lang]) as the persona
    and asks for a short, in-character "正在……" line. Returns the raw
    text, or None on any failure so the caller can fall back to the
    static PRESENCE_BY_CHARACTER strings.

    character : str
        'QWEN' or 'DEEPSEEK' (ALL_SLEEP is never LLM-generated).
    lang : str
        'zh' or 'en'.
    model : str | None
        Optional explicit model id; None = whatever is loaded.
    now : datetime.datetime | None
        The current system time to make the status time-aware. Defaults
        to the current local time. Pass it in so callers can test with a
        fixed clock.
    """
    role_card = SYSTEM_PROMPTS.get(character, {}).get(lang) \
        or SYSTEM_PROMPTS.get(character, {}).get("zh", "")
    if not role_card:
        logger.warning("No role card for %s — skipping LLM presence", character)
        return None

    now = now or datetime.datetime.now()
    time_str = now.strftime("%H:%M")
    weekday = now.strftime("%A")

    if lang == "zh":
        instruction = (
            f"现在是{weekday}的 {time_str}。"
            "你现在是 Discord 上的一名在线少女。请基于你自己的角色卡，"
            "结合当前的具体时间与时段，用一句话、以你自己此刻的语气和性格，"
            "写下你的『签名状态』（就是 Discord 昵称下面那行小字，"
            "格式类似“傍晚在花园喝下午茶”、“深夜在书房读书”、“午后在泳池里吐泡泡”）。"
            "要求：只输出那一句签名本身，不要任何解释、引号、标点结尾或前后缀，不超过 12 个字。"
        )
    else:
        instruction = (
            f"It is {weekday}, {time_str}. "
            "You are an online girl on Discord. Based on your role card and "
            "the current time of day, write your status line in one short "
            "sentence, in your own voice and personality (e.g. \"sipping "
            "evening tea in the garden\", \"reading late in the study\"). "
            "Requirements: output ONLY that status line — no explanation, "
            "quotes, trailing punctuation, or prefixes. Keep it under 12 words."
        )

    messages = [
        {"role": "system", "content": role_card},
        {"role": "user", "content": instruction},
    ]
    try:
        raw = await _chat_once(messages, PRESENCE_MAX_TOKENS, PRESENCE_SAMPLING, None, model)
    except Exception as exc:
        logger.error("LLM presence generation failed: %s", exc)
        return None

    if not raw:
        return None

    # Clean: strip quotes/parens/prefixes, collapse whitespace, cut length.
    text = raw.strip().strip("“”\"'（）()【】").split("\n")[0].strip()
    return text[:24] or None


async def summarize_memory(current_memory: str, history: list[dict], lang: str, model: str | None = None) -> str:
    """Ask the LLM to extract key facts and merge with existing long-term memory."""
    if lang == "zh":
        instruction = (
            "请提取以下对话中关于各个用户的关键事实（例如：用户的昵称、喜好、作息、说过的重要事情等）。"
            "每条事实必须注明是属于哪个用户（按昵称区分，不同昵称是不同的用户，不要合并）。"
            "将新事实与[现有记忆]合并，写成一段简短的客观总结。不要输出任何废话、主观感受、"
            "思考过程或对话格式，只输出事实本身。"
        )
    else:
        instruction = (
            "Extract key facts about EACH user from the conversation below "
            "(nickname, preferences, schedule, important things they said). "
            "Attribute every fact to the correct user by their nickname — different "
            "nicknames are different people, never merge them. "
            "Merge new facts with [Existing Memory] into a single concise paragraph "
            "of objective facts. Output only the facts — no thought process, "
            "commentary, or conversational filler."
        )

    chat_text = ""
    for msg in history:
        content = msg.get("content", "")
        if isinstance(content, list):
            content = "[sent an image]"
        name = msg.get("name", "")
        if name:
            chat_text += f"用户 {name}: {content}\n"
        else:
            chat_text += f"{msg['role']}: {content}\n"

    prompt = (
        f"{instruction}\n\n"
        f"[Existing Memory]: {current_memory or '(empty)'}\n\n"
        f"[Recent Conversation]:\n{chat_text}"
    )

    try:
        raw = await _chat_once(
            [{"role": "user", "content": prompt}],
            MAX_MEMORY_TOKENS,
            MEMORY_SAMPLING,
            {"cache_prompt": True},
            model,
        )
        if raw:
            raw = raw.strip()
            if "<|channel>thought" in raw:
                cleaned = re.sub(r"<\|channel>thought.*?(?=\n\n|\Z)", "", raw, flags=re.DOTALL).strip()
                return cleaned or raw
            return raw
    except Exception as exc:
        logger.error("Memory summarisation failed: %s", exc)

    return current_memory
