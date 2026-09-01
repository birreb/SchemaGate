from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import ValidationError

from schemagate.errors import ExtractionError
from schemagate.extract.base import SYSTEM_PROMPT, ModelT
from schemagate.ingest.images import NormalisedImage

DEFAULT_MODEL = "qwen3"

# A local runtime still honours these, unlike the hosted frontier models, which
# reject sampling parameters outright. The same document should extract to the
# same rows twice.
DETERMINISTIC = {"temperature": 0, "seed": 0}


class ChatClient(Protocol):
    """The slice of `ollama.AsyncClient` this adapter uses.

    Spelled out rather than left as `**kwargs`, which would claim the client
    accepts any keyword at all and would not describe the real one.
    """

    async def chat(
        self,
        *,
        model: str,
        messages: Any,
        # Shadows a builtin, and has to: this is the keyword the Ollama API
        # takes, and renaming it here would stop the protocol matching.
        format: Any,  # noqa: A002
        options: Any,
    ) -> Any: ...


class OllamaExtractor:
    """Extract through a local Ollama server.

    Ollama takes a JSON Schema in `format` and constrains generation against it
    with XGrammar, so the shape of the output is enforced while the tokens are
    chosen, inside a runtime the operator controls. That is a stronger guarantee
    than a hosted promise, and it keeps the document on the customer's network.

    Shape is not accuracy. A small model returns well-formed wrong answers more
    often than a large one, which is why the validation gate exists and why it
    was built before this.
    """

    def __init__(self, client: ChatClient, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    async def extract(
        self,
        document: str,
        model: type[ModelT],
        images: Sequence[NormalisedImage] = (),
    ) -> ModelT:
        schema = model.model_json_schema()
        # Ollama takes raw bytes on the message rather than a content block.
        user: dict[str, Any] = {"role": "user", "content": document}
        if images:
            user["images"] = [image.data for image in images]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            user,
        ]

        # Broad on purpose, and narrow in scope: this call is the boundary to
        # another process, and everything it can fail with should reach the
        # caller as one recognisable error. Nothing of ours runs inside it.
        try:
            response = await self._client.chat(
                model=self._model,
                messages=messages,
                format=schema,
                options=dict(DETERMINISTIC),
            )
        except Exception as error:
            raise ExtractionError(
                f"Could not reach the Ollama server for model {self._model!r}. "
                f"Is `ollama serve` running? ({error})"
            ) from error

        content = getattr(response.message, "content", None)
        if not content:
            raise ExtractionError(f"Model {self._model!r} returned an empty response.")

        try:
            return model.model_validate_json(content)
        except ValidationError as error:
            raise ExtractionError(
                f"Model {self._model!r} returned output that does not match "
                f"{model.__name__}: {error}"
            ) from error
