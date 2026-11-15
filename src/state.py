"""
State Engine (状态机) — tracks which character is currently awake and
persists it to disk.

The sniffer (sniffer.py) reports the raw character each poll; this module
owns the durable state and the *state-change hooks*:

  - update_character(): persist the new character and return whether it
    actually changed.
  - get_character(): return the current active character (QWEN /
    DEEPSEEK / ALL_SLEEP).
  - build_change_hook(): a thin callback the client wires to update the
    Discord presence / fire a handover announcement when the character
    flips.

Only one character is ever awake at a time — this is the "合租生态"
invariant enforced by the framework.
"""

import os
import json
import datetime
from typing import Optional, Callable

from config import logger, STATE_FILE, CHARACTER_KEYS


def _load_state() -> dict:
    """Load the state dict from disk, returning defaults if absent."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("character") in CHARACTER_KEYS:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "character": "ALL_SLEEP",
        "changed_at": None,
        "history": [],
    }


def _save_state(data: dict) -> None:
    """Persist state atomically (write-tmp → os.replace)."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def get_character() -> str:
    """Return the current active character key."""
    return _load_state().get("character", "ALL_SLEEP")


def update_character(new_character: str) -> bool:
    """
    Set the active character. Returns True if it actually changed
    (i.e. a handover happened), False if it stayed the same.

    Persists a small history entry for observability (/inspect).
    """
    if new_character not in CHARACTER_KEYS:
        logger.warning("Unknown character key '%s' — ignored", new_character)
        return False

    state = _load_state()
    old = state.get("character", "ALL_SLEEP")
    now = datetime.datetime.now().isoformat(timespec="seconds")

    if old == new_character:
        state.setdefault("changed_at", now)
        _save_state(state)
        return False

    state["character"] = new_character
    state["changed_at"] = now
    history = state.setdefault("history", [])
    history.append({
        "from": old,
        "to": new_character,
        "timestamp": now,
    })
    # Keep only the most recent entries
    state["history"] = history[-50:]
    _save_state(state)

    logger.info("🌀 Character handover: %s → %s (%s)", old, new_character, now)
    return True


def get_change_history() -> list[dict]:
    """Return the recent handover history (newest first)."""
    history = list(_load_state().get("history", []))
    history.reverse()
    return history


def on_change_hook(fn: Callable[[str, str], None], old: str, new: str) -> None:
    """Fire a state-change hook safely, swallowing any exception."""
    try:
        fn(old, new)
    except Exception as exc:
        logger.error("State-change hook failed (%s→%s): %s", old, new, exc)
