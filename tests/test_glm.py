import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.config import Config, LLMConfig, CharacterConfig
from src.llm import CallParams, LLMError, LLMProcessor


class GLMConfigTests(unittest.TestCase):
    def test_glm_never_uses_unrelated_openai_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unrelated-test-key"}, clear=True):
            config = LLMConfig(provider="glm", api_key_env="GLM_API_KEY")
            self.assertEqual(config.resolve_api_key(), "")
            self.assertFalse(config.api_key_configured)

    def test_explicit_env_name_is_used(self):
        with patch.dict(os.environ, {"GLM_API_KEY": "fake-key-for-unit-test"}, clear=True):
            config = LLMConfig(provider="glm", api_key_env="GLM_API_KEY")
            self.assertEqual(config.resolve_api_key(), "fake-key-for-unit-test")

    def test_dotenv_loads_from_project_root_without_overriding_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("GLM_API_KEY=file-test-key\n", encoding="utf-8")
            (root / "config.yaml").write_text("llm:\n  provider: glm\n  api_key_env: GLM_API_KEY\n", encoding="utf-8")
            with patch("src.config.PROJECT_ROOT", root), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(Config.load().llm.resolve_api_key(), "file-test-key")
            with patch("src.config.PROJECT_ROOT", root), patch.dict(os.environ, {"GLM_API_KEY": "shell-test-key"}, clear=True):
                self.assertEqual(Config.load().llm.resolve_api_key(), "shell-test-key")


class GLMRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_flash_request_omits_provider_specific_thinking_fields(self):
        processor = LLMProcessor(LLMConfig(provider="glm", api_key="fake-unit-key", model="glm-4-flash-250414"), CharacterConfig())
        try:
            kwargs = processor._request_kwargs(CallParams(max_tokens=96))
            self.assertEqual(kwargs["model"], "glm-4-flash-250414")
            self.assertEqual(kwargs["max_tokens"], 96)
            self.assertNotIn("extra_body", kwargs)
        finally:
            await processor.close()

    async def test_missing_key_stops_before_network_request(self):
        with patch.dict(os.environ, {}, clear=True):
            processor = LLMProcessor(LLMConfig(provider="glm", api_key_env="GLM_API_KEY"), CharacterConfig())
        try:
            with patch.object(processor.client.chat.completions, "create", new=AsyncMock()) as create:
                with self.assertRaisesRegex(LLMError, "GLM_API_KEY"):
                    await processor.chat("测试")
                create.assert_not_awaited()
        finally:
            await processor.close()

    async def test_provider_error_does_not_echo_credentials(self):
        processor = LLMProcessor(LLMConfig(provider="glm", api_key="private-test-key"), CharacterConfig())
        try:
            error = RuntimeError("upstream echoed private-test-key")
            self.assertNotIn("private-test-key", processor._safe_failure(error))
        finally:
            await processor.close()


if __name__ == "__main__":
    unittest.main()
