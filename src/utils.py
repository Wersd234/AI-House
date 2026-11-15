"""
Utility layer: JSON persistence + startup integrity check.

All JSON writes use atomic replace (write-tmp → os.replace) so a crash
never leaves a half-written file — required for Docker bind-mounts.
"""

import os
import json
import sys

from config import (
    logger, DATA_DIR, SETTINGS_FILE, DISCORD_TOKEN,
)


# ═══════════════════════════════════════════════════════════════════
# Generic JSON I/O
# ═══════════════════════════════════════════════════════════════════

def _load_json(path: str) -> dict:
    """Load a JSON dict from disk; return {} if missing or corrupted."""
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read JSON %s: %s", path, exc)
    return {}


def _save_json_atomic(path: str, data: dict) -> None:
    """Write JSON via temp-file + atomic rename (crash-safe)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ═══════════════════════════════════════════════════════════════════
# Server Settings (per-guild)
# ═══════════════════════════════════════════════════════════════════

def get_server_language(guild_id: str) -> str:
    """Return the language code ('zh' or 'en') for a Discord guild."""
    return _load_json(SETTINGS_FILE).get(str(guild_id), "zh")


def save_server_language(guild_id: str, lang: str) -> None:
    settings = _load_json(SETTINGS_FILE)
    settings[str(guild_id)] = lang
    _save_json_atomic(SETTINGS_FILE, settings)


def get_broadcast_channel(guild_id: str) -> str | None:
    """Return the channel configured for handover announcements, or None."""
    settings = _load_json(SETTINGS_FILE)
    return settings.get(f"{guild_id}_broadcast")


def save_broadcast_channel(guild_id: str, channel_id: str) -> None:
    settings = _load_json(SETTINGS_FILE)
    settings[f"{guild_id}_broadcast"] = channel_id
    _save_json_atomic(SETTINGS_FILE, settings)


# ═══════════════════════════════════════════════════════════════════
# Startup Integrity Check
# ═══════════════════════════════════════════════════════════════════

def run_startup_check() -> None:
    """Validate prerequisites before connecting to Discord."""
    logger.info("=== VRAM Suite Startup Self-Check ===")

    if not DISCORD_TOKEN or DISCORD_TOKEN == "your_token_here":
        logger.critical(
            "DISCORD_BOT_TOKEN is not set. Copy .env.example → .env and fill in your token."
        )
        sys.exit(1)

    os.makedirs(DATA_DIR, exist_ok=True)
    logger.info("Startup check passed — connecting to Discord...")
