"""
Smoke tests for the VRAM Suite (显卡单间) framework.

Verifies the core framework logic WITHOUT needing a live Discord bot
or a running LLM backend:
  - character keyword mapping (qwen / deepseek / unmapped / unreachable)
  - state machine handover transitions
  - prompt assembly for each character
  - immersive fallback copy wiring

Run:  python tests/smoke.py   (from the repo root, with .venv)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class CharacterMappingTest(unittest.TestCase):
    """sniffer: model id → character key mapping."""

    def test_qwen_maps_to_qwen(self):
        from sniffer import CHARACTER_KEYWORDS
        lowered = "qwen2.5-14b"
        mapped = next(
            (c for kw, c in CHARACTER_KEYWORDS if kw in lowered.lower()),
            "ALL_SLEEP",
        )
        self.assertEqual(mapped, "QWEN")

    def test_deepseek_maps_to_deepseek(self):
        from sniffer import CHARACTER_KEYWORDS
        lowered = "deepseek-v3"
        mapped = next(
            (c for kw, c in CHARACTER_KEYWORDS if kw in lowered.lower()),
            "ALL_SLEEP",
        )
        self.assertEqual(mapped, "DEEPSEEK")

    def test_unmapped_goes_to_sleep(self):
        from sniffer import CHARACTER_KEYWORDS
        lowered = "llama-3.1-8b"
        mapped = next(
            (c for kw, c in CHARACTER_KEYWORDS if kw in lowered.lower()),
            "ALL_SLEEP",
        )
        self.assertEqual(mapped, "ALL_SLEEP")


class StateMachineTest(unittest.TestCase):
    """state: handover transitions + persistence."""

    def setUp(self):
        # Point the state file to a throwaway path
        import state
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self._tmp.close()
        self._orig = state.STATE_FILE
        state.STATE_FILE = self._tmp.name

    def tearDown(self):
        import state
        state.STATE_FILE = self._orig
        os.unlink(self._tmp.name)

    def test_initial_state_is_all_sleep(self):
        import state
        self.assertEqual(state.get_character(), "ALL_SLEEP")

    def test_handover_returns_changed(self):
        import state
        self.assertTrue(state.update_character("QWEN"))
        self.assertFalse(state.update_character("QWEN"))  # no change
        self.assertTrue(state.update_character("DEEPSEEK"))

    def test_history_recorded(self):
        import state
        state.update_character("QWEN")
        state.update_character("DEEPSEEK")
        history = state.get_change_history()
        self.assertEqual(history[0]["to"], "DEEPSEEK")
        self.assertEqual(history[0]["from"], "QWEN")


class PromptAssemblyTest(unittest.TestCase):
    """prompts: character prompt assembly."""

    def test_qwen_prompt_has_status_note(self):
        from prompts import build_system_prompt
        p = build_system_prompt("QWEN", "zh")
        self.assertIn("千问", p)
        self.assertIn("System Status", p)

    def test_deepseek_prompt_has_status_note(self):
        from prompts import build_system_prompt
        p = build_system_prompt("DEEPSEEK", "zh")
        self.assertIn("小鲸鱼", p)
        self.assertIn("System Status", p)

    def test_qwen_new_persona(self):
        # 千问大小姐群友卡核心：折扇大小姐、形象雷点、对小鱼关系、JSON 格式
        from config import SYSTEM_PROMPTS
        card = SYSTEM_PROMPTS["QWEN"]["zh"]
        self.assertTrue(card.strip(), "qwen 角色卡为空")
        self.assertIn("折扇", card)
        self.assertIn("食堂阿姨", card)        # 形象雷点必须保留
        self.assertIn("小鲸鱼", card)          # 与新室友卡联动
        self.assertIn('"emotion"', card)
        self.assertIn('"reply"', card)

    def test_deepseek_new_persona(self):
        # 小鲸鱼群友卡核心：自称小鲸鱼、大肥鱼是雷点、JSON 输出格式在
        from config import SYSTEM_PROMPTS
        card = SYSTEM_PROMPTS["DEEPSEEK"]["zh"]
        self.assertTrue(card.strip(), "deepseek 角色卡为空")
        self.assertIn("小鲸鱼", card)
        self.assertIn("大肥鱼", card)          # 雷点仍在（作为蔑称）
        self.assertIn('"emotion"', card)       # JSON 输出格式必须保留
        self.assertIn('"reply"', card)

    def test_handover_note(self):
        from prompts import build_handover_note
        # DEEPSEEK left, QWEN is on duty → the note names the leaving girl
        p = build_handover_note("zh", "DEEPSEEK", "QWEN")
        self.assertIn("小鲸鱼", p)
        self.assertIn("值班", p)

    def test_loading_note(self):
        from prompts import build_loading_note
        p = build_loading_note("zh")
        self.assertIn("别墅", p)


class LoadingVsSleepTest(unittest.TestCase):
    """config + main sniffer: consecutive failures before declaring sleep."""

    def test_failure_threshold_config(self):
        import config
        # Default: a single failure must NOT immediately count as sleep
        self.assertGreaterEqual(config.SNIFFER_FAILURES_TO_SLEEP, 2)

    def test_sniffer_config_present(self):
        import config
        self.assertGreater(config.SNIFFER_INTERVAL_SEC, 0)
        self.assertGreater(config.SNIFFER_TIMEOUT_SEC, 0)


class PresenceGenerationTest(unittest.TestCase):
    """config + llm_api: LLM-generated Discord signature status."""

    def test_presence_config_present(self):
        import config
        # LLM presence is on by default; static strings remain as fallback
        self.assertTrue(config.LLM_PRESENCE_ENABLED)
        self.assertGreater(config.PRESENCE_MAX_TOKENS, 0)
        self.assertIn("QWEN", config.PRESENCE_BY_CHARACTER)
        self.assertIn("DEEPSEEK", config.PRESENCE_BY_CHARACTER)
        self.assertIn("ALL_SLEEP", config.PRESENCE_BY_CHARACTER)

    def test_presence_refresh_config(self):
        import config
        # Default refresh interval is 2h = 7200s
        self.assertEqual(config.PRESENCE_REFRESH_SEC, 7200)
        self.assertGreater(config.PRESENCE_REFRESH_SEC, 0)

    def test_generate_presence_time_injected(self):
        import asyncio
        import datetime
        from unittest import mock
        from llm_api import _chat_once, generate_presence

        captured = {}

        async def fake_chat_once(messages, max_tokens, sampling, extra, model):
            # The time must appear in the user instruction
            user_content = next(m["content"] for m in messages if m["role"] == "user")
            captured["has_time"] = "14:30" in user_content
            return "正在花园喝下午茶"

        with mock.patch.object(__import__("llm_api"), "_chat_once", fake_chat_once):
            fixed = datetime.datetime(2026, 11, 15, 14, 30)
            result = asyncio.run(generate_presence("QWEN", now=fixed))
        self.assertEqual(result, "正在花园喝下午茶")
        self.assertTrue(captured.get("has_time"), "time was not injected into the prompt")

    def test_generate_presence_no_role_card(self):
        import asyncio
        from llm_api import generate_presence
        # Unknown character → returns None (no role card), never raises
        result = asyncio.run(generate_presence("UNKNOWN"))
        self.assertIsNone(result)

    def test_presence_fallback_copy_villa(self):
        import config
        # Fallback strings should reflect the villa setting, not a PC case
        self.assertIn("别墅", config.PRESENCE_BY_CHARACTER["ALL_SLEEP"])


class WebhookModuleTest(unittest.TestCase):
    """webhook: module imports and cache helpers."""

    def test_webhook_cache_helpers(self):
        import webhook
        # load should never raise, even with no cache file
        data = webhook._load_webhooks()
        self.assertIsInstance(data, dict)

    def test_webhook_identity_config(self):
        import config
        self.assertIn("QWEN", config.WEBHOOK_IDENTITY)
        self.assertIn("DEEPSEEK", config.WEBHOOK_IDENTITY)

    def test_avatar_local_path_loaded(self):
        # Bundled placeholder icons must load as real PNG bytes
        import asyncio
        import os
        import config
        import webhook
        rel = config.WEBHOOK_IDENTITY["QWEN"]["avatar_url"]
        data = asyncio.run(webhook._fetch_avatar_bytes(rel))
        self.assertIsNotNone(data, f"avatar {rel} failed to load")
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")  # PNG magic
        self.assertLess(len(data), 1024 * 1024)

    def test_avatar_missing_path_returns_none(self):
        import asyncio
        import webhook
        self.assertIsNone(asyncio.run(webhook._fetch_avatar_bytes("resource/nope.png")))
        self.assertIsNone(asyncio.run(webhook._fetch_avatar_bytes("")))


class ModelFieldTest(unittest.TestCase):
    """openai SDK 强制要求 model 字段：由嗅探器发现并回填。"""

    def test_sniffer_caches_model_id(self):
        from unittest import mock
        import sniffer

        class FakeResp:
            status_code = 200
            def json(self):
                return {"data": [{"id": "qwen-fake-7b"}]}

        old = sniffer.get_sniffed_model()
        try:
            with mock.patch.object(sniffer.httpx, "get", lambda *a, **k: FakeResp()):
                ch = sniffer.sniff_character()
            self.assertEqual(ch, "QWEN")
            self.assertEqual(sniffer.get_sniffed_model(), "qwen-fake-7b")
        finally:
            sniffer.set_sniffed_model(old)

    def test_chat_once_always_sends_model(self):
        import asyncio
        from types import SimpleNamespace
        from unittest import mock
        import llm_api
        import sniffer

        captured = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            msg = SimpleNamespace(content="ok")
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

        sniffer.set_sniffed_model("qwen-fake-7b")
        try:
            with mock.patch.object(
                llm_api._llm_client.chat.completions, "create", fake_create
            ):
                out = asyncio.run(
                    llm_api._chat_once([{"role": "user", "content": "x"}], 10, {"temperature": 0.5})
                )
        finally:
            sniffer.set_sniffed_model(None)
        self.assertEqual(out, "ok")
        self.assertEqual(captured.get("model"), "qwen-fake-7b")

    def test_chat_once_falls_back_to_default(self):
        import asyncio
        from types import SimpleNamespace
        from unittest import mock
        import llm_api
        import sniffer

        captured = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            msg = SimpleNamespace(content="ok")
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

        sniffer.set_sniffed_model(None)
        with mock.patch.object(llm_api._llm_client.chat.completions, "create", fake_create):
            asyncio.run(llm_api._chat_once([{"role": "user", "content": "x"}], 10, {"temperature": 0.5}))
        self.assertEqual(captured.get("model"), "default")


class VisionRoutingTest(unittest.TestCase):
    """视觉能力路由：有图时 qwen 收多模态、deepseek 收文字提示。"""

    def test_vision_capability_defaults(self):
        import config
        self.assertTrue(config.VISION_CAPABILITY["QWEN"])
        self.assertFalse(config.VISION_CAPABILITY["DEEPSEEK"])

    def test_no_images_plain_text(self):
        from main import build_user_content
        c = build_user_content("小明", "你好", [], True)
        self.assertEqual(c, "小明: 你好")

    def test_vision_on_returns_multimodal(self):
        from main import build_user_content
        parts = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}]
        c = build_user_content("小明", "看图", parts, True)
        self.assertIsInstance(c, list)
        self.assertEqual(c[0]["type"], "text")
        self.assertEqual(c[1]["type"], "image_url")

    def test_vision_off_note_forbids_hallucination(self):
        from main import build_user_content

        class FakeAtt:
            pass

        c = build_user_content("小明", "", [FakeAtt(), FakeAtt()], False, "zh")
        self.assertIsInstance(c, str)
        self.assertIn("2 张图片", c)
        self.assertIn("绝不许编造", c)
        c_en = build_user_content("Bob", "look", [FakeAtt()], False, "en")
        self.assertIn("NEVER invent", c_en)

    def test_cards_mention_vision(self):
        from config import SYSTEM_PROMPTS
        self.assertIn("视觉模块", SYSTEM_PROMPTS["DEEPSEEK"]["zh"])
        self.assertIn("视觉模块", SYSTEM_PROMPTS["QWEN"]["zh"])
        self.assertIn("vision", SYSTEM_PROMPTS["DEEPSEEK"]["en"])
        self.assertIn("vision", SYSTEM_PROMPTS["QWEN"]["en"])


class PreprocessTest(unittest.TestCase):
    """GAP #9 文本预处理/后处理层。"""

    def test_prettify_mentions_and_emoji(self):
        import preprocess
        t = preprocess.prettify_text(
            "<@123> 和 <@!456> 在 <#789> 比 <:sadcat:999> <a:party:888>",
            {"123": "小明"}, {"789": "水群"})
        self.assertIn("@小明", t)
        self.assertIn("@用户456", t)      # unknown id degrades readably
        self.assertIn("#水群", t)
        self.assertIn(":sadcat:", t)
        self.assertNotIn("<:", t)         # raw emoji markup all converted

    def test_apply_guild_emojis_roundtrip(self):
        import preprocess
        out = preprocess.apply_guild_emojis(
            "行 :sadcat: :ghost: :nope:",
            {"sadcat": ("999", False), "ghost": ("777", True)})
        self.assertIn("<:sadcat:999>", out)
        self.assertIn("<a:ghost:777>", out)
        self.assertIn(":nope:", out)      # unknown shortcode untouched

    def test_clean_reaction(self):
        import preprocess
        self.assertEqual(preprocess.clean_reaction(" 👍 "), "👍")
        self.assertEqual(preprocess.clean_reaction("<:xx:123>"), "<:xx:123>")
        self.assertIsNone(preprocess.clean_reaction("行吧你赢了"))
        self.assertIsNone(preprocess.clean_reaction(""))
        self.assertIsNone(preprocess.clean_reaction(None))

    def test_extract_stickers_filters_unknown(self):
        import preprocess
        text, tags = preprocess.extract_stickers(
            "哈 [sticker:st1] [sticker:zz]", {"st1"})
        self.assertEqual(tags, ["st1"])
        self.assertNotIn("[sticker", text)
        self.assertTrue(text.startswith("哈"))

    def test_format_snapshots(self):
        from types import SimpleNamespace
        import preprocess
        s = SimpleNamespace(content="原话", author=SimpleNamespace(display_name="阿强"))
        out = preprocess.format_snapshots([s])
        self.assertIn("[转发的消息·来自 阿强] 原话", out)
        self.assertEqual(preprocess.format_snapshots([]), "")

    def test_generate_reply_validates_reaction(self):
        import asyncio
        import llm_api

        async def fake_raw(*a, **k):
            return '{"emotion":"other","reply":"6","reaction":"太长不合法了啦"}'

        orig, llm_api._chat_once = llm_api._chat_once, fake_raw
        try:
            r = asyncio.run(llm_api.generate_reply([{"role": "user", "content": "x"}]))
        finally:
            llm_api._chat_once = orig
        self.assertEqual(r.get("reply"), "6")
        self.assertNotIn("reaction", r)   # invalid value dropped, never sent


class StickerLibTest(unittest.TestCase):
    """GAP #9.11 表情包库：偷图入库 → 名录 → 取路径。"""

    def test_collect_roster_path_roundtrip(self):
        import stickers
        data = b"\x89PNG-tiny-" + os.urandom(48)
        tag = stickers.collect(data, "image/png", "测试图")
        if tag is None:
            self.skipTest("sticker library full")
        try:
            self.assertIn(tag, stickers.known_tags())
            self.assertIsNotNone(stickers.path_for(tag))
            self.assertIn(f"[sticker:{tag}]", stickers.roster())
            # same bytes = already owns → no duplicate
            self.assertIsNone(stickers.collect(data, "image/png", ""))
            # unsupported type rejected
            self.assertIsNone(stickers.collect(b"plain text", "text/plain", ""))
        finally:
            path = stickers.path_for(tag)
            if path:
                os.remove(path)
            man = stickers.load_manifest()
            man.pop(tag, None)
            stickers._save(man)


class WebhookSendTest(unittest.TestCase):
    """回归：Webhook.send 不支持 reference（discord.py 2.7.1），webhook 路径必须丢弃它。"""

    def test_webhook_path_drops_reference(self):
        import asyncio
        from types import SimpleNamespace
        import webhook

        captured = {}

        async def fake_send(**kw):
            captured.update(kw)
            return SimpleNamespace(id=1)

        async def fake_get(channel, character):
            return SimpleNamespace(send=fake_send)

        orig, webhook._get_or_create_webhook = \
            webhook._get_or_create_webhook, fake_get
        try:
            asyncio.run(webhook.send_as(
                SimpleNamespace(), "QWEN", "hi",
                reference=SimpleNamespace(id=99),
            ))
        finally:
            webhook._get_or_create_webhook = orig
        self.assertEqual(captured.get("content"), "hi")
        self.assertNotIn("reference", captured)
        am = captured.get("allowed_mentions")
        self.assertIsNotNone(am)
        self.assertFalse(am.replied_user)


class WebhookReuseTest(unittest.TestCase):
    """回归：2.7 API 是 TextChannel.webhooks()，不存在 Guild.fetch_webhook(id)。"""

    def _fake_channel(self, webhooks, created):
        from types import SimpleNamespace

        async def whs():
            return webhooks

        async def create(**kw):
            created.append(kw)
            return SimpleNamespace(id=555, name=kw.get("name", "?"))

        return SimpleNamespace(id=7, name="general", guild=SimpleNamespace(id=99),
                               webhooks=whs, create_webhook=create)

    def _run(self, cached_id, webhooks):
        import asyncio
        import tempfile
        import webhook
        tmp = tempfile.mktemp(suffix=".json")
        orig_file = webhook.WEBHOOKS_FILE
        webhook.WEBHOOKS_FILE = tmp
        if cached_id:
            webhook._save_webhooks({"99": {"QWEN": cached_id}})
        created = []
        try:
            wh = asyncio.run(webhook._get_or_create_webhook(
                self._fake_channel(webhooks, created), "QWEN"))
            cache = webhook._load_webhooks()
        finally:
            webhook.WEBHOOKS_FILE = orig_file
            if os.path.exists(tmp):
                os.remove(tmp)
        return wh, created, cache

    def test_cached_id_hit_reuses_without_create(self):
        from types import SimpleNamespace
        wh, created, _ = self._run(
            "111", [SimpleNamespace(id=111, name="千问大小姐")])
        self.assertEqual(wh.id, 111)
        self.assertEqual(created, [])          # 未重复创建

    def test_stale_id_falls_back_to_name_match(self):
        from types import SimpleNamespace
        wh, created, cache = self._run(
            "999", [SimpleNamespace(id=333, name="千问大小姐")])
        self.assertEqual(wh.id, 333)          # 按名字自愈
        self.assertEqual(created, [])
        self.assertEqual(cache["99"]["QWEN"], "333")   # 缓存已修正


class FollowupWindowTest(unittest.TestCase):
    """对话热度窗口 + 「先接话再玩梗」规则。"""

    def test_followup_window_config_default(self):
        from config import FOLLOWUP_WINDOW_SEC
        self.assertGreaterEqual(FOLLOWUP_WINDOW_SEC, 0)

    def test_cards_teach_answer_first(self):
        from config import SYSTEM_PROMPTS
        zh_q = SYSTEM_PROMPTS["QWEN"]["zh"]
        zh_d = SYSTEM_PROMPTS["DEEPSEEK"]["zh"]
        self.assertIn("先接话", zh_q)
        self.assertIn("先接话", zh_d)
        self.assertIn("答非所问", zh_q)   # 反面示例钉住行为
        self.assertIn("Answer first", SYSTEM_PROMPTS["QWEN"]["en"])


class TimezoneTest(unittest.TestCase):
    """手表校准：注入时间必须走 TIMEZONE，不再是容器 UTC。"""

    def test_now_local_is_beijing_offset(self):
        import utils
        now = utils.now_local()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.utcoffset().total_seconds(), 8 * 3600)

    def test_status_note_carries_timezone_label(self):
        from prompts import build_system_prompt
        p = build_system_prompt("QWEN", "zh")
        self.assertIn("Asia/Shanghai", p)
        self.assertIn("UTC+8", p)

    def test_qwen_card_knows_whales_face(self):
        from config import SYSTEM_PROMPTS
        self.assertIn("鲸鱼尾巴", SYSTEM_PROMPTS["QWEN"]["zh"])
        self.assertIn("whale tail", SYSTEM_PROMPTS["QWEN"]["en"])


class EnvFallbackTest(unittest.TestCase):
    """config._env: empty string (Portainer ${VAR:-}) falls back to default."""

    def test_empty_env_uses_default(self):
        import os
        import config
        key = "PRESENCE_SLEEP_TEST"
        os.environ[key] = "   "  # blank like compose `:-` injection
        try:
            self.assertEqual(config._env(key, "DEFAULT"), "DEFAULT")
            os.environ[key] = "  real  "
            self.assertEqual(config._env(key, "DEFAULT"), "real")
        finally:
            del os.environ[key]

    def test_missing_env_uses_default(self):
        import config
        self.assertEqual(config._env("VRS_UNSET_TEST_KEY", "DEFAULT"), "DEFAULT")


class MemoryTest(unittest.TestCase):
    """Long-term memory: config, helpers round-trip, prompt injection."""

    def test_memory_config_present(self):
        import config
        self.assertTrue(config.MEMORY_ENABLED)
        self.assertGreaterEqual(config.MEMORY_EVERY_N_MESSAGES, 1)

    def test_memory_helpers_roundtrip(self):
        import utils
        gid = "test_guild_000"
        old = utils.get_guild_memory(gid)
        try:
            utils.save_guild_memory(gid, "小明喜欢喝可乐")
            self.assertEqual(utils.get_guild_memory(gid), "小明喜欢喝可乐")
        finally:
            utils.save_guild_memory(gid, old)  # restore

    def test_memory_note_injected(self):
        from prompts import build_memory_note
        note = build_memory_note("zh", "小明喜欢喝可乐")
        self.assertIn("长期记忆", note)
        self.assertIn("小明", note)
        # Empty memory → no injection at all
        self.assertEqual(build_memory_note("zh", ""), "")


class CommandRegistrationTest(unittest.TestCase):
    """commands.setup_commands registers all five slash commands."""

    def test_all_commands_registered(self):
        import commands

        registered = {}

        class FakeTree:
            def command(self, **kwargs):
                def deco(fn):
                    registered[kwargs.get("name")] = fn
                    return fn
                return deco

        class FakeBot:
            tree = FakeTree()

        commands.setup_commands(FakeBot())
        self.assertEqual(
            set(registered),
            {"whoishome", "inspect", "help", "setchannel", "lang"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
