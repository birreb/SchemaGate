from dataclasses import dataclass
from typing import Any

# Asked of the provider first. This is only what to show when that cannot be
# done, typically because no key has been entered yet, and it is deliberately
# ordered with the default first.
#
# Anthropic has one because its names are documented and change slowly. OpenAI
# has none on purpose: its names change often enough that an offline guess would
# offer models that may not exist, and a dropdown of wrong names is worse than
# an empty one that explains itself.
ANTHROPIC_KNOWN: tuple[str, ...] = (
    "claude-opus-5",
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
)

FALLBACKS: dict[str, tuple[str, ...]] = {
    "anthropic": ANTHROPIC_KNOWN,
    "openai": (),
    "openai_compatible": (),
    "ollama": (),
}


@dataclass(frozen=True, slots=True)
class ModelListing:
    """What to offer, and whether the provider actually said so."""

    models: tuple[str, ...]
    source: str
    detail: str | None = None


async def list_models(provider: str, client: Any) -> ModelListing:
    """Ask a provider which models it will accept.

    Asking beats a hardcoded list: it cannot go stale, it reflects what this
    key is entitled to, and for a local runtime it shows what has actually been
    pulled rather than what exists somewhere.
    """
    if provider not in FALLBACKS:
        raise ValueError(
            f"Unknown provider {provider!r}. Choose anthropic, openai, openai_compatible or ollama."
        )

    try:
        return ModelListing(models=await _ask(provider, client), source="provider")
    except Exception as error:
        return ModelListing(
            models=FALLBACKS[provider],
            source="fallback",
            detail=f"Could not list models from the provider: {error}",
        )


async def _ask(provider: str, client: Any) -> tuple[str, ...]:
    if provider == "ollama":
        listing = await client.list()
        return tuple(entry.model for entry in listing.models)

    page = await client.models.list()
    return tuple(entry.id for entry in page.data)
