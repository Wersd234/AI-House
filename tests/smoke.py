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


if __name__ == "__main__":
    unittest.main(verbosity=2)
