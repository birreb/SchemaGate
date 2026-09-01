from typing import Protocol, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)

# Static so that it caches. Anything varying per request goes in the user
# message, never here.
SYSTEM_PROMPT = (
    "You extract structured records from documents. "
    "Return only the rows the document actually contains. "
    "Copy values exactly as they appear, including their punctuation and "
    "separators, and do not reformat, round or convert numbers or dates. "
    "If a value is not present, use null rather than inventing one."
)


class Extractor(Protocol):
    """One way of turning a document into rows of a compiled model.

    The implementations disagree on method name, parameter name, result
    attribute and how a refusal is signalled, which is the whole reason this
    exists rather than branching on a provider name inside the pipeline.
    """

    async def extract(self, document: str, model: type[ModelT]) -> ModelT: ...
