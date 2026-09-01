from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel

from schemagate.errors import ExtractionError
from schemagate.extract.ollama import OllamaExtractor


class Row(BaseModel):
    invoice_number: str
    total: str


class Rows(BaseModel):
    rows: list[Row]


@dataclass
class FakeMessage:
    content: str | None


@dataclass
class FakeResponse:
    message: FakeMessage


@dataclass
class FakeClient:
    """Records what was sent and replies with whatever the test supplies."""

    reply: str | None = '{"rows": [{"invoice_number": "INV-1", "total": "10.00"}]}'
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def chat(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return FakeResponse(message=FakeMessage(content=self.reply))


def extractor(client: FakeClient) -> OllamaExtractor:
    return OllamaExtractor(client=client, model="qwen3")


async def test_returns_a_validated_model() -> None:
    result = await extractor(FakeClient()).extract("Invoice INV-1 total 10.00", Rows)

    assert isinstance(result.value, Rows)
    assert result.value.rows[0].invoice_number == "INV-1"


async def test_the_schema_is_sent_as_the_output_format() -> None:
    client = FakeClient()

    await extractor(client).extract("anything", Rows)

    assert client.calls[0]["format"] == Rows.model_json_schema(), (
        "the schema is the whole guarantee; sending it is what constrains generation"
    )


async def test_generation_is_made_deterministic() -> None:
    client = FakeClient()

    await extractor(client).extract("anything", Rows)

    options = client.calls[0]["options"]
    assert options["temperature"] == 0
    assert "seed" in options, (
        "a local runtime still accepts a seed, unlike the hosted frontier models, "
        "so the same document should give the same answer twice"
    )


async def test_the_document_reaches_the_model() -> None:
    client = FakeClient()

    await extractor(client).extract("Invoice INV-7 total 99.00", Rows)

    contents = [message["content"] for message in client.calls[0]["messages"]]
    assert any("INV-7" in content for content in contents)


async def test_the_instructions_do_not_vary_between_calls() -> None:
    client = FakeClient()
    subject = extractor(client)

    await subject.extract("first document", Rows)
    await subject.extract("second document", Rows)

    first, second = (call["messages"][0] for call in client.calls)
    assert first == second, "a prefix that changes per request cannot be cached"
    assert first["role"] == "system"


async def test_the_configured_model_is_used() -> None:
    client = FakeClient()

    await OllamaExtractor(client=client, model="llama3.2").extract("x", Rows)

    assert client.calls[0]["model"] == "llama3.2"


async def test_output_that_is_not_json_is_reported() -> None:
    client = FakeClient(reply="I could not find any invoices in this document.")

    with pytest.raises(ExtractionError) as caught:
        await extractor(client).extract("x", Rows)

    assert "qwen3" in str(caught.value), "the message should name the model that misbehaved"


async def test_output_that_does_not_match_the_schema_is_reported() -> None:
    client = FakeClient(reply='{"rows": [{"invoice_number": "INV-1"}]}')

    with pytest.raises(ExtractionError):
        await extractor(client).extract("x", Rows)


async def test_an_empty_reply_is_reported() -> None:
    with pytest.raises(ExtractionError):
        await extractor(FakeClient(reply=None)).extract("x", Rows)


async def test_a_server_that_is_not_running_is_reported_plainly() -> None:
    client = FakeClient(error=ConnectionError("connection refused"))

    with pytest.raises(ExtractionError) as caught:
        await extractor(client).extract("x", Rows)

    assert "ollama" in str(caught.value).lower(), (
        "the most common failure is that nobody started the server, "
        "so the error should say which server"
    )


def test_the_adapter_satisfies_the_extractor_protocol() -> None:
    from schemagate.extract.base import Extractor

    subject: Extractor = OllamaExtractor(client=FakeClient(), model="qwen3")

    assert subject is not None, "assignment is the assertion; mypy checks the shape"
