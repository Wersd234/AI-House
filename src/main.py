"""
VRAM Suite (显卡单间) — Discord Bot Entry Point.

A "shared-room" AI girl Discord bot. Two girls (千问大小姐 / 大肥鱼)
share a single VRAM bed and take shifts. The bot does NOT let users
switch characters — it sniffs which model the host loaded and swaps
the on-duty girl automatically.

Key behaviours:
  - Background model sniffer (sniffer.py) probes GET /v1/models every
    SNIFFER_INTERVAL_SEC seconds. A single failed probe is treated as
    "model loading" (加载中); only SNIFFER_FAILURES_TO_SLEEP consecutive
    failures flip the suite to ALL_SLEEP (全员断电休眠).
  - On character change: updates Discord presence + posts a handover
    announcement *as the newly-awake girl* (via webhook) in the
    configured broadcast channel.
  - Replies are sent through a channel Webhook carrying the on-duty
    girl's name + avatar (webhook.py) — dodge name/avatar rate limits.
  - When everyone is asleep (ALL_SLEEP), incoming messages get an
    immersive "机箱断电" refusal.
"""

import asyncio
import random
import re
from collections import deque

import discord
from discord.ext import tasks

from config import (
    logger, DISCORD_TOKEN, HISTORY_MAXLEN,
    NAME_TRIGGER_KEYWORDS,
    SNIFFER_INTERVAL_SEC, SNIFFER_FAILURES_TO_SLEEP,
    PRESENCE_BY_CHARACTER,
    SLEEP_REPLY, LOADING_REPLY,
)
from utils import (
    run_startup_check, get_server_language, get_broadcast_channel,
)
from state import get_character, update_character, get_change_history
from sniffer import sniff_character
from prompts import build_system_prompt, build_handover_note, build_loading_note
from llm_api import generate_reply
from webhook import send_as
from commands import setup_commands


class VrSuiteBot(discord.Client):
    def __init__(self, *args, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        kwargs["intents"] = intents
        super().__init__(*args, **kwargs)

        self.tree = discord.app_commands.CommandTree(self)

        # Per-channel: conversation history with the bot
        self.history: dict[str, deque] = {}

        # Sniffer failure counter — consecutive failures before sleep
        self._sniff_failures: int = 0

        # Guard: only announce a handover once per distinct change
        self._handover_announced: tuple[str, str] | None = None  # (from, to)

    # ── Lifecycle ───────────────────────────────────────────────

    async def setup_hook(self):
        setup_commands(self)
        self.model_sniffer_loop.start()
        await self.tree.sync()
        logger.info("Command tree synced.")

    async def on_ready(self):
        char = get_character()
        logger.info(
            "✅ VRS '%s' Online | 当前当值: %s",
            self.user.name, char,
        )
        await self._apply_presence(char)
        # Guild-scoped command sync = instant propagation
        for guild in self.guilds:
            try:
                await self.tree.sync(guild=guild)
            except Exception as exc:
                logger.warning("Guild command sync failed for %s: %s", guild.id, exc)

    # ── Presence ────────────────────────────────────────────────

    async def _apply_presence(self, character: str):
        """Update the bot's signature status to match the on-duty girl."""
        activity = PRESENCE_BY_CHARACTER.get(character, PRESENCE_BY_CHARACTER["ALL_SLEEP"])
        try:
            await self.change_presence(activity=discord.Game(name=activity))
            logger.info("Presence updated → %s: '%s'", character, activity)
        except Exception as exc:
            logger.warning("Presence update failed: %s", exc)

    # ── Model Sniffer Loop (心跳嗅探器) ─────────────────────────

    @tasks.loop(seconds=SNIFFER_INTERVAL_SEC)
    async def model_sniffer_loop(self):
        await self.wait_until_ready()
        new_char = sniff_character()

        if new_char == "ALL_SLEEP":
            # A single unreachable probe = model loading, NOT sleep.
            # Only sustained failures flip the suite off.
            self._sniff_failures += 1
            if self._sniff_failures < SNIFFER_FAILURES_TO_SLEEP:
                logger.info(
                    "Probe failed %d/%d — 模型加载中，保持当前角色",
                    self._sniff_failures, SNIFFER_FAILURES_TO_SLEEP,
                )
                return
        else:
            # Probe succeeded — reset the failure counter
            self._sniff_failures = 0

        # Only update the state machine when the character genuinely changed
        old_char = get_character()
        if new_char != old_char:
            changed = update_character(new_char)
            if changed:
                await self._apply_presence(new_char)
                await self._announce_handover(old_char, new_char)

    @model_sniffer_loop.before_loop
    async def _before_sniffer(self):
        await self.wait_until_ready()

    # ── Handover Announcement (交接班公告) ───────────────────────

    async def _announce_handover(self, old_char: str, new_char: str):
        """Post an in-character handover line, sent AS the newly-awake girl."""
        if new_char == "ALL_SLEEP":
            # Everyone left — post a generic "机箱断电" note instead.
            await self._announce_power_off()
            return
        if self._handover_announced == (old_char, new_char):
            return
        self._handover_announced = (old_char, new_char)

        for guild in self.guilds:
            gid = str(guild.id)
            cid = get_broadcast_channel(gid)
            if not cid:
                continue
            ch = guild.get_channel(int(cid))
            if ch and ch.permissions_for(guild.me).send_messages:
                try:
                    lang = get_server_language(gid)
                    note = build_handover_note(lang, old_char, new_char)
                    # Send AS the newly-awake girl (webhook identity)
                    await send_as(ch, new_char, note)
                    logger.info("📡 Handover %s→%s → guild %s", old_char, new_char, gid)
                except discord.HTTPException as e:
                    logger.warning("Handover announce fail: %s", e)

    async def _announce_power_off(self):
        """Post a generic '机箱断电' note when everyone falls asleep."""
        if self._handover_announced == ("*", "ALL_SLEEP"):
            return
        self._handover_announced = ("*", "ALL_SLEEP")
        for guild in self.guilds:
            gid = str(guild.id)
            cid = get_broadcast_channel(gid)
            if not cid:
                continue
            ch = guild.get_channel(int(cid))
            if ch and ch.permissions_for(guild.me).send_messages:
                try:
                    lang = get_server_language(gid)
                    note = (
                        "🔌 机箱断电了，合租屋门反锁。千问大小姐和大肥鱼都睡过去了。"
                        if lang == "zh" else
                        "🔌 Power cut — the suite is locked up. Miss Qwen and Fat Fish are both asleep."
                    )
                    await ch.send(note)
                    logger.info("📡 Power-off announce → guild %s", gid)
                except discord.HTTPException as e:
                    logger.warning("Power-off announce fail: %s", e)

    # ── Helpers ─────────────────────────────────────────────────

    def _history_as_messages(self, channel_id: str, current_name: str) -> list[dict]:
        out = []
        for entry in self.history[channel_id]:
            if entry.get("role") == "user":
                name = entry.get("name") or current_name
                out.append({
                    "role": "user",
                    "content": f"{name}: {entry.get('content', '')}",
                })
            else:
                out.append({"role": "assistant", "content": entry.get("content", "")})
        return out

    @staticmethod
    def _split_bubbles(text: str, max_len: int = 40, max_bubbles: int = 4) -> list[str]:
        """Split a reply into short 'chat bubbles' like a real member typing."""
        text = (text or "").strip()
        if not text:
            return []
        if len(text) <= max_len:
            return [text]
        pieces = re.split(r"(?<=[。！？!?~…；;:,、])", text)
        bubbles: list[str] = []
        for p in pieces:
            p = p.strip()
            if not p:
                continue
            if bubbles and len(bubbles[-1]) + len(p) <= max_len:
                bubbles[-1] += p
            else:
                bubbles.append(p)
        while len(bubbles) > max_bubbles:
            a = bubbles.pop(0)
            b = bubbles.pop(0)
            bubbles.insert(0, a + b)
        return bubbles

    # ═════════════════════════════════════════════════════════════
    # Message Handler
    # ═════════════════════════════════════════════════════════════

    async def on_message(self, message: discord.Message):
        if message.author == self.user or message.author.bot:
            return

        guild_id = str(message.guild.id) if message.guild else str(message.author.id)
        channel_id = str(message.channel.id)
        lang = get_server_language(guild_id)
        content = message.content

        is_mention = self.user in message.mentions
        is_reply_to_bot = (
            message.reference
            and getattr(message.reference.resolved, "author", None) == self.user
        )
        is_name_call = any(kw.lower() in (content or "").lower() for kw in NAME_TRIGGER_KEYWORDS)

        if not (is_mention or is_reply_to_bot or is_name_call):
            # For v1 we only reply to direct summons. A free-speaking /
            # attention model (like Amadeus' ChannelMind) can be grafted on
            # later without touching this path.
            return

        # ── Asleep check ─────────────────────────────────────────
        character = get_character()
        if character == "ALL_SLEEP":
            await message.channel.send(SLEEP_REPLY)
            return

        # ── History ──────────────────────────────────────────────
        if channel_id not in self.history:
            self.history[channel_id] = deque(maxlen=HISTORY_MAXLEN)

        async with message.channel.typing():
            user_text = (content or "").replace(f"<@{self.user.id}>", "").strip()

            # ── Assemble system prompt ──────────────────────────
            extra = ""
            # If we're mid-loading (recent probe failures), inject the loading note.
            if self._sniff_failures > 0:
                extra += build_loading_note(lang)

            # First turn after a handover: tell the new girl who just left.
            history_len = len(self.history[channel_id])
            if history_len == 0 and get_change_history():
                latest = get_change_history()[0]
                if latest["to"] == character:
                    extra += build_handover_note(lang, latest["from"], character)

            system_prompt = build_system_prompt(character, lang, extra_notes=extra)

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(self._history_as_messages(channel_id, message.author.display_name))
            messages.append({
                "role": "user",
                "content": f"{message.author.display_name}: {user_text or '...'}",
            })

            llm_resp = await generate_reply(messages)
            if llm_resp is None:
                await message.channel.send(LOADING_REPLY)
                return

            reply_text = llm_resp.get("reply", "……")

            # ── Update history ──────────────────────────────────
            self.history[channel_id].append({
                "role": "user",
                "content": user_text,
                "name": message.author.display_name,
            })
            self.history[channel_id].append({
                "role": "assistant", "content": reply_text,
            })

            # ── Reply through webhook (身份拟态) ────────────────
            bubbles = self._split_bubbles(reply_text)
            for i, bubble in enumerate(bubbles):
                await send_as(message.channel, character, bubble)
                if i != len(bubbles) - 1:
                    await asyncio.sleep(random.uniform(0.8, 1.5))


# ═══════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    run_startup_check()
    bot = VrSuiteBot()
    bot.run(DISCORD_TOKEN)
