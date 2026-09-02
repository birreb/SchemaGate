from collections.abc import Sequence
from typing import Any, Protocol, cast

from pydantic import ValidationError

from schemagate.errors import ExtractionError
from schemagate.extract.base import SYSTEM_PROMPT, Extracted, ModelT, Usage, counted, encoded
from schemagate.ingest.images import NormalisedImage

# What a server says when it does not implement schema-constrained output.
# Groq, Together, Fireworks, DeepInfra, vLLM and Ollama all speak this API,
# and they differ in which models honour `json_schema` with `strict`. The
# refusal is a 400 naming the parameter; anything else is a real failure.
SCHEMA_REFUSAL_WORDS = ("response_format", "json_schema", "strict", "structured output")


class Completions(Protocol):
    # Spelled out rather than **kwargs, which would claim the client takes
    # any keyword at all and would not describe the real one.
    async def parse(self, *, model: str, messages: Any, response_format: Any) -> Any: ...

    async def create(self, *, model: str, messages: Any, response_format: Any) -> Any: ...


class Chat(Protocol):
    @property
    def completions(self) -> Completions: ...


class OpenAIClient(Protocol):
    """The slice of `openai.AsyncOpenAI` this adapter uses."""

    # A property, not a plain attribute: a Protocol attribute must be
    # settable, and the SDK exposes this read-only.
    @property
    def chat(self) -> Chat: ...


class OpenAIExtractor:
    """Extract through the OpenAI API, or anything that speaks it.

    The model name is required rather than defaulted. OpenAI's names change
    often enough that a stale default would fail at the worst moment, with an
    error about a model the operator never chose.

    Schema-constrained output is asked for first. A server that refuses it is
    asked again for plain JSON, which is then validated here, and the result
    says which of the two happened. The rows are checked either way; what
    differs is whether a column the table does not have was impossible to
    generate or merely rejected afterwards.
    """

    def __init__(self, client: OpenAIClient, model: str) -> None:
        self._client = client
        self._model = model

    async def extract(
        self,
        document: str,
        model: type[ModelT],
        images: Sequence[NormalisedImage] = (),
    ) -> Extracted[ModelT]:
        messages = [
            # OpenAI has no separate instructions parameter, so they lead the
            # conversation instead. The protocol hides the difference.
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _content(document, images)},
        ]
        try:
            completion = await self._client.chat.completions.parse(
                model=self._model, messages=messages, response_format=model
            )
        except Exception as error:
            if not _refused_schema(error):
                raise ExtractionError(
                    f"Could not reach OpenAI for model {self._model!r}: {error}"
                ) from error
            return await self._extract_unconstrained(messages, model)

        choice = completion.choices[0]
        message = choice.message
        self._check(choice, message)

        parsed = getattr(message, "parsed", None)
        if parsed is None:
            raise ExtractionError(f"Model {self._model!r} returned no parsed output.")
        # The SDK returns an instance of the model it was given, but says so
        # only through an untyped attribute.
        return Extracted(
            value=cast(ModelT, parsed), usage=_usage(completion, self._model), constrained=True
        )

    async def _extract_unconstrained(self, messages: Any, model: type[ModelT]) -> Extracted[ModelT]:
        """The second attempt, against a server that only promises valid JSON."""
        try:
            completion = await self._client.chat.completions.create(
                model=self._model, messages=messages, response_format={"type": "json_object"}
            )
        except Exception as error:
            raise ExtractionError(
                f"Could not reach the server for model {self._model!r}: {error}"
            ) from error

        choice = completion.choices[0]
        message = choice.message
        self._check(choice, message)

        content = getattr(message, "content", None)
        if not content:
            raise ExtractionError(f"Model {self._model!r} returned an empty response.")
        try:
            value = model.model_validate_json(content)
        except ValidationError as error:
            raise ExtractionError(
                f"Model {self._model!r} returned output that does not match "
                f"{model.__name__}: {error}"
            ) from error
        return Extracted(value=value, usage=_usage(completion, self._model), constrained=False)

    def _check(self, choice: Any, message: Any) -> None:
        if getattr(message, "refusal", None):
            raise ExtractionError(
                f"Model {self._model!r} refused to process this document. ({message.refusal})"
            )

        # Truncation arrives as a successful response whose parsed field is
        # empty, because the JSON never closed. Reported separately from a
        # genuine empty result.
        if getattr(choice, "finish_reason", None) == "length":
            raise ExtractionError(
                f"The document produced more rows than the output limit allows, so "
                f"model {self._model!r} was cut off mid-answer. Split the document "
                f"and extract it in parts."
            )


def _refused_schema(error: Exception) -> bool:
    """Whether a failure is the server declining constrained output.

    A 400 that names the parameter. A transport error, a bad key, or a model
    that does not exist must still be reported, not retried as plain JSON.
    """
    if getattr(error, "status_code", None) != 400:
        return False
    text = str(error).lower()
    return any(word in text for word in SCHEMA_REFUSAL_WORDS)


def _usage(completion: Any, model: str) -> Usage:
    """Read the token counts off the completion.

    OpenAI counts cached tokens inside `prompt_tokens` rather than beside it,
    the opposite of Anthropic, so the cached share is subtracted out to leave
    the two fields meaning the same thing in both adapters.
    """
    reported = getattr(completion, "usage", None)
    if reported is None:
        return Usage(model=model)

    prompt = counted(reported, "prompt_tokens", "input_tokens")
    cached = counted(getattr(reported, "prompt_tokens_details", None), "cached_tokens")
    return Usage(
        model=model,
        input_tokens=max(prompt - cached, 0),
        output_tokens=counted(reported, "completion_tokens", "output_tokens"),
        cached_input_tokens=cached,
    )


def _content(document: str, images: Sequence[NormalisedImage]) -> Any:
    """The user message. OpenAI takes an image as a data URL in a content part."""
    if not images:
        return document
    parts: list[dict[str, Any]] = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{image.media_type};base64,{encoded(image)}"},
        }
        for image in images
    ]
    parts.append({"type": "text", "text": document})
    return parts
