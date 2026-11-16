"""
Slash command definitions for the VRAM Suite (显卡单间).

Commands:
  /whoishome  — who is currently awake in the GPU suite (Rich Embed)
  /inspect    — alias for /whoishome (视察合租别墅)
  /help       — command reference
"""

import random
import discord
from discord import app_commands

from config import logger
from state import get_character, get_change_history


# Character → immersive display copy (placeholders — fill later)
_CHAR_DISPLAY = {
    "QWEN": {
        "name": "<占位：如「千问大小姐」>",
        "status": "<占位：如「在花园露台享用下午茶」>",
        "unseen": "<占位：如「大肥鱼」>",
        "unseen_status": "<占位：如「在别墅后院泳池里泡澡中，暂不接客」>",
    },
    "DEEPSEEK": {
        "name": "<占位：如「大肥鱼」>",
        "status": "<占位：如「在别墅后院泳池里吐泡泡」>",
        "unseen": "<占位：如「千问大小姐」>",
        "unseen_status": "<占位：如「正在喝下午茶，暂不接客」>",
    },
    "ALL_SLEEP": {
        "name": "全员休眠",
        "status": "<占位：如「别墅打烊，少女们都回房睡了」>",
        "unseen": "—",
        "unseen_status": "—",
    },
}

# VRAM usage display (placeholder — wire to real metrics later)
_VRAM_USAGE = "<占位：如 92%>"
_VRAM_DESC = "<占位：如「已被千问大小姐的长裙占满」>"


def _build_whoishome_embed() -> discord.Embed:
    """Build the Rich Embed for /whoishome."""
    char = get_character()
    disp = _CHAR_DISPLAY.get(char, _CHAR_DISPLAY["ALL_SLEEP"])

    embed = discord.Embed(
        title="🏠 合租别墅 · 视察报告",
        description="谁在别墅里醒着，谁就出来见客。",
        color=discord.Color.purple(),
    )
    embed.add_field(
        name="当值少女",
        value=f"**{disp['name']}**\n状态：{disp['status']}",
        inline=False,
    )
    embed.add_field(
        name="显卡占用状态",
        value=f"VRAM **{_VRAM_USAGE}**（{_VRAM_DESC}）",
        inline=False,
    )
    embed.add_field(
        name="未在线少女动向",
        value=f"{disp['unseen']}（{disp['unseen_status']}）",
        inline=False,
    )
    embed.set_footer(text="别墅只有一张床，谁醒着谁当值。")
    return embed


def setup_commands(bot) -> None:
    """Register all slash commands on the bot's CommandTree."""

    # ═════════════════════════════════════════════════════════════
    # /whoishome
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="whoishome",
        description="谁在别墅里？（查看当前当值少女）",
    )
    async def slash_whoishome(interaction: discord.Interaction):
        embed = _build_whoishome_embed()
        await interaction.response.send_message(embed=embed)
        logger.info("/whoishome by %s → %s", interaction.user.name, get_character())

    # ═════════════════════════════════════════════════════════════
    # /inspect (alias)
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="inspect",
        description="视察合租别墅（同 /whoishome）",
    )
    async def slash_inspect(interaction: discord.Interaction):
        embed = _build_whoishome_embed()
        await interaction.response.send_message(embed=embed)

    # ═════════════════════════════════════════════════════════════
    # /help
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="help",
        description="查看指令列表",
    )
    async def slash_help(interaction: discord.Interaction):
        embed = discord.Embed(
            title="🏠 显卡单间 · 指令列表",
            color=discord.Color.purple(),
        )
        embed.add_field(name="💬 聊天", value="直接 `@我` 或回复我的消息即可聊天。", inline=False)
        embed.add_field(name="🏠 `/whoishome`", value="查看当前谁在别墅里当值。", inline=False)
        embed.add_field(name="🔍 `/inspect`", value="视察合租别墅（同 /whoishome）。", inline=False)
        await interaction.response.send_message(embed=embed)

    logger.info("Slash commands registered: whoishome, inspect, help")
