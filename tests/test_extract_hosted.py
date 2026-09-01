from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel

from schemagate.errors import ExtractionError
from schemagate.extract.anthropic import AnthropicExtractor
from schemagate.extract.base import ModelT
from schemagate.extract.openai import OpenAIExtractor


class Row(BaseModel):
    invoice_number: str
    total: str


class Rows(BaseModel):
    rows: list[Row]


ANSWER = Rows(rows=[Row(invoice_number="INV-1", total="10.00")])


# --- Anthropic ---------------------------------------------------------------


@dataclass
class FakeAnthropicResponse:
    parsed_output: Any
    stop_reason: str = "end_turn"
    stop_details: Any = None


@dataclass
class FakeMessages:
    response: Any = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response or FakeAnthropicResponse(parsed_output=ANSWER)


@dataclass
class FakeAnthropic:
    messages: FakeMessages = field(default_factory=FakeMessages)


async def test_anthropic_returns_a_validated_model() -> None:
    client = FakeAnthropic()

    result = await AnthropicExtractor(client=client).extract("Invoice INV-1", Rows)

    assert result.rows[0].invoice_number == "INV-1"


async def test_anthropic_is_given_the_compiled_model() -> None:
    client = FakeAnthropic()

    await AnthropicExtractor(client=client).extract("x", Rows)

    assert client.messages.calls[0]["output_format"] is Rows, (
        "the SDK constrains generation from the model itself, so it must be passed through"
    )


async def test_anthropic_uses_the_current_default_model() -> None:
    client = FakeAnthropic()

    await AnthropicExtractor(client=client).extract("x", Rows)

    assert client.messages.calls[0]["model"] == "claude-opus-5"


async def test_anthropic_keeps_instructions_out_of_the_conversation() -> None:
    client = FakeAnthropic()

    await AnthropicExtractor(client=client).extract("the document", Rows)

    call = client.messages.calls[0]
    assert isinstance(call["system"], str), (
        "Anthropic takes instructions as a separate parameter, and keeping them "
        "there is what lets the prefix cache"
    )
    assert [m["role"] for m in call["messages"]] == ["user"]
    assert "the document" in call["messages"][0]["content"]


async def test_anthropic_refusal_is_reported_rather_than_read() -> None:
    client = FakeAnthropic(
        messages=FakeMessages(
            response=FakeAnthropicResponse(parsed_output=None, stop_reason="refusal")
        )
    )

    with pytest.raises(ExtractionError) as caught:
        await AnthropicExtractor(client=client).extract("x", Rows)

    assert "refus" in str(caught.value).lower(), (
        "a refusal arrives as a normal 200 response, so it has to be checked "
        "before the content is touched"
    )


async def test_anthropic_transport_failure_is_reported() -> None:
    client = FakeAnthropic(messages=FakeMessages(error=ConnectionError("no route")))

    with pytest.raises(ExtractionError):
        await AnthropicExtractor(client=client).extract("x", Rows)


async def test_anthropic_empty_output_is_reported() -> None:
    client = FakeAnthropic(
        messages=FakeMessages(response=FakeAnthropicResponse(parsed_output=None))
    )

    with pytest.raises(ExtractionError):
        await AnthropicExtractor(client=client).extract("x", Rows)


# --- OpenAI ------------------------------------------------------------------


@dataclass
class FakeOpenAIMessage:
    parsed: Any = None
    refusal: str | None = None


@dataclass
class FakeChoice:
    message: FakeOpenAIMessage


@dataclass
class FakeCompletion:
    choices: list[FakeChoice]


@dataclass
class FakeCompletions:
    message: FakeOpenAIMessage | None = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def parse(self, **kwargs: Any) -> FakeCompletion:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return FakeCompletion(
            choices=[FakeChoice(self.message or FakeOpenAIMessage(parsed=ANSWER))]
        )


@dataclass
class FakeChat:
    completions: FakeCompletions = field(default_factory=FakeCompletions)


@dataclass
class FakeOpenAI:
    chat: FakeChat = field(default_factory=FakeChat)


def openai_extractor(client: FakeOpenAI) -> OpenAIExtractor:
    return OpenAIExtractor(client=client, model="gpt-5")


async def test_openai_returns_a_validated_model() -> None:
    result = await openai_extractor(FakeOpenAI()).extract("Invoice INV-1", Rows)

    assert result.rows[0].total == "10.00"


async def test_openai_is_given_the_compiled_model() -> None:
    client = FakeOpenAI()

    await openai_extractor(client).extract("x", Rows)

    assert client.chat.completions.calls[0]["response_format"] is Rows


async def test_openai_takes_instructions_as_a_system_message() -> None:
    client = FakeOpenAI()

    await openai_extractor(client).extract("the document", Rows)

    roles = [m["role"] for m in client.chat.completions.calls[0]["messages"]]
    assert roles == ["system", "user"], (
        "OpenAI has no separate instructions parameter, so the protocol hides "
        "the difference from the pipeline"
    )


async def test_openai_refusal_is_reported() -> None:
    client = FakeOpenAI(
        chat=FakeChat(completions=FakeCompletions(message=FakeOpenAIMessage(refusal="no")))
    )

    with pytest.raises(ExtractionError) as caught:
        await openai_extractor(client).extract("x", Rows)

    assert "refus" in str(caught.value).lower()


async def test_openai_transport_failure_is_reported() -> None:
    client = FakeOpenAI(chat=FakeChat(completions=FakeCompletions(error=TimeoutError("slow"))))

    with pytest.raises(ExtractionError):
        await openai_extractor(client).extract("x", Rows)


# --- both --------------------------------------------------------------------


def test_both_satisfy_the_same_protocol() -> None:
    from schemagate.extract.base import Extractor

    hosted: list[Extractor] = [
        AnthropicExtractor(client=FakeAnthropic()),
        OpenAIExtractor(client=FakeOpenAI(), model="gpt-5"),
    ]

    assert len(hosted) == 2, "assignment is the assertion; mypy checks the shapes"


def test_a_model_name_is_never_invented_for_openai() -> None:
    with pytest.raises(TypeError):
        OpenAIExtractor(client=FakeOpenAI())  # type: ignore[call-arg]


async def test_the_same_document_and_model_work_through_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anthropic_rows = await AnthropicExtractor(client=FakeAnthropic()).extract("doc", Rows)
    openai_rows = await openai_extractor(FakeOpenAI()).extract("doc", Rows)

    assert anthropic_rows == openai_rows, "the protocol exists to make these interchangeable"


def test_extractors_are_typed_against_the_protocol() -> None:
    def use(extractor: Any, model: type[ModelT]) -> type[ModelT]:
        return model

    assert use(AnthropicExtractor(client=FakeAnthropic()), Rows) is Rows
