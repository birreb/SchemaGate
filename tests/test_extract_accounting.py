"""What each provider says about tokens, and what each says when it runs out.

Both are per-provider dialects. Anthropic reports cache reads beside the input
count, OpenAI reports them inside it, and Ollama calls them something else
again. Truncation is worse: every one of them returns an ordinary successful
response with an answer that stops mid-JSON, which used to reach the caller as
"returned no parsed output".
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel

from schemagate.errors import ExtractionError
from schemagate.extract.anthropic import MAX_TOKENS, AnthropicExtractor
from schemagate.extract.ollama import OllamaExtractor
from schemagate.extract.openai import OpenAIExtractor


class Row(BaseModel):
    invoice_number: str


class Rows(BaseModel):
    rows: list[Row]


ANSWER = Rows(rows=[Row(invoice_number="INV-1")])
CONTENT = '{"rows": [{"invoice_number": "INV-1"}]}'


# --- Anthropic ---------------------------------------------------------------


@dataclass
class AnthropicUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class AnthropicResponse:
    parsed_output: Any = field(default_factory=lambda: ANSWER)
    stop_reason: str = "end_turn"
    stop_details: Any = None
    usage: Any = None


@dataclass
class AnthropicMessages:
    response: Any = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response if self.response is not None else AnthropicResponse()


@dataclass
class FakeAnthropic:
    messages: AnthropicMessages = field(default_factory=AnthropicMessages)


async def test_anthropic_tokens_are_reported() -> None:
    client = FakeAnthropic(
        AnthropicMessages(
            AnthropicResponse(usage=AnthropicUsage(input_tokens=1200, output_tokens=340))
        )
    )

    result = await AnthropicExtractor(client=client, model="claude-opus-5").extract("doc", Rows)

    assert result.usage.model == "claude-opus-5"
    assert result.usage.input_tokens == 1200
    assert result.usage.output_tokens == 340
    assert result.usage.calls == 1


async def test_anthropic_cache_writes_are_input_and_reads_are_not() -> None:
    """Reads are the discounted ones, so they are the ones kept apart."""
    client = FakeAnthropic(
        AnthropicMessages(
            AnthropicResponse(
                usage=AnthropicUsage(
                    input_tokens=100, cache_creation_input_tokens=50, cache_read_input_tokens=900
                )
            )
        )
    )

    result = await AnthropicExtractor(client=client).extract("doc", Rows)

    assert result.usage.input_tokens == 150
    assert result.usage.cached_input_tokens == 900


async def test_a_provider_that_reports_nothing_still_counts_the_call() -> None:
    result = await AnthropicExtractor(client=FakeAnthropic()).extract("doc", Rows)

    assert result.usage.calls == 1
    assert result.usage.input_tokens == 0, "a missing count is not a free call, it is no answer"


async def test_a_truncated_anthropic_answer_says_it_was_truncated() -> None:
    client = FakeAnthropic(
        AnthropicMessages(AnthropicResponse(parsed_output=None, stop_reason="max_tokens"))
    )

    with pytest.raises(ExtractionError, match="more rows than"):
        await AnthropicExtractor(client=client).extract("doc", Rows)


async def test_the_truncation_message_names_the_limit_and_the_way_out() -> None:
    client = FakeAnthropic(
        AnthropicMessages(AnthropicResponse(parsed_output=None, stop_reason="max_tokens"))
    )

    with pytest.raises(ExtractionError) as raised:
        await AnthropicExtractor(client=client).extract("doc", Rows)

    assert str(MAX_TOKENS) in str(raised.value)
    assert "Split the document" in str(raised.value)


async def test_effort_is_sent_when_configured() -> None:
    client = FakeAnthropic()

    await AnthropicExtractor(client=client, effort="low").extract("doc", Rows)

    assert client.messages.calls[0]["output_config"] == {"effort": "low"}


async def test_effort_is_omitted_when_turned_off() -> None:
    """An older model rejects the field, and an operator has to be able to say so."""
    client = FakeAnthropic()

    await AnthropicExtractor(client=client, effort=None).extract("doc", Rows)

    assert "output_config" not in client.messages.calls[0]


# --- OpenAI ------------------------------------------------------------------


@dataclass
class OpenAIDetails:
    cached_tokens: int = 0


@dataclass
class OpenAIUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_tokens_details: Any = None


@dataclass
class OpenAIMessage:
    parsed: Any = field(default_factory=lambda: ANSWER)
    refusal: str | None = None


@dataclass
class OpenAIChoice:
    message: OpenAIMessage
    finish_reason: str = "stop"


@dataclass
class OpenAICompletion:
    choices: list[OpenAIChoice]
    usage: Any = None


@dataclass
class OpenAICompletions:
    completion: Any = None

    async def parse(self, **kwargs: Any) -> Any:
        return self.completion or OpenAICompletion(choices=[OpenAIChoice(OpenAIMessage())])


@dataclass
class OpenAIChat:
    completions: OpenAICompletions = field(default_factory=OpenAICompletions)


@dataclass
class FakeOpenAI:
    chat: OpenAIChat = field(default_factory=OpenAIChat)


async def test_openai_cached_tokens_are_taken_out_of_the_prompt_count() -> None:
    """OpenAI counts them inside `prompt_tokens`, the opposite of Anthropic."""
    completion = OpenAICompletion(
        choices=[OpenAIChoice(OpenAIMessage())],
        usage=OpenAIUsage(
            prompt_tokens=1000,
            completion_tokens=200,
            prompt_tokens_details=OpenAIDetails(cached_tokens=800),
        ),
    )
    client = FakeOpenAI(OpenAIChat(OpenAICompletions(completion)))

    result = await OpenAIExtractor(client=client, model="gpt-5").extract("doc", Rows)

    assert result.usage.input_tokens == 200
    assert result.usage.cached_input_tokens == 800
    assert result.usage.output_tokens == 200


async def test_a_truncated_openai_answer_says_it_was_truncated() -> None:
    completion = OpenAICompletion(
        choices=[OpenAIChoice(OpenAIMessage(parsed=None), finish_reason="length")]
    )
    client = FakeOpenAI(OpenAIChat(OpenAICompletions(completion)))

    with pytest.raises(ExtractionError, match="more rows than"):
        await OpenAIExtractor(client=client, model="gpt-5").extract("doc", Rows)


# --- Ollama ------------------------------------------------------------------


@dataclass
class OllamaMessage:
    content: str | None = CONTENT


@dataclass
class OllamaResponse:
    message: OllamaMessage = field(default_factory=OllamaMessage)
    prompt_eval_count: int = 0
    eval_count: int = 0
    done_reason: str = "stop"


@dataclass
class FakeOllama:
    response: Any = None

    async def chat(self, **kwargs: Any) -> Any:
        return self.response or OllamaResponse()


async def test_ollama_tokens_are_reported() -> None:
    client = FakeOllama(OllamaResponse(prompt_eval_count=4000, eval_count=600))

    result = await OllamaExtractor(client=client, model="qwen3").extract("doc", Rows)

    assert result.usage.input_tokens == 4000
    assert result.usage.output_tokens == 600
    assert result.usage.model == "qwen3"


async def test_a_local_model_out_of_context_says_so() -> None:
    """The symptom is malformed JSON, and the cause is the window, not the model."""
    client = FakeOllama(
        OllamaResponse(message=OllamaMessage('{"rows": [{"invo'), done_reason="length")
    )

    with pytest.raises(ExtractionError, match="context"):
        await OllamaExtractor(client=client, model="qwen3").extract("doc", Rows)


# --- The default a library caller gets ---------------------------------------


def test_a_library_caller_gets_the_same_effort_as_the_service() -> None:
    """Otherwise using this as a library quietly costs several times more."""
    from schemagate.config import DEFAULT_EFFORT
    from schemagate.extract.factory import make_extractor

    built = make_extractor("anthropic")

    assert built._effort == DEFAULT_EFFORT  # type: ignore[attr-defined]


def test_effort_can_still_be_turned_off_explicitly() -> None:
    from schemagate.extract.factory import make_extractor

    assert make_extractor("anthropic", effort=None)._effort is None  # type: ignore[attr-defined]
