"""用户可配置大模型：厂商解析、密钥脱敏、请求级覆盖。"""

from __future__ import annotations

import unittest

from server.chat_service import ChatRequest, chat_service, make_provider, normalize_provider
from server.hidden_tables import drop_hidden_table_rows, is_hidden_pg_table, sql_touches_hidden
from server.llm_profile_service import mask_key
from tools.sql_toolkit import SQLToolkitManager


class LlmProfileUnitTests(unittest.TestCase):
    def test_normalize_provider_aliases(self):
        self.assertEqual(normalize_provider("DeepSeek"), "deepseek")
        self.assertEqual(normalize_provider("claude"), "anthropic")
        self.assertEqual(normalize_provider("kimi"), "openai_compat")
        self.assertEqual(normalize_provider("custom"), "openai_compat")

    def test_mask_key(self):
        self.assertEqual(mask_key(""), "")
        self.assertEqual(mask_key("sk-abcdefghijklmnop"), "sk-a****mnop")
        self.assertEqual(mask_key("short"), "****")

    def test_make_provider_openai_compat(self):
        p = make_provider(
            "openai_compat",
            api_key="sk-user-key",
            base_url="https://example.com/v1",
            default_model="my-model",
        )
        self.assertEqual(p.api_key, "sk-user-key")
        self.assertEqual(p.get_chat_url(), "https://example.com/v1/chat/completions")
        self.assertIn("Bearer sk-user-key", p.get_headers()["Authorization"])

    def test_make_provider_anthropic_header(self):
        p = make_provider("anthropic", api_key="ak-1", default_model="claude-3-haiku")
        self.assertEqual(p.get_headers().get("x-api-key"), "ak-1")
        self.assertTrue(p.get_chat_url().endswith("/messages"))

    def test_request_inline_credentials_override_env(self):
        req = ChatRequest(
            query="hi",
            model="custom-model",
            provider="openai_compat",
            api_key="sk-from-ui",
            base_url="https://gateway.example/v1",
        )
        provider, actual = chat_service.resolve_for_request(req)
        self.assertIsNotNone(provider)
        self.assertEqual(actual, "custom-model")
        self.assertEqual(provider.api_key, "sk-from-ui")
        self.assertEqual(provider.base_url, "https://gateway.example/v1")

    def test_config_table_hidden_from_storage_and_sql(self):
        self.assertTrue(is_hidden_pg_table("user_llm_profile"))
        self.assertTrue(sql_touches_hidden('SELECT * FROM "user_llm_profile"'))
        rows = drop_hidden_table_rows(
            [{"tablename": "stock_daily"}, {"tablename": "user_llm_profile"}],
            "tablename",
        )
        self.assertEqual([r["tablename"] for r in rows], ["stock_daily"])
        err = SQLToolkitManager._validate_readonly_sql("select api_key from user_llm_profile")
        self.assertIsNotNone(err)


if __name__ == "__main__":
    unittest.main()
