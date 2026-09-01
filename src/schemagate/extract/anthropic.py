from typing import Any, Protocol, cast

from schemagate.errors import ExtractionError
from schemagate.extract.base import SYSTEM_PROMPT, ModelT

DEFAULT_MODEL = "claude-opus-5"

# Enough room for a long document's worth of rows without inviting a timeout.
MAX_TOKENS = 16000


class Messages(Protocol):
    # Spelled out rather than **kwargs, which would claim the client takes
    # any keyword at all and would not describe the real one.
    async def parse(
        self,
        *,
        model: str,
        max_tokens: int,
        system: Any,
        messages: Any,
        output_format: Any,
    ) -> Any: ...


class AnthropicClient(Protocol):
    """The slice of `anthropic.AsyncAnthropic` this adapter uses."""

    # A property, not a plain attribute: a Protocol attribute must be
    # settable, and the SDK exposes this read-only.
    @property
    def messages(self) -> Messages: ...


class AnthropicExtractor:
    """Extract through the Anthropic API.

    `messages.parse` takes the compiled model directly and constrains the
    response against it, returning an already validated instance.
    """

    def __init__(self, client: AnthropicClient, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    async def extract(self, document: str, model: type[ModelT]) -> ModelT:
        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=MAX_TOKENS,
                # Instructions go in `system`, not in the conversation. That is
                # both where they belong and what lets the prefix cache across
                # requests, since only the document below it changes.
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": document}],
                output_format=model,
            )
        except Exception as error:
            raise ExtractionError(
                f"Could not reach Anthropic for model {self._model!r}: {error}"
            ) from error

        # A refusal arrives as an ordinary successful response, so it has to be
        # checked before the content is read.
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            raise ExtractionError(
                f"Model {self._model!r} refused to process this document. "
                f"({details or 'no reason given'})"
            )

        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise ExtractionError(f"Model {self._model!r} returned no parsed output.")
        # The SDK returns an instance of the model it was given, but says so
        # only through an untyped attribute.
        return cast(ModelT, parsed)
