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
        self.assertIn("大肥鱼", p)
        self.assertIn("System Status", p)

    def test_handover_note(self):
        from prompts import build_handover_note
        # DEEPSEEK left, QWEN is on duty → the note names the leaving girl
        p = build_handover_note("zh", "DEEPSEEK", "QWEN")
        self.assertIn("大肥鱼", p)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
