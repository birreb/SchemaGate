import base64
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from schemagate.extract.anthropic import AnthropicExtractor
from schemagate.extract.ollama import OllamaExtractor
from schemagate.extract.openai import OpenAIExtractor
from schemagate.ingest.images import NormalisedImage


class Row(BaseModel):
    invoice_number: str


class Rows(BaseModel):
    rows: list[Row]


ANSWER = Rows(rows=[Row(invoice_number="INV-1")])
PIXEL = NormalisedImage(data=b"\x89PNG-not-really", media_type="image/png", width=8, height=8)
REPLY = '{"rows": [{"invoice_number": "INV-1"}]}'


@dataclass
class Recorder:
    """One fake shaped three ways, since the point is that the callers differ."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def record(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return None


@dataclass
class FakeAnthropic:
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def messages(self) -> Any:
        return self

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return type("Response", (), {"parsed_output": ANSWER, "stop_reason": "end_turn"})()


@dataclass
class FakeOpenAI:
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def chat(self) -> Any:
        return self

    @property
    def completions(self) -> Any:
        return self

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        message = type("Message", (), {"parsed": ANSWER, "refusal": None})()
        return type("Completion", (), {"choices": [type("Choice", (), {"message": message})()]})()


@dataclass
class FakeOllama:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def chat(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return type("Response", (), {"message": type("M", (), {"content": REPLY})()})()


async def test_anthropic_sends_an_image_block() -> None:
    client = FakeAnthropic()

    await AnthropicExtractor(client=client).extract("read this", Rows, images=(PIXEL,))

    content = client.calls[0]["messages"][0]["content"]
    image = next(part for part in content if part["type"] == "image")

    assert image["source"]["media_type"] == "image/png"
    assert base64.b64decode(image["source"]["data"]) == PIXEL.data


async def test_anthropic_puts_the_image_before_the_text() -> None:
    client = FakeAnthropic()

    await AnthropicExtractor(client=client).extract("read this", Rows, images=(PIXEL,))

    blocks = [part["type"] for part in client.calls[0]["messages"][0]["content"]]

    assert blocks.index("image") < blocks.index("text"), (
        "the instruction should read as being about the image above it"
    )


async def test_openai_sends_a_data_url() -> None:
    client = FakeOpenAI()

    await OpenAIExtractor(client=client, model="gpt-5").extract("read", Rows, images=(PIXEL,))

    content = client.calls[0]["messages"][-1]["content"]
    image = next(part for part in content if part["type"] == "image_url")

    assert image["image_url"]["url"].startswith("data:image/png;base64,")


async def test_ollama_sends_bytes_on_the_message() -> None:
    client = FakeOllama()

    await OllamaExtractor(client=client, model="qwen3").extract("read", Rows, images=(PIXEL,))

    user = client.calls[0]["messages"][-1]

    assert user["images"] == [PIXEL.data], (
        "ollama takes raw bytes on the message rather than a content block"
    )


async def test_text_only_calls_are_unchanged() -> None:
    client = FakeAnthropic()

    await AnthropicExtractor(client=client).extract("just text", Rows)

    assert isinstance(client.calls[0]["messages"][0]["content"], str), (
        "a document with no images sends a plain string, as it always has"
    )


async def test_the_model_still_comes_back() -> None:
    result = await AnthropicExtractor(client=FakeAnthropic()).extract("x", Rows, images=(PIXEL,))

    assert result == ANSWER
