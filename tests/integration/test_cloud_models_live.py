"""Opt-in live smoke tests for DeepSeekModelPort and GeminiModelPort.

Skipped unconditionally during offline CI (`./ci.sh`). Enable explicitly with:
    SAS_LIVE_MODEL_TESTS=1
plus the corresponding provider env var (`DEEPSEEK_API_KEY` / `GEMINI_API_KEY`).
"""
from __future__ import annotations

import os
import unittest

from core import ModelRequest
from core.enums import ModelRole
from models import DeepSeekModelPort, GeminiModelPort


def _live_enabled(env_var: str) -> bool:
    return os.environ.get("SAS_LIVE_MODEL_TESTS") == "1" and bool(os.environ.get(env_var, "").strip())


class CloudModelLiveSmokeTests(unittest.TestCase):

    @unittest.skipUnless(_live_enabled("GEMINI_API_KEY"), "set SAS_LIVE_MODEL_TESTS=1 and GEMINI_API_KEY to run")
    def test_gemini_live_minimal_generate(self):
        port = GeminiModelPort(max_tokens=32, temperature=0.0)
        resp = port.generate(ModelRequest(
            role=ModelRole.GENERAL_AGENT,
            messages=[{"role": "user", "content": "Reply with the single word OK."}],
        ))
        self.assertNotEqual(resp.finishReason, "provider_error", getattr(port.last_error, "message", ""))
        self.assertTrue(resp.content.strip())
        self.assertIsNotNone(resp.usage)
        self.assertGreater(resp.usage.get("total_tokens", 0), 0)

    @unittest.skipUnless(_live_enabled("DEEPSEEK_API_KEY"), "set SAS_LIVE_MODEL_TESTS=1 and DEEPSEEK_API_KEY to run")
    def test_deepseek_live_minimal_generate(self):
        port = DeepSeekModelPort(max_tokens=32, temperature=0.0)
        resp = port.generate(ModelRequest(
            role=ModelRole.GENERAL_AGENT,
            messages=[{"role": "user", "content": "Reply with the single word OK."}],
        ))
        self.assertNotEqual(resp.finishReason, "provider_error", getattr(port.last_error, "message", ""))
        self.assertTrue(resp.content.strip())
        self.assertIsNotNone(resp.usage)
        self.assertGreater(resp.usage.get("total_tokens", 0), 0)


if __name__ == "__main__":
    unittest.main()
