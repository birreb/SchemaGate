from collections.abc import Sequence
from typing import Any, Protocol, cast

from schemagate.errors import ExtractionError
from schemagate.extract.base import (
    SYSTEM_PROMPT,
    Extracted,
    ModelT,
    Usage,
    counted,
    encoded,
)
from schemagate.ingest.images import NormalisedImage

DEFAULT_MODEL = "claude-opus-5"

# Enough room for a long document's worth of rows without inviting a timeout.
MAX_TOKENS = 16000

# The current models think by default at high effort. Extraction against a
# compiled schema has a fixed answer shape and nothing to reason about.
DEFAULT_EFFORT = "low"


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
        output_config: Any = ...,
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

    def __init__(
        self,
        client: AnthropicClient,
        model: str = DEFAULT_MODEL,
        effort: str | None = DEFAULT_EFFORT,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = effort

    async def extract(
        self,
        document: str,
        model: type[ModelT],
        images: Sequence[NormalisedImage] = (),
    ) -> Extracted[ModelT]:
        # Sent only when set. An older model rejects the field outright, and an
        # operator who has pinned one should be able to turn it off rather than
        # be told their model is unsupported.
        options: dict[str, Any] = {}
        if self._effort:
            options["output_config"] = {"effort": self._effort}

        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=MAX_TOKENS,
                # Instructions go in `system`, not in the conversation, so
                # the per-request text sits below them.
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _content(document, images)}],
                output_format=model,
                **options,
            )
        except Exception as error:
            raise ExtractionError(
                f"Could not reach Anthropic for model {self._model!r}: {error}"
            ) from error

        # A refusal arrives as an ordinary successful response, so it has to be
        # checked before the content is read.
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            raise ExtractionError(
                f"Model {self._model!r} refused to process this document. "
                f"({details or 'no reason given'})"
            )

        # Also an ordinary successful response. The model ran out of room
        # mid-answer, so the JSON never closed and nothing parsed from it.
        # Reported separately from a genuine empty result.
        if stop == "max_tokens":
            raise ExtractionError(
                f"The document produced more rows than {MAX_TOKENS} output tokens "
                f"allow, so model {self._model!r} was cut off mid-answer. Split the "
                f"document and extract it in parts."
            )

        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise ExtractionError(f"Model {self._model!r} returned no parsed output.")
        # The SDK returns an instance of the model it was given, but says so
        # only through an untyped attribute.
        return Extracted(value=cast(ModelT, parsed), usage=_usage(response, self._model))


def _usage(response: Any, model: str) -> Usage:
    """Read the token counts off the response.

    Anthropic reports uncached input, cache writes and cache reads separately.
    The first two are billed as input, at slightly different rates, and only
    reads are discounted, so those are what get their own field.
    """
    reported = getattr(response, "usage", None)
    if reported is None:
        return Usage(model=model)
    return Usage(
        model=model,
        input_tokens=(
            counted(reported, "input_tokens") + counted(reported, "cache_creation_input_tokens")
        ),
        output_tokens=counted(reported, "output_tokens"),
        cached_input_tokens=counted(reported, "cache_read_input_tokens"),
    )


def _content(document: str, images: Sequence[NormalisedImage]) -> Any:
    """The user message, as a string when there is nothing but text.

    Images come first so the instruction below reads as being about them.
    """
    if not images:
        return document
    blocks: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image.media_type,
                "data": encoded(image),
            },
        }
        for image in images
    ]
    blocks.append({"type": "text", "text": document})
    return blocks
