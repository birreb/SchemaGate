"""A server that speaks the OpenAI API may not constrain output to a schema.

Groq does so only for some models, Ollama and vLLM for all, and a few hosts for
none. The adapter asks for the schema first, falls back to plain JSON when the
server refuses, and says which happened, because the two are different
promises and the stage should not claim one when it got the other.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel

from schemagate.errors import ExtractionError
from schemagate.extract.openai import OpenAIExtractor
from schemagate.pipeline import _describe_extract
from schemagate.schema.spec import ColumnSpec, TableSchema


class Row(BaseModel):
    invoice_number: str
    total: str


class Rows(BaseModel):
    rows: list[Row]


ANSWER = Rows(rows=[Row(invoice_number="INV-1", total="10.00")])


class RefusalError(Exception):
    """What the SDK raises for a 400: a status code and the server's message."""

    status_code = 400


@dataclass
class Message:
    parsed: Any = None
    content: str | None = None
    refusal: str | None = None


@dataclass
class Choice:
    message: Message
    finish_reason: str = "stop"


@dataclass
class Completion:
    choices: list[Choice]


@dataclass
class Completions:
    parse_error: Exception | None = None
    create_content: str = '{"rows": [{"invoice_number": "INV-1", "total": "10.00"}]}'
    parse_calls: list[dict[str, Any]] = field(default_factory=list)
    create_calls: list[dict[str, Any]] = field(default_factory=list)

    async def parse(self, **kwargs: Any) -> Completion:
        self.parse_calls.append(kwargs)
        if self.parse_error is not None:
            raise self.parse_error
        return Completion(choices=[Choice(Message(parsed=ANSWER))])

    async def create(self, **kwargs: Any) -> Completion:
        self.create_calls.append(kwargs)
        return Completion(choices=[Choice(Message(content=self.create_content))])


@dataclass
class Chat:
    completions: Completions


@dataclass
class Client:
    chat: Chat


def client(**kwargs: Any) -> Client:
    return Client(chat=Chat(completions=Completions(**kwargs)))


async def test_a_server_that_honours_the_schema_is_reported_as_constrained() -> None:
    result = await OpenAIExtractor(client(), model="m").extract("doc", Rows)

    assert result.constrained is True


async def test_a_server_that_refuses_the_schema_is_asked_for_plain_json() -> None:
    fake = client(parse_error=RefusalError("response_format json_schema is not supported"))

    result = await OpenAIExtractor(fake, model="m").extract("doc", Rows)

    assert result.value == ANSWER
    assert result.constrained is False
    assert fake.chat.completions.create_calls[0]["response_format"] == {"type": "json_object"}


async def test_the_fallback_still_validates_against_the_table() -> None:
    fake = client(
        parse_error=RefusalError("strict mode is not supported"),
        create_content='{"rows": [{"invoice_number": "INV-1", "amount": "10.00"}]}',
    )

    with pytest.raises(ExtractionError) as caught:
        await OpenAIExtractor(fake, model="m").extract("doc", Rows)

    assert "does not match" in str(caught.value)


async def test_other_400s_are_not_retried_as_plain_json() -> None:
    fake = client(parse_error=RefusalError("model 'nope' does not exist"))

    with pytest.raises(ExtractionError):
        await OpenAIExtractor(fake, model="nope").extract("doc", Rows)

    assert fake.chat.completions.create_calls == [], (
        "an unknown model is a real error, and retrying it as plain JSON would hide it"
    )


async def test_a_transport_failure_is_not_retried_either() -> None:
    fake = client(parse_error=TimeoutError("slow"))

    with pytest.raises(ExtractionError):
        await OpenAIExtractor(fake, model="m").extract("doc", Rows)

    assert fake.chat.completions.create_calls == []


def test_the_stage_says_when_the_output_was_only_checked_afterwards() -> None:
    table = TableSchema(
        schema="public",
        name="t",
        columns=(ColumnSpec(name="a", data_type="text", nullable=True, ordinal=1),),
    )

    constrained = _describe_extract(1, table, "text", constrained=True)
    free = _describe_extract(1, table, "text", constrained=False)
    unknown = _describe_extract(1, table, "text")

    assert "free JSON" in free
    assert "free JSON" not in constrained
    assert "free JSON" not in unknown
