import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

from schemagate.errors import SchemaGateError
from schemagate.extract.cost import Price
from schemagate.optional import require

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

FACTORY = "schemagate.api.app:create_app"

DEFAULT_CASES = Path("evals/cases")


def main(argv: list[str] | None = None) -> int:
    """Run the service, check what is configured, or measure a model.

    `serve` wraps `uvicorn schemagate.api.app:create_app --factory`. `--host`
    defaults to loopback rather than every interface; the container image
    overrides it, since binding to the interface is its purpose.
    """
    parser = argparse.ArgumentParser(prog="schemagate", description=main.__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="run the HTTP service")
    serve.add_argument("--host", default=DEFAULT_HOST)
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--reload", action="store_true", help="restart on source changes")
    serve.add_argument("--workers", type=int, default=None)

    subcommands.add_parser("check", help="validate configuration and exit")

    measure = subcommands.add_parser(
        "evaluate", help="score a model on documents with known answers"
    )
    measure.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    measure.add_argument("--provider", default=None, help="defaults to the configured one")
    measure.add_argument("--model", default=None)
    measure.add_argument("--effort", default=None)
    measure.add_argument("--base-url", default=None)
    measure.add_argument(
        "--prices",
        default=None,
        help='JSON, for example \'{"claude-opus-5": {"input": 5, "output": 25}}\'',
    )

    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "check":
            return _check()
        if arguments.command == "evaluate":
            return _evaluate(arguments)
        return _serve(arguments)
    except SchemaGateError as error:
        print(f"schemagate: {error}", file=sys.stderr)
        return 1


def _check() -> int:
    """Read the configuration and report what it says.

    Nothing here connects to a database or a provider. It answers what is
    configured, not what is reachable.
    """
    from schemagate.config import Settings

    settings = Settings()
    print(f"connections: {', '.join(sorted(settings.connections))}")
    print(f"provider: {settings.provider or 'none, so documents needing a model are refused'}")
    print(f"effort: {settings.effort or 'not sent'}")
    priced = ", ".join(sorted(settings.prices)) or "none, so cost is reported null"
    print(f"priced models: {priced}")
    print(f"header model: {settings.header_model or 'the extraction model does both'}")
    print(f"api keys: {len(settings.api_keys) or 'none, so the endpoints are open'}")
    print(f"rate limit: {settings.rate_limit_per_minute or 'none'}")
    print(f"concurrent extractions: {settings.max_concurrent_extractions or 'unbounded'}")
    return 0


def _evaluate(arguments: argparse.Namespace) -> int:
    """Score a provider on the case files, and print accuracy beside cost.

    Exits non-zero when a case is not clean, so it can gate a model change.
    """
    from schemagate.config import Settings
    from schemagate.evaluate import evaluate, load_cases, report
    from schemagate.extract.factory import build_extractor, make_extractor

    settings = Settings()
    prices = _prices(arguments.prices) or settings.prices

    if arguments.provider:
        extractor = make_extractor(
            provider=arguments.provider,
            model=arguments.model,
            base_url=arguments.base_url or settings.openai_base_url,
            ollama_host=settings.ollama_host,
            effort=arguments.effort or settings.effort,
            timeout=settings.model_timeout_seconds,
        )
    elif settings.provider:
        extractor = build_extractor(settings, model=arguments.model)
    else:
        # The tabular cases still run and still score.
        print("No provider configured, so only the cases that need no model will run.\n")
        extractor = None

    cases = load_cases(arguments.cases)
    results = asyncio.run(evaluate(cases, extractor, prices))
    print(report(results))
    return 0 if all(result.ok for result in results) else 1


def _prices(raw: str | None) -> dict[str, Price] | None:
    if not raw:
        return None
    return {
        model: Price(
            input=Decimal(str(entry["input"])),
            output=Decimal(str(entry["output"])),
            cached_input=(Decimal(str(entry["cached_input"])) if "cached_input" in entry else None),
        )
        for model, entry in json.loads(raw).items()
    }


def _serve(arguments: argparse.Namespace) -> int:
    uvicorn = require("uvicorn")
    uvicorn.run(
        FACTORY,
        factory=True,
        host=arguments.host,
        port=arguments.port,
        reload=arguments.reload,
        workers=arguments.workers,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
