"""Designer settings as plain values, and the provider they describe."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from urllib.parse import urlsplit

from .anthropic_provider import AnthropicProvider
from .providers import LocalServerProvider, Provider, ProviderError


PROVIDERS = ("off", "anthropic", "local")
DEFAULTS = {
    "designer/provider": "off",
    "designer/anthropic_model": "claude-opus-5-5",
    "designer/anthropic_api_key": "",
    "designer/local_base_url": "http://localhost:8080/v1",
    "designer/local_model": "",
    "designer/local_api_key": "",
}
URL_RULE = "Use an http or https address, such as http://localhost:8080/v1."


@dataclass(frozen=True)
class DesignerConfig:
    provider: str
    anthropic_model: str
    anthropic_api_key: str
    local_base_url: str
    local_model: str
    local_api_key: str

    @classmethod
    def from_mapping(cls, values: Mapping) -> "DesignerConfig":
        """Missing or None values take the default; everything is text, stripped."""
        clean = {}
        for key, default in DEFAULTS.items():
            value = values.get(key) if isinstance(values, Mapping) else None
            clean[key] = default if value is None else str(value).strip()
        if clean["designer/provider"] not in PROVIDERS:
            clean["designer/provider"] = "off"
        if not clean["designer/anthropic_model"]:
            clean["designer/anthropic_model"] = DEFAULTS["designer/anthropic_model"]
        return cls(*(clean[key] for key in DEFAULTS))

    def as_mapping(self) -> dict:
        return {key: getattr(self, field.name) for key, field in zip(DEFAULTS, fields(self))}

    @property
    def is_configured(self) -> bool:
        return self.provider != "off"


def validate_base_url(url: str) -> str:
    url = str(url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(URL_RULE)
    return url.rstrip("/")


def url_is_loopback(url: str) -> bool:
    try:
        host = (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return False
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")


def make_provider(config: DesignerConfig) -> Provider:
    if config.provider == "anthropic":
        return AnthropicProvider(model=config.anthropic_model, api_key=config.anthropic_api_key)
    if config.provider == "local":
        if not config.local_model:
            raise ProviderError("model", "Choose a model name in Set up AI designer.")
        try:
            base_url = validate_base_url(config.local_base_url)
        except ValueError as exc:
            raise ProviderError("not_running", str(exc)) from None
        return LocalServerProvider(base_url=base_url, model=config.local_model, api_key=config.local_api_key)
    raise ProviderError("not_configured", "The AI designer isn't set up yet.")
