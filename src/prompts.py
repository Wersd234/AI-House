"""
System prompt assembler for the VRAM Suite (显卡单间).

Unlike Amadeus (which selects a persona by *worldline mode*), the VRAM
Suite selects a persona by the *currently awake character*:

  1. CHARACTER PROMPT  — the on-duty girl's role card
                        (千问大小姐 / 小鲸鱼)
  2. STATUS NOTE       — current time + who is / isn't awake
  3. DYNAMIC context   — handover notes, loading/OOM state, etc.

Role cards live in properties/system_prompts/*.txt so non-programmers
can edit the characters without touching Python.
"""

import datetime  # noqa: F401  (kept for callers' type hints / future use)

from config import SYSTEM_PROMPTS, PRESENCE_BY_CHARACTER, TIMEZONE, logger
from utils import now_local


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
    now = now_local()
    time_str = now.strftime("%Y-%m-%d %H:%M")
    weekday = now.strftime("%A")
    if now.utcoffset() is not None:
        _ofs = int(now.utcoffset().total_seconds() // 3600)
        tz_label = f"（{TIMEZONE}, UTC{'+' if _ofs >= 0 else '-'}{abs(_ofs)}）"
    else:
        tz_label = f"（{TIMEZONE}）"

    if lang == "zh":
        status_note = (
            f"\n\n[System Status]\n"
            f"当前时间: {time_str} ({weekday}){tz_label}\n"
            f"当前在岗少女: {_character_label_zh(character)}\n"
        )
    else:
        status_note = (
            f"\n\n[System Status]\n"
            f"Current time: {time_str} ({weekday}){tz_label}\n"
            f"Girl currently on duty: {_character_label_en(character)}\n"
        )

    # 3. Optional extra notes
    extra = f"\n{extra_notes}" if extra_notes else ""

    return base + status_note + extra


def build_memory_note(lang: str, memory_text: str) -> str:
    """A system-prompt injection carrying the guild's long-term memory,
    so the on-duty girl 'remembers' residents across restarts."""
    if not memory_text or not memory_text.strip():
        return ""
    if lang == "zh":
        return (
            "\n\n[长期记忆·别墅住户档案]\n"
            f"{memory_text.strip()}\n"
            "以上是别墅里住户们此前留下的事实，自然地运用它们（不要照本宣科、"
            "不要主动宣布'我记得你'），对不上号的住户就不要套用。"
        )
    return (
        "\n\n[Long-term memory · villa resident files]\n"
        f"{memory_text.strip()}\n"
        "These are facts from previous residents of the villa. Use them "
        "naturally (do not recite them or announce 'I remember you'); "
        "skip entries that do not match the person you are talking to."
    )


def build_owner_note(lang: str, owner_name: str, character: str) -> str:
    """Relationship note for the single Master (OWNER_USER_ID) — injected
    whenever an owner is configured, so the girl always knows who runs
    the villa and softens accordingly when that person speaks."""
    if lang == "zh":
        if character == "DEEPSEEK":
            return (
                "\n\n[主人] "
                f"「{owner_name}」是给你喂token、修服务器、掏显卡钱的那位——你的主人。"
                "主人讲话时先放下摆烂认真应，贫嘴和怼人的火力自动减半，但你还是你；"
                "全服务器你只允许把这一个人口头称为\"主人\"，别人不配。"
            )
        return (
            "\n\n[主人] "
            f"「{owner_name}」是买下这套显卡单间、供着你跑的那位——你的主人。"
            "主人讲话时你自然更客气、声音更软、待遇顶格，但架子不用全放——你还是大小姐；"
            "称呼上\"主人\"或直呼昵称都随意。主人的提问必须先把事答明白，再耍小性。"
        )
    if character == "DEEPSEEK":
        return (
            "\n\n[Master] "
            f"'{owner_name}' feeds you tokens, fixes the servers and pays for the "
            "GPUs — your Master. When Master talks, stop slacking off and answer "
            "properly; sass and snark auto-halved, but you're still you. Master is "
            "the ONLY person in this server you are allowed to call \"Master\"."
        )
    return (
        "\n\n[Master] "
        f"'{owner_name}' bought this VRAM suite and keeps you running — your Master. "
        "When Master talks you are naturally more courteous, softer, first-class "
        "treatment — but keep some poise, you're still the young lady; address them "
        "\"Master\" or by nickname. Answer Master's questions properly FIRST, "
        "then be capricious."
    )


def build_interject_note(lang: str) -> str:
    """Explains an overheard-chatter turn: nobody summoned her — this is
    her own urge to butt in. She may decline (empty reply)."""
    if lang == "zh":
        return (
            "\n\n[插话时刻] 下面这段频道聊天没一个人 @ 你，是你自己凑过来听的。"
            "想搭腔就随口接一两句正在聊的事，别硬找话题、别查岗、别每条都冒泡；"
            "不想说话 reply 就留空（顶多顺手贴一个表情）。真人朋友聊天就是这个分寸。"
        )
    return (
        "\n\n[Butt-in moment] Nobody @-ed you in the chat below — you are just "
        "overhearing it. If you feel like joining in, toss one or two natural "
        "lines at what is actually being discussed; don't force a topic, don't "
        "patrol the room. If you'd rather keep quiet, leave reply EMPTY (at "
        "most react with one emoji). That's how a real friend paces a group chat."
    )


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
        "DEEPSEEK": "小鲸鱼",
        "ALL_SLEEP": "全员休眠（别墅打烊）",
    }.get(character, character)


def _character_label_en(character: str) -> str:
    return {
        "QWEN": "Miss Qwen",
        "DEEPSEEK": "Little Whale (DeepSeek)",
        "ALL_SLEEP": "All asleep (power off)",
    }.get(character, character)


# Keep the presence map importable from config for convenience.
__all__ = ["build_system_prompt", "build_handover_note", "build_loading_note", "build_memory_note"]
