"""Designer settings: defaults, URL rules, the provider factory, and the app's settings round trip."""
import unittest
from unittest.mock import patch

import tests  # noqa: F401  (isolates settings)
from pixelheart.designer_config import load_designer_config, save_designer_config
from pixelheart_designer.config import DEFAULTS, DesignerConfig, make_provider, url_is_loopback, validate_base_url
from pixelheart_designer.providers import LocalServerProvider, ProviderError


class ConfigTests(unittest.TestCase):
    def test_defaults_and_mapping(self):
        config = DesignerConfig.from_mapping({})
        self.assertEqual(config.as_mapping(), DEFAULTS)
        self.assertEqual(DesignerConfig.from_mapping({"designer/provider": "bogus", "designer/local_model": " qwen "}).provider, "off")
        self.assertEqual(DesignerConfig.from_mapping({"designer/local_model": " qwen "}).local_model, "qwen")

    def test_default_address_is_where_llama_server_and_mlx_listen(self):
        self.assertEqual(DEFAULTS["designer/local_base_url"], "http://localhost:8080/v1")

    def test_url_rules(self):
        self.assertEqual(validate_base_url("http://localhost:8080/v1/"), "http://localhost:8080/v1")
        with self.assertRaises(ValueError):
            validate_base_url("ftp://x")
        with self.assertRaises(ValueError):
            validate_base_url("")
        self.assertTrue(url_is_loopback("http://127.0.0.1:1234/v1"))
        self.assertFalse(url_is_loopback("http://192.168.1.20:11434/v1"))

    def test_factory(self):
        with self.assertRaises(ProviderError) as off:
            make_provider(DesignerConfig.from_mapping({}))
        self.assertEqual(off.exception.kind, "not_configured")
        self.assertIsInstance(make_provider(DesignerConfig.from_mapping({"designer/provider": "local", "designer/local_model": "qwen"})), LocalServerProvider)
        with self.assertRaises(ProviderError) as blank:
            make_provider(DesignerConfig.from_mapping({"designer/provider": "local"}))
        self.assertEqual(blank.exception.kind, "model")
        with patch("pixelheart_designer.config.AnthropicProvider") as anthropic_cls:
            make_provider(DesignerConfig.from_mapping({"designer/provider": "anthropic", "designer/anthropic_api_key": "k"}))
        anthropic_cls.assert_called_once_with(model="claude-opus-5-5", api_key="k")


class AppConfigTests(unittest.TestCase):
    def test_round_trip_through_isolated_settings(self):
        save_designer_config({"designer/provider": "local", "designer/local_model": "qwen"})
        config = load_designer_config()
        self.assertEqual((config.provider, config.local_model, config.anthropic_model), ("local", "qwen", "claude-opus-5-5"))


if __name__ == "__main__":
    unittest.main()
