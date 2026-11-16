"""
Preprocessing / postprocessing layer (QQ→Discord 迁移 · GAP #9).

Pure text transforms, no Discord objects — everything here is unit-testable:

  prettify_text          : <@id> / #channel / <:emoji:id> → readable text (9.1)
  build_guild_maps       : (helper) id→name maps from plain dicts
  apply_guild_emojis     : :name: → <:name:id> on the way OUT (9.10)
  clean_reaction         : validate the optional reaction field (9.3)
  extract_stickers       : pull [sticker:tag] placeholders out of a reply (9.11)
  format_snapshots       : forwarded-message snapshots → readable text block (9.7)
  STICKER_RE / EMOJI_RE  : shared patterns
"""

import re

# <@123> / <@!123>
USER_MENTION_RE = re.compile(r"<@!?(\d+)>")
# <#123>
CHANNEL_MENTION_RE = re.compile(r"<#!?(\d+)>")
# <:name:123> / <a:name:123>  (custom emoji inside message text)
GUILD_EMOJI_RE = re.compile(r"<(a?):([\w~_]{2,32}):(\d+)>")
# :name: (the OUTBOUND shorthand the model is taught to write)
SHORTCODE_RE = re.compile(r":([\w~_]{2,32}):")
# [sticker:tag]
STICKER_RE = re.compile(r"\[sticker:([\w~_-]{1,40})\]")
# <a?:name:id> full custom emoji (for reaction validation)
CUSTOM_EMOJI_RE = re.compile(r"<(a?):([\w~_]{2,32}):(\d+)>")
# unicode emoji pictographs / symbols / flags / modifiers only —
# short Chinese sentences must NOT pass as "an emoji"
UNICODE_EMOJI_RE = re.compile(
    r"[\U0001F000-\U0001FAFF\U0001FB00-\U0001FBFF"
    r"\U0001F1E6-\U0001F1FF\u2600-\u27BF\u2B00-\u2BFF"
    r"\uFE0F\u200D\u20E3\U0001F900-\U0001F9FF]+"
)


def prettify_text(text: str, user_names: dict, channel_names: dict) -> str:
    """
    Replace raw Discord mention/emoji markup with readable text BEFORE the
    text is fed to the LLM.

    user_names / channel_names : {str_id: display_name}. Unknown ids degrade
    to @昵称未知(id尾4位) so the model never rambles raw digits.

    Custom emoji <:name:id> become :name: — the model can echo them back and
    apply_guild_emojis() turns them back into real emoji on the way out.
    """
    if not text:
        return text

    def _user(m):
        uid = m.group(1)
        return "@" + user_names.get(uid, f"用户{uid[-4:]}")

    def _chan(m):
        cid = m.group(1)
        return "#" + channel_names.get(cid, f"频道{cid[-4:]}")

    def _emoji(m):
        return f":{m.group(2)}:"

    text = USER_MENTION_RE.sub(_user, text)
    text = CHANNEL_MENTION_RE.sub(_chan, text)
    text = GUILD_EMOJI_RE.sub(_emoji, text)
    return text


def apply_guild_emojis(text: str, emoji_map: dict) -> str:
    """
    Replace :name: shortcodes with real guild emoji markup <:name:id>
    (or <a:name:id> when animated). emoji_map: {name: (id, animated)}.

    Webhook text can carry any custom emoji from a server the bot is in —
    no Nitro needed.
    """
    if not text or not emoji_map:
        return text

    def _sub(m):
        name = m.group(1)
        hit = emoji_map.get(name)
        if not hit:
            return m.group(0)
        eid, animated = hit
        return f"<{'a' if animated else ''}:{name}:{eid}>"

    return SHORTCODE_RE.sub(_sub, text)


def clean_reaction(value) -> str | None:
    """
    Validate the optional LLM-provided reaction (the DC equivalent of QQ's
    poke): a single unicode emoji (≤8 chars) or one custom emoji token.
    Returns a clean token or None.
    """
    if not value or not isinstance(value, str):
        return None
    r = value.strip().strip('"')
    if not r:
        return None
    m = CUSTOM_EMOJI_RE.fullmatch(r)
    if m:
        return r  # <:name:id> / <a:name:id> as-is
    if not r.startswith("<") and UNICODE_EMOJI_RE.fullmatch(r):
        return r  # plain unicode emoji (pictographs/symbols/flags only)
    return None


def extract_stickers(text: str, known_tags: set) -> tuple[str, list]:
    """
    Pull [sticker:tag] placeholders out of a reply.

    Returns (clean_text, [tag, ...]) keeping only tags present in
    known_tags; unknown tags are dropped so the model can't invent files.
    """
    if not text:
        return text, []
    found = []

    def _sub(m):
        tag = m.group(1)
        if tag in known_tags:
            found.append(tag)
        return ""

    clean = STICKER_RE.sub(_sub, text).strip()
    return clean, found


def format_snapshots(snapshots) -> str:
    """
    Render forwarded-message snapshots (Discord Message Forwarding,
    discord.py ≥2.4) into a text block the girls can riff on (9.7).

    Each snapshot may carry .author (SnapshotAuthor: username/global_name/
    display_name properties) and .content. Absent content degrades to a
    bare notice.
    """
    if not snapshots:
        return ""
    lines = []
    for snap in snapshots:
        content = (getattr(snap, "content", "") or "").strip()
        author = getattr(snap, "author", None)
        name = (getattr(author, "display_name", None)
                or getattr(author, "global_name", None)
                or getattr(author, "username", None) or "某人") if author else "某人"
        if content:
            lines.append(f"[转发的消息·来自 {name}] {content}")
        else:
            lines.append(f"[转发的消息·来自 {name}（无文字内容，可能只有图片）]")
    return "\n".join(lines)
