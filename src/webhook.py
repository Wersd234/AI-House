"""
Webhook Identity Engine (Webhook 身份拟态引擎).

To dodge Discord's severe rate limits on renaming / re-avataring the bot
itself, every character's messages are forged through a per-guild channel
Webhook that carries the character's name + avatar.

Responsibilities:
  - Lazily find-or-create one webhook per (guild, character) and cache it
    in data/webhooks.json (keyed by guild_id → {character: webhook_id}).
  - send_as(character, ...) posts a message with the character's name +
    avatar through the webhook, so replies "become" whichever girl is awake.

Webhook creation requires manage_webhooks permission; the bot degrades
gracefully to sending as the bot itself if it lacks permission.
"""

import os
import json
from typing import Optional

import discord

from config import logger, WEBHOOK_IDENTITY, WEBHOOKS_FILE


# ── Webhook cache persistence ───────────────────────────────────────

def _load_webhooks() -> dict:
    """Load {guild_id: {character: webhook_id}} from disk."""
    try:
        if os.path.exists(WEBHOOKS_FILE):
            with open(WEBHOOKS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read webhook cache: %s", exc)
    return {}


def _save_webhooks(data: dict) -> None:
    os.makedirs(os.path.dirname(WEBHOOKS_FILE), exist_ok=True)
    tmp = WEBHOOKS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, WEBHOOKS_FILE)


# ── Webhook lookup / creation ───────────────────────────────────────

async def _get_or_create_webhook(
    channel: discord.TextChannel,
    character: str,
) -> Optional[discord.Webhook]:
    """
    Return a cached webhook for (channel.guild, character), or create one.

    The webhook name/avatar are pulled from WEBHOOK_IDENTITY[character].
    """
    guild_id = str(channel.guild.id)
    cache = _load_webhooks()
    char_cache = cache.setdefault(guild_id, {})

    # 1. Try the cached id
    cached_id = char_cache.get(character)
    if cached_id:
        try:
            wh = await channel.guild.fetch_webhook(int(cached_id))
            if wh.channel_id == channel.id:
                return wh
        except (discord.NotFound, discord.Forbidden, ValueError):
            pass  # stale / forbidden — recreate below

    # 2. Create a fresh webhook on this channel
    identity = WEBHOOK_IDENTITY.get(character, {})
    name = identity.get("name", "少女")[:80]
    avatar_url = identity.get("avatar_url") or None

    try:
        wh = await channel.create_webhook(name=name, avatar=avatar_url)
    except (discord.Forbidden, discord.HTTPException) as exc:
        logger.warning("Webhook create failed in #%s (%s): %s", channel.name, guild_id, exc)
        return None

    char_cache[character] = str(wh.id)
    _save_webhooks(cache)
    logger.info("🪄 Created webhook '%s' in guild %s", name, guild_id)
    return wh


async def send_as(
    channel: discord.TextChannel,
    character: str,
    content: str,
    *,
    embed: Optional[discord.Embed] = None,
) -> None:
    """
    Send a message AS the given character (name + avatar via webhook).

    Degrades to the bot's own identity if webhooks are unavailable.
    """
    wh = await _get_or_create_webhook(channel, character)
    if wh is None:
        # Fallback: send as the bot itself
        await channel.send(content=content, embed=embed)
        return

    kwargs = {"content": content, "wait": True}
    if embed is not None:
        kwargs["embed"] = embed
    try:
        await wh.send(**kwargs)
    except discord.HTTPException as exc:
        logger.warning("Webhook send failed: %s — falling back to bot message", exc)
        await channel.send(content=content, embed=embed)
