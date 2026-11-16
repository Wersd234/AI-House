"""
VRAM Suite (显卡单间) — Discord Bot Entry Point.

A "shared-room" AI girl Discord bot. Two girls (千问大小姐 / 小鲸鱼)
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
    immersive "别墅打烊" refusal.
"""

import asyncio
import base64
import random
import re
import time
from collections import deque

import discord
from discord.ext import tasks

from config import (
    logger, DISCORD_TOKEN, HISTORY_MAXLEN, HISTORY_FILE,
    NAME_TRIGGER_KEYWORDS,
    SNIFFER_INTERVAL_SEC, SNIFFER_FAILURES_TO_SLEEP,
    PRESENCE_BY_CHARACTER, LLM_PRESENCE_ENABLED, PRESENCE_REFRESH_SEC,
    MEMORY_ENABLED, MEMORY_EVERY_N_MESSAGES,
    FOLLOWUP_WINDOW_SEC,
    OWNER_USER_ID, INTERJECT_ENABLED, INTERJECT_CHANCE,
    INTERJECT_COOLDOWN_SEC,
    VISION_CAPABILITY, IMAGE_INLINE_MAX_BYTES, MAX_IMAGE_ATTACHMENTS,
    EMOJI_ROSTER_SIZE, STICKER_AUTO_COLLECT, REACTIONS_ENABLED,
    SLEEP_REPLY, LOADING_REPLY,
)
import preprocess
import stickers
from utils import (
    run_startup_check, get_server_language, get_broadcast_channel,
    get_guild_memory, save_guild_memory, load_history, save_history,
)
from state import get_character, update_character, get_change_history
from sniffer import sniff_character
from prompts import (
    build_system_prompt, build_handover_note, build_loading_note,
    build_memory_note, build_owner_note, build_interject_note,
)
from llm_api import generate_reply, generate_presence, summarize_memory
from webhook import send_as
from commands import setup_commands


def collect_media(message: discord.Message) -> tuple[list, list[str]]:
    """
    Gather media sources on/around a message (GAP #9.9) — Discord images
    live in FOUR places, QQ only had one:

      attachments        uploaded images (incl. forwarded-snapshot ones)
      message.stickers   server stickers (Lottie/JSON filtered out, PNG only)
      embeds             Tenor/Giphy GIFs and link previews land here
      custom emoji       <:name:id> inside text → its CDN png

    Returns (attachments, plain_urls), capped at MAX_IMAGE_ATTACHMENTS total.
    """
    cap = max(MAX_IMAGE_ATTACHMENTS, 1)
    atts: list = []
    urls: list[str] = []

    def full() -> bool:
        return len(atts) + len(urls) >= cap

    def add_att(a):
        ctype = getattr(a, "content_type", None) or ""
        if ctype.startswith("image/") and not full():
            atts.append(a)

    for a in (message.attachments or []):
        add_att(a)
    # forwarded message snapshots (discord.py ≥ 2.4)
    for snap in (getattr(message, "message_snapshots", None) or []):
        for a in (getattr(snap, "attachments", None) or []):
            add_att(a)
    # server stickers — PNG preview only (Lottie/JSON animation can't be
    # fed to a vision model)
    for st in (getattr(message, "stickers", None) or []):
        if not full():
            urls.append(f"https://cdn.discordapp.com/stickers/{st.id}.png")
    # embeds (Tenor/Giphy)
    for e in (getattr(message, "embeds", None) or []):
        im = getattr(e, "image", None)
        url = getattr(im, "url", None) if im else None
        if url and not full():
            urls.append(url)
    # custom emoji mentioned inside text
    for m in preprocess.GUILD_EMOJI_RE.finditer(message.content or ""):
        if not full():
            urls.append(f"https://cdn.discordapp.com/emojis/{m.group(3)}.png")
    return atts, urls


async def _image_part(att) -> dict:
    """
    Build an OpenAI multimodal image_url part from a discord.Attachment.

    Small images are inlined as base64 data URLs (self-contained — the LLM
    host need not reach Discord's CDN, which is often blocked on CN LANs).
    Oversized images fall back to the plain URL.
    """
    try:
        if att.size and att.size <= IMAGE_INLINE_MAX_BYTES:
            data = await att.read()
            if data and len(data) <= IMAGE_INLINE_MAX_BYTES:
                b64 = base64.b64encode(data).decode("ascii")
                ctype = att.content_type or "image/png"
                return {"type": "image_url",
                        "image_url": {"url": f"data:{ctype};base64,{b64}"}}
    except Exception as exc:
        logger.warning("Inline image failed (%s): %s — using URL", att.filename, exc)
    return {"type": "image_url", "image_url": {"url": att.url}}


async def _url_data_url(url: str) -> dict:
    """
    Fetch a plain media URL (sticker preview / Tenor embed / emoji CDN)
    and inline it as a base64 data URL; fall back to the raw URL.
    """
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url)
            ctype = resp.headers.get("content-type", "image/png").split(";")[0]
            if resp.status_code == 200 and ctype.startswith("image/") \
                    and 0 < len(resp.content) <= IMAGE_INLINE_MAX_BYTES:
                b64 = base64.b64encode(resp.content).decode("ascii")
                return {"type": "image_url",
                        "image_url": {"url": f"data:{ctype};base64,{b64}"}}
    except Exception as exc:
        logger.debug("Media url inline failed (%s): %s", url, exc)
    return {"type": "image_url", "image_url": {"url": url}}


def build_user_content(display_name: str, user_text: str, images: list,
                       vision: bool, lang: str = "zh"):
    """
    Assemble the user message content for the LLM.

    images interpretation depends on vision:
      vision=True  → images = prepared multimodal parts (from _image_part);
                     result is the OpenAI list [text, image_url...].
      vision=False → images = raw attachments; result is plain text plus a
                     note, so a text-only model knows the images exist but
                     are invisible — and must not hallucinate their content.
      no images    → plain "{name}: {text}" string (as before).
    """
    label = f"{display_name}: {user_text or '...'}"
    if not images:
        return label
    if vision:
        text_part = {"type": "text",
                     "text": f"{display_name}: {user_text or ('（有人发了图片）' if lang == 'zh' else '(someone sent an image)')}"}
        return [text_part, *images]
    # Vision-less: describe the images in text, forbid making things up.
    n = len(images)
    if lang == "zh":
        note = (f"（注：{display_name} 发来 {n} 张图片，但你没有视觉模块、看不到图内容，"
                f"绝不许编造图里有什么；需要时让对方自己描述，或按看不到图自然接话。）")
    else:
        note = (f"(Note: {display_name} sent {n} image(s), but you have no vision module "
                f"and cannot see them. NEVER invent their content — ask the sender to "
                f"describe them, or reply naturally as someone who can't see the picture.)")
    return f"{label}\n{note}"


def build_text_maps(message: discord.Message):
    """
    Resolve the readable-name maps for one message (GAP #9.1 / #9.10).

    Returns (user_names, channel_names, emoji_map, emoji_roster):
      user_names    {id: displayName} — member nickname > global name > username
      channel_names {id: name}
      emoji_map     {name: (id, animated)} — used to turn :name: back into
                    real server emoji on the way out
      emoji_roster  ", "-joined ":name:" list for the system prompt ("" if none)
    """
    guild = message.guild
    user_names: dict = {}
    channel_names: dict = {}
    emoji_map: dict = {}
    if guild is not None:
        for u in (getattr(message, "mentions", None) or []):
            if isinstance(u, discord.Member):
                user_names[str(u.id)] = u.display_name
            elif isinstance(u, discord.User):
                user_names[str(u.id)] = u.global_name or u.name
        for uid in preprocess.USER_MENTION_RE.findall(message.content or ""):
            if uid not in user_names:
                try:
                    mem = guild.get_member(int(uid))
                except (ValueError, TypeError):
                    mem = None
                if mem is not None:
                    user_names[uid] = mem.display_name
        for cid in preprocess.CHANNEL_MENTION_RE.findall(message.content or ""):
            try:
                ch = guild.get_channel(int(cid))
            except (ValueError, TypeError):
                ch = None
            if ch is not None:
                channel_names[cid] = getattr(ch, "name", cid)
        for i, e in enumerate(guild.emojis):
            if i >= EMOJI_ROSTER_SIZE:
                break
            emoji_map.setdefault(e.name, (str(e.id), bool(e.animated)))
    roster = ", ".join(f":{n}:" for n in list(emoji_map)[:EMOJI_ROSTER_SIZE])
    return user_names, channel_names, emoji_map, roster


class VrSuiteBot(discord.Client):
    def __init__(self, *args, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True  # needed to resolve nicknames for <@id> (GAP 9.1/9.4)
        kwargs["intents"] = intents
        super().__init__(*args, **kwargs)

        self.tree = discord.app_commands.CommandTree(self)

        # Per-channel: conversation history with the bot — loaded from
        # data/history.json so restarts no longer wipe the girls' memory
        # of the conversation (GAP #11).
        self.history: dict[str, deque] = load_history(HISTORY_FILE, HISTORY_MAXLEN)

        # Per-channel: unix time of the girl's last reply — powers the hot
        # conversation window (follow-ups answered without an @mention).
        self._last_reply_ts: dict[str, float] = {}

        # Sniffer failure counter — consecutive failures before sleep
        self._sniff_failures: int = 0

        # Guard: only announce a handover once per distinct change
        self._handover_announced: tuple[str, str] | None = None  # (from, to)

        # Long-term memory: per-channel message batches awaiting summary
        self._memory_pending: dict[str, list] = {}
        # Per-guild lock so two summaries never race on memory.json
        self._memory_locks: dict[str, asyncio.Lock] = {}

    # ── Lifecycle ───────────────────────────────────────────────

    async def setup_hook(self):
        setup_commands(self)
        self.model_sniffer_loop.start()
        self.presence_refresh_loop.start()
        await self.tree.sync()
        logger.info("Command tree synced.")

    async def on_ready(self):
        # Prime the sniffer once so the model ID is known before the
        # first LLM presence/chat call (SDK requires the model field).
        try:
            sniffed = sniff_character()
            update_character(sniffed)
        except Exception as exc:
            logger.warning("Startup sniff failed: %s", exc)

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
        """Update the bot's signature status to match the on-duty girl.

        When LLM_PRESENCE_ENABLED and a girl is actually awake, ask the
        LLM to invent the status line in-character; otherwise fall back
        to the static PRESENCE_BY_CHARACTER strings (ALL_SLEEP always
        uses the static copy — nobody is awake to write one).
        """
        activity = PRESENCE_BY_CHARACTER.get(character, PRESENCE_BY_CHARACTER["ALL_SLEEP"])

        if LLM_PRESENCE_ENABLED and character != "ALL_SLEEP":
            lang = "zh"  # presence is global (not per-guild); default to zh
            try:
                generated = await generate_presence(character, lang)
                if generated:
                    activity = generated
            except Exception as exc:
                logger.warning("LLM presence unavailable, using static: %s", exc)

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

    # ── Presence Auto-Refresh (签名定时刷新) ──────────────────────
    # Keeps the on-duty girl's signature time-aware: every
    # PRESENCE_REFRESH_SEC (default 2h) it regenerates a fresh status
    # line based on the current system time.

    @tasks.loop(seconds=PRESENCE_REFRESH_SEC)
    async def presence_refresh_loop(self):
        await self.wait_until_ready()
        char = get_character()
        if char == "ALL_SLEEP":
            # Nobody awake — the static sleep copy is already correct.
            return
        logger.info("⏰ Refreshing time-aware presence for %s", char)
        await self._apply_presence(char)

    @presence_refresh_loop.before_loop
    async def _before_presence_refresh(self):
        await self.wait_until_ready()

    # ── Handover Announcement (交接班公告) ───────────────────────

    async def _announce_handover(self, old_char: str, new_char: str):
        """Post an in-character handover line, sent AS the newly-awake girl."""
        if new_char == "ALL_SLEEP":
            # Everyone left — post a generic "别墅打烊" note instead.
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
        """Post a generic '别墅打烊' note when everyone falls asleep."""
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
                        "🔌 别墅的灯熄灭了，大门反锁。千问大小姐和小鲸鱼都回房睡下了。"
                        if lang == "zh" else
                        "🔌 Power cut — the villa is locked up. Miss Qwen and Little Whale are both asleep."
                    )
                    await ch.send(note)
                    logger.info("📡 Power-off announce → guild %s", gid)
                except discord.HTTPException as e:
                    logger.warning("Power-off announce fail: %s", e)

    # ── Helpers ─────────────────────────────────────────────────

    def _queue_memory(self, channel_id: str, message: dict):
        """Append one message to the channel's pending memory batch."""
        self._memory_pending.setdefault(channel_id, []).append(message)

    async def _flush_memory(self, guild_id: str, channel_id: str, batch: list, lang: str):
        """Merge a conversation batch into per-guild long-term memory.

        Uses a per-guild lock so two channels never race on memory.json;
        concurrent callers skip (their next flush will catch up).
        """
        lock = self._memory_locks.setdefault(guild_id, asyncio.Lock())
        if lock.locked():
            logger.info("Memory flush skipped (busy) guild %s ch %s", guild_id, channel_id)
            return
        async with lock:
            try:
                current = get_guild_memory(guild_id)
                merged = await summarize_memory(current, batch, lang)
                if merged:
                    save_guild_memory(guild_id, merged)
                    logger.info(
                        "🧠 Memory updated for guild %s (%d msgs)", guild_id, len(batch)
                    )
            except Exception as exc:
                logger.warning("Memory flush failed (guild %s): %s", guild_id, exc)

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

    async def _react_safe(self, message: discord.Message, react: str) -> None:
        """Attach one reaction (unicode or this-guild custom emoji), never crash."""
        try:
            em = preprocess.CUSTOM_EMOJI_RE.fullmatch(react)
            if em:
                # discord.py 2.7: Emoji() can't be built by hand; prefer the
                # real cached GuildEmote (authorization), else PartialEmoji.
                eid = int(em.group(3))
                emoji_obj = (message.guild.get_emoji(eid)
                             if message.guild else None)
                if emoji_obj is None:
                    emoji_obj = discord.PartialEmoji(
                        name=em.group(2), id=eid, animated=em.group(1) == "a")
                await message.add_reaction(emoji_obj)
            else:
                await message.add_reaction(react)
        except Exception as exc:
            logger.debug("Reaction failed on msg %s: %s", message.id, exc)

    def _owner_name(self, message: discord.Message) -> str:
        """Display name of OWNER_USER_ID (cached member > trigger author > '主人')."""
        try:
            oid = int(OWNER_USER_ID)
        except (TypeError, ValueError):
            return "主人"
        if message.guild is not None:
            mem = message.guild.get_member(oid)
            if mem is not None:
                return mem.display_name
        if message.author.id == oid:
            return message.author.display_name
        return "主人"

    async def _maybe_interject(self, message: discord.Message) -> None:
        """
        Mini-ChannelMind (自由插话): overhear un-summoned chatter — pull the
        channel's recent human messages, ask the on-duty girl if she wants
        to butt in. SHE decides: an empty reply means she kept quiet, and
        she can still drop one reaction. Probability + cooldown keep it
        at a believable human frequency.
        """
        if not INTERJECT_ENABLED or get_character() == "ALL_SLEEP":
            return
        if len((message.content or "").strip()) < 3:
            return
        if random.random() > INTERJECT_CHANCE:
            return
        channel_id = str(message.channel.id)
        if time.time() - self._last_reply_ts.get(channel_id, 0) < INTERJECT_COOLDOWN_SEC:
            return

        guild_id = str(message.guild.id) if message.guild else str(message.author.id)
        lang = get_server_language(guild_id)
        character = get_character()

        # ── overhear the room (not just her own conversations) ──
        try:
            msgs = [m async for m in message.channel.history(limit=15)
                    if not m.author.bot]
        except discord.HTTPException as exc:
            logger.debug("Interject history failed: %s", exc)
            return
        msgs.reverse()
        if len(msgs) < 2:
            return

        names: dict[str, str] = {}
        for m in msgs:
            a = m.author
            names[str(a.id)] = (a.display_name if isinstance(a, discord.Member)
                                 else (a.global_name or a.name))
        if message.guild is not None:
            for m in msgs:
                for uid in preprocess.USER_MENTION_RE.findall(m.content or ""):
                    if uid not in names:
                        try:
                            mem = message.guild.get_member(int(uid))
                        except (ValueError, TypeError):
                            mem = None
                        if mem is not None:
                            names[uid] = mem.display_name
        lines = [
            f"{names.get(str(m.author.id), '?')}: "
            f"{preprocess.prettify_text(m.content or '', names, {})[:120]}"
            for m in msgs
        ]
        transcript = "\n".join(lines[-12:])

        # ── ask her, in persona, whether she wants to speak ──
        extra = build_interject_note(lang)
        if OWNER_USER_ID:
            extra += build_owner_note(lang, self._owner_name(message), character)
        if MEMORY_ENABLED:
            extra += build_memory_note(lang, get_guild_memory(guild_id))
        system_prompt = build_system_prompt(character, lang, extra_notes=extra)

        if lang == "zh":
            user_block = f"【频道闲聊·最近】\n{transcript}\n（要说点什么吗？不想说就留空）"
        else:
            user_block = (f"[Overheard chatter]\n{transcript}\n"
                          "(Want to say something? Leave it empty if not.)")

        llm_resp = await generate_reply(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_block}])
        if not llm_resp:
            return

        # Reaction-only participation — sometimes a real person just slaps
        # a 😂 on a message without saying anything.
        if REACTIONS_ENABLED:
            react = preprocess.clean_reaction(llm_resp.get("reaction"))
            if react:
                await self._react_safe(message, react)

        reply_text = (llm_resp.get("reply") or "").strip()
        if not reply_text:
            return  # she chose to stay quiet — a very human decision

        man = stickers.load_manifest()
        reply_text, sticker_tags = preprocess.extract_stickers(
            reply_text, stickers.known_tags(man))
        if sticker_tags:
            stickers.bump_uses(sticker_tags)

        _, _, emoji_map, _ = build_text_maps(message)
        for bubble in self._split_bubbles(reply_text):
            await send_as(message.channel, character,
                          preprocess.apply_guild_emojis(bubble, emoji_map))
        for tag in sticker_tags[:1]:
            p = stickers.path_for(tag, man)
            if p:
                await send_as(message.channel, character, "",
                              files=[discord.File(p)])

        self._last_reply_ts[channel_id] = time.time()
        if channel_id not in self.history:
            self.history[channel_id] = deque(maxlen=HISTORY_MAXLEN)
        self.history[channel_id].append(
            {"role": "assistant", "content": reply_text})
        save_history(HISTORY_FILE, self.history)
        if MEMORY_ENABLED:
            self._queue_memory(channel_id,
                               {"role": "assistant", "content": reply_text})
        logger.info("💭 %s 自由插话 #%s: %s", character, channel_id,
                    reply_text[:60])

    @staticmethod
    def _split_bubbles(text: str, max_len: int = 40, max_bubbles: int = 3) -> list[str]:
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
        # The Master speaks → always worth an answer, @ or not.
        is_owner = bool(OWNER_USER_ID) and str(message.author.id) == OWNER_USER_ID
        # Hot conversation window: she just spoke here → answer follow-ups
        # ("在吗" / "怎么啦") without an @, like a real member would.
        is_hot = (FOLLOWUP_WINDOW_SEC > 0
                  and time.time() - self._last_reply_ts.get(channel_id, 0)
                  < FOLLOWUP_WINDOW_SEC)

        if not (is_mention or is_reply_to_bot or is_name_call or is_owner or is_hot):
            # For v1 we only reply to direct summons. A free-speaking /
            # attention model (like Amadeus' ChannelMind) can be grafted on
            # later without touching this path.
            # ── mini-ChannelMind: maybe she butts in on her own ──
            await self._maybe_interject(message)
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
            user_text = re.sub(rf"<@!?{self.user.id}>", "", content or "").strip()

            # ── Preprocess: forwarded snapshots + <@id>/emoji → readable
            #    text, so the model never rambles raw IDs (GAP 9.1/9.7) ──
            snap_text = preprocess.format_snapshots(
                getattr(message, "message_snapshots", None))
            if snap_text:
                user_text = f"{user_text}\n{snap_text}" if user_text else snap_text
            user_names, channel_names, emoji_map, emoji_roster = build_text_maps(message)
            user_text = preprocess.prettify_text(user_text, user_names, channel_names)

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

            # Long-term memory: inject what the girls remember about residents.
            if MEMORY_ENABLED:
                memory_text = get_guild_memory(guild_id)
                extra += build_memory_note(lang, memory_text)

            # 主人 recognition: the one who bought the suite is special.
            if OWNER_USER_ID:
                extra += build_owner_note(
                    lang, self._owner_name(message), character)

            # Server emoji roster (she learns which :name: she can use).
            if emoji_roster:
                extra += (
                    ("\n[可用服务器表情] 回复里写 :名字: 会自动变成本服务器表情：" + emoji_roster)
                    if lang == "zh" else
                    ("\n[Server emoji] write :name: in replies, it renders as the "
                     "server's emoji: " + emoji_roster)
                )
            # Sticker library roster (she may attach memes via [sticker:tag]).
            sticker_man = stickers.load_manifest()
            sticker_list = stickers.roster(sticker_man)
            if sticker_list:
                extra += (
                    ("\n[表情包库存] 回复里写 [sticker:tag]（最多一个）会附上对应梗图："
                     + sticker_list)
                    if lang == "zh" else
                    ("\n[Meme library] write [sticker:tag] (max one) in your reply "
                     "to attach that meme: " + sticker_list)
                )

            system_prompt = build_system_prompt(character, lang, extra_notes=extra)

            # ── Vision routing: only girls whose host model has vision
            #    actually receive the pixels; others get a text notice.
            #    Media comes from attachments + snapshots + stickers +
            #    embeds + custom emoji in text (GAP 9.9).
            vision = VISION_CAPABILITY.get(character, False)
            media_atts, media_urls = collect_media(message)
            n_media = len(media_atts) + len(media_urls)
            if n_media and vision:
                image_input = (
                    [await _image_part(a) for a in media_atts]
                    + [await _url_data_url(u) for u in media_urls]
                )
            else:
                image_input = media_atts + list(media_urls)
            user_content = build_user_content(
                message.author.display_name, user_text, image_input, vision, lang
            )

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(self._history_as_messages(channel_id, message.author.display_name))
            messages.append({"role": "user", "content": user_content})

            llm_resp = await generate_reply(messages)
            if llm_resp is None:
                await message.channel.send(LOADING_REPLY)
                return

            reply_text = llm_resp.get("reply", "……")

            # Image memo (desc): record WHAT the picture was in the text
            # history — the pixels only exist for this one turn, otherwise
            # she honestly forgets her own images ("我哪记得你发过啥").
            memo = (llm_resp.get("desc") or "").strip()
            hist_text = (f"{user_text} [图片: {memo}]"
                         if (memo and n_media) else user_text)

            # ── Sticker placeholders: strip [sticker:tag], keep valid ones
            #    (unknown tags are dropped so she can't invent files) ──
            reply_text, sticker_tags = preprocess.extract_stickers(
                reply_text, stickers.known_tags(sticker_man))
            if sticker_tags:
                stickers.bump_uses(sticker_tags)

            # ── Update history ──────────────────────────────────
            self.history[channel_id].append({
                "role": "user",
                "content": hist_text,
                "name": message.author.display_name,
            })
            self.history[channel_id].append({
                "role": "assistant", "content": reply_text,
            })
            save_history(HISTORY_FILE, self.history)

            # ── Memory: batch messages, flush every N user turns ─
            if MEMORY_ENABLED:
                self._queue_memory(
                    channel_id,
                    {"role": "user", "content": hist_text,
                     "name": message.author.display_name},
                )
                self._queue_memory(channel_id, {"role": "assistant", "content": reply_text})
                user_turns = sum(1 for m in self._memory_pending[channel_id] if m["role"] == "user")
                if user_turns >= MEMORY_EVERY_N_MESSAGES:
                    batch = self._memory_pending.pop(channel_id)
                    asyncio.create_task(
                        self._flush_memory(guild_id, channel_id, batch, lang)
                    )

            # ── Reply through webhook (身份拟态) ─────────────────
            #    :name: → real server emoji; first bubble silently quotes
            #    the trigger message (no ping, GAP 9.2).
            bubbles = self._split_bubbles(reply_text)
            for i, bubble in enumerate(bubbles):
                bubble = preprocess.apply_guild_emojis(bubble, emoji_map)
                await send_as(message.channel, character, bubble,
                              reference=(message if i == 0 else None))
                if i != len(bubbles) - 1:
                    await asyncio.sleep(random.uniform(0.8, 1.2))
            if bubbles:
                # conversation just went hot — follow-ups need no @
                self._last_reply_ts[channel_id] = time.time()

            # Meme attachments go out as their own bubbles (QQ-style:
            # one sticker per bubble, text never rides inside the image).
            for tag in sticker_tags[:1]:
                path = stickers.path_for(tag, sticker_man)
                if path:
                    await send_as(message.channel, character, "",
                                  files=[discord.File(path)])

            # ── Reaction = the Discord replacement for QQ's poke ────
            if REACTIONS_ENABLED:
                react = preprocess.clean_reaction(llm_resp.get("reaction"))
                if react:
                    await self._react_safe(message, react)

            # ── Auto-collect memes she actually looked at (GAP 9.11) ──
            #    her own desc memo becomes the library caption.
            if STICKER_AUTO_COLLECT and vision and media_atts:
                att = media_atts[0]
                try:
                    if att.size and att.size <= 4 * 1024 * 1024:
                        raw = await att.read()
                        stickers.collect(raw, att.content_type or "",
                                         (content or att.filename or "")[:40],
                                         desc=memo)
                except Exception as exc:
                    logger.debug("Sticker collect failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    run_startup_check()
    bot = VrSuiteBot()
    bot.run(DISCORD_TOKEN)
