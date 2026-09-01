from typing import Any, Protocol, cast

from schemagate.errors import ExtractionError
from schemagate.extract.base import SYSTEM_PROMPT, ModelT


class Completions(Protocol):
    # Spelled out rather than **kwargs, which would claim the client takes
    # any keyword at all and would not describe the real one.
    async def parse(self, *, model: str, messages: Any, response_format: Any) -> Any: ...


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
    """Extract through the OpenAI API.

    The model name is required rather than defaulted. OpenAI's names change
    often enough that a stale default would fail at the worst moment, with an
    error about a model the operator never chose.
    """

    def __init__(self, client: OpenAIClient, model: str) -> None:
        self._client = client
        self._model = model

    async def extract(self, document: str, model: type[ModelT]) -> ModelT:
        try:
            completion = await self._client.chat.completions.parse(
                model=self._model,
                # OpenAI has no separate instructions parameter, so they lead the
                # conversation instead. The protocol hides the difference.
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": document},
                ],
                response_format=model,
            )
        except Exception as error:
            raise ExtractionError(
                f"Could not reach OpenAI for model {self._model!r}: {error}"
            ) from error

        message = completion.choices[0].message

        if getattr(message, "refusal", None):
            raise ExtractionError(
                f"Model {self._model!r} refused to process this document. ({message.refusal})"
            )

        parsed = getattr(message, "parsed", None)
        if parsed is None:
            raise ExtractionError(f"Model {self._model!r} returned no parsed output.")
        # The SDK returns an instance of the model it was given, but says so
        # only through an untyped attribute.
        return cast(ModelT, parsed)
