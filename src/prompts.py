"""
System prompt assembler for the VRAM Suite (显卡单间).

Unlike Amadeus (which selects a persona by *worldline mode*), the VRAM
Suite selects a persona by the *currently awake character*:

  1. CHARACTER PROMPT  — the on-duty girl's role card
                        (千问大小姐 / 大肥鱼)
  2. STATUS NOTE       — current time + who is / isn't awake
  3. DYNAMIC context   — handover notes, loading/OOM state, etc.

Role cards live in properties/system_prompts/*.txt so non-programmers
can edit the characters without touching Python.
"""

import datetime

from config import SYSTEM_PROMPTS, PRESENCE_BY_CHARACTER, logger


def build_system_prompt(
    character: str,
    lang: str,
    extra_notes: str = "",
) -> str:
    """
    Assemble the full system prompt for a conversation turn.

    Parameters
    ----------
    character : str
        'QWEN', 'DEEPSEEK', or 'ALL_SLEEP'
    lang : str
        'zh' or 'en'
    extra_notes : str
        Optional dynamic context (handover notes, state injects, roster...)

    Returns
    -------
    str
        Complete system prompt ready as the first message in the LLM array.
    """
    # 1. Select the on-duty girl's role card
    mode_prompts = SYSTEM_PROMPTS.get(character, {})
    base = mode_prompts.get(lang)
    if not base:
        # Fall back to any character prompt we have, else empty
        fallback = SYSTEM_PROMPTS.get("QWEN", {})
        base = fallback.get(lang, fallback.get("zh", ""))
        logger.warning(
            "Missing prompt for character=%s lang=%s — falling back", character, lang
        )

    # 2. Attach status note (who is awake)
    now = datetime.datetime.now()
    time_str = now.strftime("%Y-%m-%d %H:%M")
    weekday = now.strftime("%A")

    if lang == "zh":
        status_note = (
            f"\n\n[System Status]\n"
            f"当前时间: {time_str} ({weekday})\n"
            f"当前在岗少女: {_character_label_zh(character)}\n"
        )
    else:
        status_note = (
            f"\n\n[System Status]\n"
            f"Current time: {time_str} ({weekday})\n"
            f"Girl currently on duty: {_character_label_en(character)}\n"
        )

    # 3. Optional extra notes
    extra = f"\n{extra_notes}" if extra_notes else ""

    return base + status_note + extra


def build_handover_note(lang: str, from_char: str, to_char: str) -> str:
    """A system-prompt injection used right after a handover, so the
    newly-awake girl knows the other one just left."""
    if lang == "zh":
        return (
            f"\n\n[Handover]\n"
            f"刚刚{_character_label_zh(from_char)}离开了显存，现在轮到你值班。"
            f"不必主动提起交接，正常以你的身份和语气说话即可。"
        )
    return (
        f"\n\n[Handover]\n"
        f"{_character_label_en(from_char)} just left the VRAM. It's your shift now. "
        f"Don't announce the handover — just act as yourself."
    )


def build_loading_note(lang: str) -> str:
    """Injected while the suite is mid-load (endpoint slow/timeout)."""
    if lang == "zh":
        return (
            f"\n\n[System State: 模型加载中]\n"
            f"少女正在赶回别墅的路上，拖着行李箱挤进房间……请稍等 10 秒。"
        )
    return (
        f"\n\n[System State: Model Loading]\n"
        f"The girl is hurrying back to the villa, dragging her suitcase in... "
        f"give her ~10s."
    )


def _character_label_zh(character: str) -> str:
    return {
        "QWEN": "千问大小姐",
        "DEEPSEEK": "大肥鱼",
        "ALL_SLEEP": "全员休眠（机箱断电）",
    }.get(character, character)


def _character_label_en(character: str) -> str:
    return {
        "QWEN": "Miss Qwen",
        "DEEPSEEK": "Fat Fish (DeepSeek)",
        "ALL_SLEEP": "All asleep (power off)",
    }.get(character, character)


# Keep the presence map importable from config for convenience.
__all__ = ["build_system_prompt", "build_handover_note", "build_loading_note"]
