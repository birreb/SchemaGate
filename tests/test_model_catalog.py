from dataclasses import dataclass, field
from typing import Any

import pytest

from schemagate.errors import ExtractionError
from schemagate.extract.catalog import ANTHROPIC_KNOWN, list_models


@dataclass
class Entry:
    id: str


@dataclass
class Page:
    data: list[Entry]


@dataclass
class Models:
    entries: list[str] = field(default_factory=list)
    error: Exception | None = None

    async def list(self, **kwargs: Any) -> Page:
        if self.error is not None:
            raise self.error
        return Page(data=[Entry(id=name) for name in self.entries])


@dataclass
class FakeClient:
    models: Models = field(default_factory=Models)


@dataclass
class OllamaEntry:
    model: str


@dataclass
class OllamaListing:
    models: list[OllamaEntry]


@dataclass
class FakeOllama:
    entries: list[str] = field(default_factory=list)
    error: Exception | None = None

    async def list(self) -> OllamaListing:
        if self.error is not None:
            raise self.error
        return OllamaListing(models=[OllamaEntry(model=name) for name in self.entries])


async def test_models_come_from_the_provider() -> None:
    client = FakeClient(models=Models(entries=["claude-opus-5", "claude-sonnet-5"]))

    result = await list_models("anthropic", client)

    assert result.models == ("claude-opus-5", "claude-sonnet-5")
    assert result.source == "provider", "asking the provider is what keeps the list current"


async def test_ollama_reports_the_models_actually_pulled() -> None:
    client = FakeOllama(entries=["qwen3:latest", "llama3.2:3b"])

    result = await list_models("ollama", client)

    assert result.models == ("qwen3:latest", "llama3.2:3b")


async def test_anthropic_falls_back_to_a_documented_list_without_a_key() -> None:
    client = FakeClient(models=Models(error=ExtractionError("no credentials")))

    result = await list_models("anthropic", client)

    assert result.source == "fallback"
    assert "claude-opus-5" in result.models
    assert result.detail, "the reason the live list failed is worth showing"


async def test_openai_invents_nothing_when_it_cannot_ask() -> None:
    client = FakeClient(models=Models(error=ExtractionError("no credentials")))

    result = await list_models("openai", client)

    assert result.models == (), (
        "OpenAI names change often, so an offline guess would offer models that "
        "may not exist; better to show none and say why"
    )
    assert result.detail


async def test_the_documented_anthropic_list_leads_with_the_default() -> None:
    assert ANTHROPIC_KNOWN[0] == "claude-opus-5"


async def test_an_unknown_provider_is_refused() -> None:
    with pytest.raises(ValueError):
        await list_models("wishful", FakeClient())
