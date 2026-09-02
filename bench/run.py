"""Ingestion benchmark: a document and a live PostgreSQL database, four ways.

The question is not which model reads an invoice best. Every approach here uses
the same model. The question is what happens between the document and the
table: which rows insert, which land in the right column, and which wrong
values are noticed.

Approaches under test, each given the same model, system prompt and per-table
instructions:

    whole_schema      The DDL of the whole database and the document itself
                      (PDF as a PDF where the provider takes one, otherwise
                      its pages as images, or its text for a text-only model;
                      a photo as an image; a spreadsheet as CSV text). The
                      model is asked for JSON rows. This is what most people
                      build first.
    whole_schema_sql  The same input, asked for INSERT statements, which are
                      then executed.
    one_table_text    The target table's DDL only and the document's text
                      layer, asked for JSON rows without constrained output.
                      Isolates the effect of narrowing the schema from the
                      effect of constrained decoding and the gate.
    schemagate        The pipeline in this repository: the table read from
                      pg_catalog, the text layer, constrained output, and the
                      validation gate.

Every result is inserted into the real table inside a transaction that is
rolled back, so PostgreSQL itself decides what is insertable. What was
inserted is then compared cell by cell with what the document says.

A model is named as provider:model, and any provider the pipeline supports is
a row in the same table:

    uv run python bench/run.py run --models anthropic:claude-haiku-4-5 --limit 5
    uv run python bench/run.py run --models cerebras:gpt-oss-120b together:Qwen/Qwen3.5-9B
    uv run python bench/run.py run --models ollama:qwen3.5:4b
    uv run python bench/run.py report

Keys are read from the environment or `.env`: ANTHROPIC_API_KEY (and
ANTHROPIC_WORKSPACE_ID for a key that acts in several workspaces),
CEREBRAS_API_KEY, GROQ_API_KEY, TOGETHER_API_KEY, FIREWORKS_API_KEY,
DEEPINFRA_API_KEY, OPENAI_API_KEY. Ollama needs none.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import datetime as dt
import io
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import asyncpg

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
sys.path.insert(0, str(REPO / "src"))

from schemagate.api.serialize import to_json_row  # noqa: E402
from schemagate.db.introspect import introspect  # noqa: E402
from schemagate.extract.base import SYSTEM_PROMPT, Extractor, Usage  # noqa: E402
from schemagate.extract.cost import Price, tally  # noqa: E402
from schemagate.ingest.images import normalise  # noqa: E402
from schemagate.ingest.pdf import read_pdf  # noqa: E402
from schemagate.ingest.router import FileKind, detect_kind  # noqa: E402
from schemagate.ingest.tabular import read_csv, read_spreadsheet  # noqa: E402
from schemagate.pipeline import process  # noqa: E402
from schemagate.schema.spec import TableSchema  # noqa: E402
from schemagate.validate.rules import parse_rule  # noqa: E402

DEFAULT_DSN = "postgresql://bench:bench@localhost:55433/erp"
DATA = ROOT / "data"
# A different directory for an audit run, so published results stay as they were.
RESULTS = Path(os.environ.get("BENCH_RESULTS", ROOT / "results"))
CONDITIONS = ("whole_schema", "whole_schema_sql", "one_table_text", "schemagate")

# Room for a large file's worth of rows, so the naive approaches are not cut
# short by this rather than by the model's own limit.
BASELINE_MAX_TOKENS = 60000

JSON_ASK = (
    "Return a JSON array with one object per row for the target table. Keys are the "
    "column names. Leave out columns the database fills in itself, such as identity "
    "columns and timestamps with defaults. Use null for a value the document does "
    "not give. Dates as YYYY-MM-DD. Numbers as JSON numbers with a dot as the decimal "
    "separator and no thousands separator, unit or currency. Return only the JSON, with no "
    "prose and no code fence."
)
SQL_ASK = (
    "Return SQL INSERT statements for the target table, one statement per row, each "
    "ending in a semicolon. Leave out columns the database fills in itself, such as "
    "identity columns and timestamps with defaults. Use NULL for a value the document "
    "does not give. Dates as YYYY-MM-DD and numbers with a dot as the decimal separator. "
    "Return only the SQL, with no prose and no code fence."
)


# --- providers -------------------------------------------------------------


@dataclass(frozen=True)
class Provider:
    """One place a model can be called.

    `pdf` is how the whole-schema approach hands over a PDF: the file itself
    where the API accepts one, its pages as images where the model sees images,
    or its text layer for a text-only model. `vision` says whether a photo can
    be sent at all; a provider that cannot take one skips the receipt cases and
    the report says so.
    """

    name: str
    kind: str  # anthropic, openai, ollama
    key_env: str | None = None
    base_url: str | None = None
    pdf: str = "text"
    vision: bool = False


PROVIDERS = {
    "anthropic": Provider("anthropic", "anthropic", "ANTHROPIC_API_KEY", pdf="native", vision=True),
    "openai": Provider("openai", "openai", "OPENAI_API_KEY", pdf="native", vision=True),
    "cerebras": Provider("cerebras", "openai", "CEREBRAS_API_KEY", "https://api.cerebras.ai/v1"),
    "groq": Provider("groq", "openai", "GROQ_API_KEY", "https://api.groq.com/openai/v1"),
    "together": Provider(
        "together",
        "openai",
        "TOGETHER_API_KEY",
        "https://api.together.xyz/v1",
        pdf="images",
        vision=True,
    ),
    "fireworks": Provider(
        "fireworks",
        "openai",
        "FIREWORKS_API_KEY",
        "https://api.fireworks.ai/inference/v1",
        pdf="images",
        vision=True,
    ),
    "deepinfra": Provider(
        "deepinfra",
        "openai",
        "DEEPINFRA_API_KEY",
        "https://api.deepinfra.com/v1/openai",
        pdf="images",
        vision=True,
    ),
    "ollama": Provider(
        "ollama", "ollama", None, "http://localhost:11434/v1", pdf="images", vision=True
    ),
}

# Models that read text only, whatever the provider. A photo cannot be sent to
# them and a PDF goes as its text layer.
TEXT_ONLY = ("gpt-oss", "llama-3.3", "llama-3.1", "deepseek", "kimi", "glm-4.7")


@dataclass(frozen=True)
class ModelSpec:
    provider: Provider
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider.name}:{self.model}"

    @property
    def slug(self) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", self.label)

    @property
    def text_only(self) -> bool:
        lowered = self.model.lower()
        return any(word in lowered for word in TEXT_ONLY) or not self.provider.vision

    @property
    def pdf_mode(self) -> str:
        return "text" if self.text_only else self.provider.pdf


def parse_spec(text: str) -> ModelSpec:
    if ":" not in text:
        if text.startswith("claude"):
            return ModelSpec(PROVIDERS["anthropic"], text)
        raise SystemExit(f"Name the provider, as provider:model. Got {text!r}.")
    provider, model = text.split(":", 1)
    if provider not in PROVIDERS:
        raise SystemExit(f"Unknown provider {provider!r}. Choose from {', '.join(PROVIDERS)}.")
    return ModelSpec(PROVIDERS[provider], model)


def load_prices() -> dict[str, Price]:
    raw = json.loads((ROOT / "prices.json").read_text(encoding="utf-8"))
    prices = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        prices[key] = Price(
            input=Decimal(str(value["input"])),
            output=Decimal(str(value["output"])),
            cached_input=Decimal(str(value["cached_input"])) if "cached_input" in value else None,
        )
    return prices


def load_env() -> None:
    """Read the repository's .env without a dependency, mapping the key's name."""
    path = REPO / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip("'\""))
    if "CLAUDE_API" in os.environ:
        os.environ.setdefault("ANTHROPIC_API_KEY", os.environ["CLAUDE_API"])


def api_key(provider: Provider) -> str | None:
    if provider.key_env is None:
        return "ollama"
    key = os.environ.get(provider.key_env)
    if not key:
        raise SystemExit(f"{provider.key_env} is not set, and {provider.name} needs it.")
    return key


# --- schema text -----------------------------------------------------------


def full_ddl() -> str:
    """The whole database as DDL, as a person would paste it into a prompt.

    The hand-written schema file rather than pg_dump output: it is a third of
    the size, which is the generous reading for the approach that sends it.
    """
    text = (ROOT / "schema.sql").read_text(encoding="utf-8")
    body = text.split("BEGIN;", 1)[1].rsplit("COMMIT;", 1)[0]
    body = re.sub(r"INSERT INTO .*?;\n", "", body, flags=re.S)
    return body.strip()


def table_ddl(table: str, ddl: str) -> str:
    """One table's CREATE TABLE, its comments, and any enum types it uses."""
    match = re.search(rf"CREATE TABLE {table} \((.*?)\);", ddl, flags=re.S)
    if not match:
        raise ValueError(table)
    create = match.group(0)
    comments = "\n".join(re.findall(rf"COMMENT ON COLUMN {table}\..*?;", ddl))
    types = []
    for enum in re.findall(r"CREATE TYPE (\w+) AS ENUM \(.*?\);", ddl):
        if re.search(rf"\b{enum}\b", create):
            types.append(re.search(rf"CREATE TYPE {enum} AS ENUM \(.*?\);", ddl).group(0))
    return "\n".join([*types, create, comments])


# --- documents -------------------------------------------------------------


class UnsupportedInputError(Exception):
    """The model cannot take this kind of input. Recorded, not scored."""


def as_csv_text(data: bytes, kind: FileKind) -> str:
    table = read_csv(data) if kind is FileKind.CSV else read_spreadsheet(data)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(table.headers)
    writer.writerows(table.rows)
    return buffer.getvalue()


def pdf_page_images(data: bytes) -> list[bytes]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    images = []
    for page in document:
        buffer = io.BytesIO()
        page.render(scale=1.5).to_pil().save(buffer, format="PNG")
        images.append(buffer.getvalue())
    return images


def b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": f"<document>\n{text}\n</document>"}


def document_parts(
    data: bytes, filename: str, spec: ModelSpec, mode: str
) -> tuple[list[dict[str, Any]], str]:
    """The document as one provider's API takes it, and how it was sent.

    `mode` is "native" for the whole-schema approaches, which send the file
    itself where they can, and "text" for the one-table approach, which sends
    what the pipeline reads.
    """
    kind = detect_kind(data, filename)
    api = spec.provider.kind

    if kind in {FileKind.CSV, FileKind.SPREADSHEET}:
        return [text_block(as_csv_text(data, kind))], "csv"

    if kind is FileKind.IMAGE:
        if spec.text_only:
            raise UnsupportedInputError("images")
        image = normalise(data)
        if api == "anthropic":
            return [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.media_type,
                        "data": b64(image.data),
                    },
                }
            ], "image"
        return [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image.media_type};base64,{b64(image.data)}"},
            }
        ], "image"

    # A PDF.
    how = spec.pdf_mode if mode == "native" else "text"
    if how == "text":
        parsed = read_pdf(data, allow_ocr=True)
        return [text_block(parsed.markdown)], "pdf_text"
    if how == "native":
        if api == "anthropic":
            return [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": b64(data),
                    },
                }
            ], "pdf"
        return [
            {
                "type": "file",
                "file": {
                    "filename": filename,
                    "file_data": f"data:application/pdf;base64,{b64(data)}",
                },
            }
        ], "pdf"
    parts = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64(page)}"}}
        for page in pdf_page_images(data)
    ]
    return parts, "pdf_images"


def strip_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json|sql)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_rows(text: str) -> list[dict[str, Any]]:
    body = strip_fence(text)
    start = body.find("[")
    if start > 0:
        body = body[start:]
    value = json.loads(body)
    if isinstance(value, dict):
        inner = next((v for v in value.values() if isinstance(v, list)), None)
        value = inner if inner is not None else [value]
    if not isinstance(value, list):
        raise ValueError("not a JSON array")
    return [row for row in value if isinstance(row, dict)]


def split_sql(text: str) -> list[str]:
    body = strip_fence(text)
    statements = []
    for statement in re.split(r";\s*\n|;\s*$", body):
        statement = statement.strip()
        if statement.upper().startswith("INSERT"):
            statements.append(statement)
    return statements


# --- clients ---------------------------------------------------------------


def reasoning_options(spec: ModelSpec) -> dict[str, Any]:
    """Turn thinking down or off, the way the pipeline does for its own calls.

    Extraction against a schema has nothing to reason about, and a model left
    to think at its default costs several times the output tokens for the same
    rows. Each family spells the switch differently.
    """
    lowered = spec.model.lower()
    if spec.provider.kind == "anthropic":
        return {} if "haiku" in lowered else {"output_config": {"effort": "low"}}
    if "gpt-oss" in lowered or lowered.startswith("gpt-5") or lowered.startswith("o"):
        return {"reasoning_effort": "low"}
    if "qwen3" in lowered:
        return {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    return {}


class InjectingCompletions:
    """The SDK's completions, with the reasoning options added to every call.

    The pipeline's OpenAI adapter takes no such options, so the benchmark adds
    them here, and every approach then runs the model in the same mode.
    """

    def __init__(self, inner: Any, extra: dict[str, Any]) -> None:
        self._inner = inner
        self._extra = extra

    async def parse(self, **kwargs: Any) -> Any:
        return await self._inner.parse(**kwargs, **self._extra)

    async def create(self, **kwargs: Any) -> Any:
        return await self._inner.create(**kwargs, **self._extra)


class InjectingChat:
    def __init__(self, inner: Any, extra: dict[str, Any]) -> None:
        self.completions = InjectingCompletions(inner.completions, extra)


class InjectingClient:
    def __init__(self, inner: Any, extra: dict[str, Any]) -> None:
        self.chat = InjectingChat(inner.chat, extra)


@dataclass
class Clients:
    """Everything one model needs: a raw client for the naive approaches and
    the pipeline's extractor for the fourth."""

    spec: ModelSpec
    raw: Any
    extractor: Extractor
    prices: dict[str, Price]


def build_clients(spec: ModelSpec, prices: dict[str, Price]) -> Clients:
    provider = spec.provider
    # The pipeline prices by model name, the benchmark by provider and model.
    model_prices = {spec.model: prices[spec.label]} if spec.label in prices else {}

    if provider.kind == "anthropic":
        import anthropic

        from schemagate.extract.anthropic import AnthropicExtractor

        headers = {}
        if os.environ.get("ANTHROPIC_WORKSPACE_ID"):
            headers["anthropic-workspace-id"] = os.environ["ANTHROPIC_WORKSPACE_ID"]
        client = anthropic.AsyncAnthropic(
            api_key=api_key(provider), timeout=240, max_retries=5, default_headers=headers
        )
        effort = reasoning_options(spec).get("output_config", {}).get("effort")
        return Clients(
            spec, client, AnthropicExtractor(client, model=spec.model, effort=effort), model_prices
        )

    import openai

    from schemagate.extract.openai import OpenAIExtractor

    client = openai.AsyncOpenAI(
        api_key=api_key(provider), base_url=provider.base_url, timeout=240, max_retries=5
    )
    extra = reasoning_options(spec)

    if provider.kind == "ollama":
        from schemagate.extract.factory import make_extractor

        extractor = make_extractor(
            "ollama",
            model=spec.model,
            ollama_host=provider.base_url.removesuffix("/v1"),
            timeout=600,
        )
        return Clients(spec, client, extractor, model_prices)

    extractor = OpenAIExtractor(InjectingClient(client, extra), model=spec.model)
    return Clients(spec, client, extractor, model_prices)


async def ask_plain(
    clients: Clients, parts: list[dict[str, Any]], question: str
) -> tuple[str, Usage, bool]:
    """One unconstrained call, the way the naive approaches make it."""
    spec = clients.spec
    extra = reasoning_options(spec)
    if spec.provider.kind == "anthropic":
        response = await clients.raw.messages.create(
            model=spec.model,
            max_tokens=BASELINE_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [*parts, {"type": "text", "text": question}]}],
            **extra,
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        return (
            text,
            Usage(
                model=spec.model,
                input_tokens=(usage.input_tokens or 0)
                + (getattr(usage, "cache_creation_input_tokens", 0) or 0),
                output_tokens=usage.output_tokens or 0,
                cached_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            ),
            response.stop_reason == "max_tokens",
        )

    completion = await clients.raw.chat.completions.create(
        model=spec.model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [*parts, {"type": "text", "text": question}]},
        ],
        **extra,
    )
    choice = completion.choices[0]
    text = choice.message.content or ""
    usage = completion.usage
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    details = getattr(usage, "prompt_tokens_details", None)
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    return (
        text,
        Usage(
            model=spec.model,
            input_tokens=max(prompt - cached, 0),
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cached_input_tokens=cached,
        ),
        choice.finish_reason == "length",
    )


# --- the four approaches ---------------------------------------------------


@dataclass
class Case:
    id: str
    file: str
    kind: str
    table: str
    expected: list[dict[str, Any]]
    expected_flags: int = 0
    instructions: str | None = None
    rules: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""


@dataclass
class Outcome:
    """What one approach produced for one document, before the database sees it."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    sql: list[str] = field(default_factory=list)
    flagged: set[tuple[int, str]] = field(default_factory=set)
    flag_rules: list[str] = field(default_factory=list)
    usage: list[Usage] = field(default_factory=list)
    ms: int = 0
    error: str = ""
    truncated: bool = False
    sent_as: str = ""
    constrained: bool | None = None
    raw: str = ""
    failures: list[str] = field(default_factory=list)


def question(schema_text: str, table: str, instructions: str | None, ask: str) -> str:
    parts = [f"<schema>\n{schema_text}\n</schema>", f"Target table: public.{table}."]
    if instructions:
        parts.append(instructions)
    parts.append(ask)
    return "\n\n".join(parts)


async def run_baseline(
    clients: Clients, case: Case, data: bytes, ddl: str, condition: str
) -> Outcome:
    started = time.perf_counter()
    outcome = Outcome()
    try:
        if condition == "one_table_text":
            parts, sent = document_parts(data, case.file, clients.spec, "text")
            schema_text = table_ddl(case.table, ddl)
            ask = JSON_ASK
        else:
            parts, sent = document_parts(data, case.file, clients.spec, "native")
            schema_text = ddl
            ask = SQL_ASK if condition == "whole_schema_sql" else JSON_ASK
        outcome.sent_as = sent
        text, usage, truncated = await ask_plain(
            clients, parts, question(schema_text, case.table, case.instructions, ask)
        )
        outcome.usage.append(usage)
        outcome.truncated = truncated
        outcome.raw = text[:20000]
        if condition == "whole_schema_sql":
            outcome.sql = split_sql(text)
        else:
            outcome.rows = parse_rows(text)
    except UnsupportedInputError as what:
        outcome.error = f"unsupported: {what}"
    except Exception as error:
        outcome.error = f"{type(error).__name__}: {str(error)[:300]}"
    outcome.ms = int((time.perf_counter() - started) * 1000)
    return outcome


async def run_schemagate(clients: Clients, case: Case, data: bytes, schema: TableSchema) -> Outcome:
    started = time.perf_counter()
    outcome = Outcome()
    if case.kind == "receipt" and clients.spec.text_only:
        outcome.error = "unsupported: images"
        return outcome
    try:
        extraction = await process(
            data,
            case.file,
            schema,
            extractor=clients.extractor,
            rules=[parse_rule(rule) for rule in case.rules],
            instructions=case.instructions,
            prices=clients.prices,
        )
        outcome.rows = [to_json_row(row) for row in extraction.rows]
        outcome.flagged = {(f.row, f.column) for f in extraction.failures if f.column}
        # An arithmetic failure names the column the rule equals, but any of
        # the rule's columns may be the wrong one, and the row is flagged for
        # a person either way. Every column the rule touches counts as flagged.
        for failure in extraction.failures:
            if failure.rule != "arithmetic":
                continue
            for rule in case.rules:
                if rule.get("equals") == failure.column:
                    operands = rule.get("terms") or rule.get("factors") or []
                    outcome.flagged.update((failure.row, c) for c in (*operands, rule["equals"]))
        outcome.flag_rules = [f.rule for f in extraction.failures]
        outcome.failures = [
            f"row {f.row} {f.column or '*'} {f.rule}: {f.detail}" for f in extraction.failures
        ]
        outcome.usage = list(extraction.spend.by_model)
        outcome.sent_as = extraction.route.value
        stages = [asdict(s) for s in extraction.stages]
        outcome.constrained = not any("free JSON" in s["detail"] for s in stages)
        outcome.raw = json.dumps(stages)
    except Exception as error:
        outcome.error = f"{type(error).__name__}: {str(error)[:300]}"
    outcome.ms = int((time.perf_counter() - started) * 1000)
    return outcome


# --- the database decides --------------------------------------------------


@dataclass
class Landed:
    """What the database accepted, row by row, and why it refused the rest."""

    inserted: list[dict[str, Any] | None] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    phantom_columns: int = 0


def error_class(error: Exception) -> str:
    text = str(error)
    if "enum" in text:
        return "enum"
    if "GENERATED ALWAYS" in text or "identity" in text:
        return "identity"
    if isinstance(error, asyncpg.NotNullViolationError):
        return "not_null"
    if isinstance(error, asyncpg.StringDataRightTruncationError):
        return "length"
    if isinstance(error, asyncpg.UniqueViolationError):
        return "duplicate"
    if isinstance(error, asyncpg.CheckViolationError):
        return "check"
    if isinstance(error, asyncpg.DataError | asyncpg.InvalidTextRepresentationError):
        return "type"
    if isinstance(error, asyncpg.PostgresSyntaxError):
        return "syntax"
    if isinstance(error, asyncpg.UndefinedColumnError):
        return "unknown_column"
    if isinstance(error, asyncpg.UndefinedTableError):
        return "unknown_table"
    return type(error).__name__


async def land_rows(
    connection: asyncpg.Connection, table: str, columns: dict[str, str], rows: list[dict[str, Any]]
) -> Landed:
    """Insert each row on its own savepoint and read back what was stored."""
    landed = Landed()
    for row in rows:
        # A null is left out rather than sent, so a column with a default
        # takes it, which is how an INSERT would be written by hand.
        landed.phantom_columns += sum(1 for key in row if key not in columns)
        keys = [key for key in row if key in columns and row[key] is not None]
        if not keys:
            landed.inserted.append(None)
            landed.errors.append("empty")
            continue
        payload = json.dumps({key: row[key] for key in keys}, default=str)
        names = ", ".join(f'"{key}"' for key in keys)
        sql = (
            f"INSERT INTO public.{table} ({names}) "
            f"SELECT {names} FROM json_populate_record(NULL::public.{table}, $1::json) RETURNING *"
        )
        transaction = connection.transaction()
        await transaction.start()
        try:
            record = await connection.fetchrow(sql, payload)
            await transaction.commit()
            landed.inserted.append(dict(record) if record else None)
        except Exception as error:
            await transaction.rollback()
            landed.inserted.append(None)
            landed.errors.append(error_class(error))
    return landed


async def land_sql(connection: asyncpg.Connection, table: str, statements: list[str]) -> Landed:
    landed = Landed()
    for statement in statements:
        transaction = connection.transaction()
        await transaction.start()
        try:
            await connection.execute(statement)
            await transaction.commit()
            landed.inserted.append({})
        except Exception as error:
            await transaction.rollback()
            landed.inserted.append(None)
            landed.errors.append(error_class(error))
    records = await connection.fetch(f"SELECT * FROM public.{table} ORDER BY id")
    stored = [dict(record) for record in records]
    landed.inserted = [
        stored.pop(0) if row is not None and stored else row for row in landed.inserted
    ]
    return landed


# --- scoring ---------------------------------------------------------------


def key_of(row: dict[str, Any] | None, table: str) -> str | None:
    if row is None:
        return None
    if table == "invoice_lines":
        value = row.get("line_no")
        return str(value) if value is not None else None
    if table == "expense_claims":
        return "only"
    value = row.get("invoice_number")
    if value is None:
        return None
    return re.sub(r"[\s\-_.]", "", str(value)).casefold()


def same(stored: Any, expected: Any, column_type: str) -> bool:
    if expected is None or stored is None:
        return expected is None and stored is None
    if column_type in {"numeric", "int2", "int4", "int8"}:
        try:
            return Decimal(str(stored)) == Decimal(str(expected))
        except InvalidOperation:
            return False
    if column_type == "date":
        stored_text = stored.isoformat() if isinstance(stored, dt.date) else str(stored)
        return stored_text[:10] == str(expected)[:10]
    return (
        re.sub(r"\s+", " ", str(stored)).strip().casefold()
        == re.sub(r"\s+", " ", str(expected)).strip().casefold()
    )


@dataclass
class Score:
    case: str
    kind: str
    table: str
    model: str
    condition: str
    cells: int = 0
    correct: int = 0
    wrong_silent: int = 0
    # Stored NULL where the document carried a value, and nothing flagged it.
    # Counted apart from a different value: a blank is found on review, a
    # plausible wrong number is not.
    wrong_null: int = 0
    wrong_flagged: int = 0
    # Cells of a row the approach itself flagged and that then could not be
    # inserted. The row is not lost: it comes back with the failing cell and
    # the rule named. Counted apart from a database rejection, where the
    # caller gets only an error.
    held: int = 0
    rejected: int = 0
    missing: int = 0
    rows_expected: int = 0
    rows_returned: int = 0
    rows_inserted: int = 0
    rows_rejected: int = 0
    rows_extra: int = 0
    phantom_columns: int = 0
    insert_errors: dict[str, int] = field(default_factory=dict)
    flags: int = 0
    flags_expected: int = 0
    inconsistency_caught: bool | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: str | None = None
    ms: int = 0
    error: str = ""
    truncated: bool = False
    sent_as: str = ""
    constrained: bool | None = None
    wrong_examples: list[str] = field(default_factory=list)

    @property
    def unsupported(self) -> bool:
        return self.error.startswith("unsupported")


def score(
    case: Case,
    model: str,
    condition: str,
    outcome: Outcome,
    landed: Landed | None,
    schema: TableSchema,
    prices: dict[str, Price],
) -> Score:
    types = {column.name: column.data_type for column in schema.columns}
    result = Score(case=case.id, kind=case.kind, table=case.table, model=model, condition=condition)
    result.rows_expected = len(case.expected)
    result.flags_expected = case.expected_flags
    result.ms = outcome.ms
    result.error = outcome.error
    result.truncated = outcome.truncated
    result.sent_as = outcome.sent_as
    result.constrained = outcome.constrained
    spend = tally(outcome.usage, prices)
    result.input_tokens = spend.input_tokens + spend.cached_input_tokens
    result.output_tokens = spend.output_tokens
    result.cost_usd = None if spend.cost_usd is None else str(spend.cost_usd)
    result.flags = len(outcome.flagged) + sum(
        1 for rule in outcome.flag_rules if rule == "arithmetic"
    )
    if case.expected_flags:
        result.inconsistency_caught = "arithmetic" in outcome.flag_rules

    returned = outcome.rows if outcome.rows else [{} for _ in outcome.sql]
    result.rows_returned = len(returned)
    if landed is None:
        result.cells = sum(len(row) for row in case.expected)
        result.missing = result.cells
        return result

    result.phantom_columns = landed.phantom_columns
    result.insert_errors = dict(Counter(landed.errors))
    result.rows_inserted = sum(1 for row in landed.inserted if row is not None)
    result.rows_rejected = sum(1 for row in landed.inserted if row is None)

    by_key: dict[str, tuple[int, dict[str, Any] | None]] = {}
    for index, stored in enumerate(landed.inserted):
        source = outcome.rows[index] if index < len(outcome.rows) else stored
        key = key_of(stored if stored is not None else source, case.table)
        if key is not None and key not in by_key:
            by_key[key] = (index, stored)
    matched = 0
    for expected in case.expected:
        key = key_of(expected, case.table)
        found = by_key.pop(key, None) if key is not None else None
        if found is None:
            result.cells += len(expected)
            result.missing += len(expected)
            continue
        matched += 1
        index, stored = found
        row_flagged = any(flagged_row == index for flagged_row, _ in outcome.flagged)
        for column, wanted in expected.items():
            result.cells += 1
            if stored is None:
                if (index, column) in outcome.flagged:
                    result.wrong_flagged += 1
                elif row_flagged:
                    result.held += 1
                else:
                    result.rejected += 1
                continue
            if same(stored.get(column), wanted, types.get(column, "text")):
                result.correct += 1
            elif (index, column) in outcome.flagged:
                result.wrong_flagged += 1
            elif stored.get(column) is None:
                result.wrong_null += 1
            else:
                result.wrong_silent += 1
                if len(result.wrong_examples) < 6:
                    result.wrong_examples.append(
                        f"{column}: wanted {wanted!r}, stored {stored.get(column)!r}"
                    )
    result.rows_extra = max(0, len(landed.inserted) - matched)
    return result


# --- runner ----------------------------------------------------------------


def result_path(spec: ModelSpec, condition: str, case_id: str) -> Path:
    return RESULTS / "raw" / spec.slug / condition / f"{case_id}.json"


async def run_one(
    connection: asyncpg.Connection,
    clients: Clients,
    condition: str,
    case: Case,
    schema: TableSchema,
    ddl: str,
) -> Score:
    data = (DATA / case.file).read_bytes()
    columns = {column.name: column.data_type for column in schema.columns}

    if condition == "schemagate":
        outcome = await run_schemagate(clients, case, data, schema)
    else:
        outcome = await run_baseline(clients, case, data, ddl, condition)

    landed: Landed | None = None
    if not outcome.error:
        outer = connection.transaction()
        await outer.start()
        try:
            if condition == "whole_schema_sql":
                landed = await land_sql(connection, case.table, outcome.sql)
            else:
                landed = await land_rows(connection, case.table, columns, outcome.rows)
        finally:
            await outer.rollback()

    scored = score(case, clients.spec.label, condition, outcome, landed, schema, clients.prices)
    path = result_path(clients.spec, condition, case.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "score": asdict(scored),
                "raw": outcome.raw[:6000],
                "sql": outcome.sql[:3],
                "rows": outcome.rows[:80],
                "failures": outcome.failures,
            },
            ensure_ascii=False,
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    return scored


def load_cases() -> list[Case]:
    lines = (DATA / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    return [Case(**json.loads(line)) for line in lines if line.strip()]


async def perfect_run(
    connection: asyncpg.Connection, cases: list[Case], schemas: dict[str, TableSchema]
) -> None:
    """Feed the expected rows straight to the database.

    Proves the ground truth is insertable and scores 100%, so a miss in a real
    run is the approach's and not the benchmark's.
    """
    total = 0
    bad = 0
    for case in cases:
        outer = connection.transaction()
        await outer.start()
        try:
            columns = {c.name: c.data_type for c in schemas[case.table].columns}
            landed = await land_rows(connection, case.table, columns, case.expected)
        finally:
            await outer.rollback()
        scored = score(
            case, "none", "perfect", Outcome(rows=case.expected), landed, schemas[case.table], {}
        )
        total += scored.cells
        if scored.correct != scored.cells:
            bad += 1
            print(
                case.id,
                scored.correct,
                "/",
                scored.cells,
                scored.insert_errors,
                scored.wrong_examples,
            )
    print(f"perfect run: {len(cases)} cases, {total} cells, {bad} cases not fully correct")


async def main_run(args: argparse.Namespace) -> None:
    load_env()
    cases = load_cases()
    if args.kinds:
        cases = [case for case in cases if case.kind in args.kinds]
    if args.cases:
        cases = [case for case in cases if case.id in args.cases]
    if args.limit:
        cases = cases[: args.limit]

    connection = await asyncpg.connect(args.dsn)
    try:
        schemas = {
            table: await introspect(connection, "public", table)
            for table in sorted({case.table for case in cases})
        }
        if args.perfect:
            await perfect_run(connection, cases, schemas)
            return

        ddl = full_ddl()
        prices = load_prices()
        spent = Decimal(0)
        for text in args.models:
            spec = parse_spec(text)
            clients = build_clients(spec, prices)
            for case in cases:
                if case.kind == "tabular" and len(case.expected) > args.max_tabular_rows:
                    conditions = [c for c in args.conditions if c == "schemagate"]
                else:
                    conditions = list(args.conditions)
                for condition in conditions:
                    path = result_path(spec, condition, case.id)
                    if path.exists() and not args.redo:
                        continue
                    if spent >= Decimal(str(args.budget_usd)):
                        print(
                            f"Stopped: ${spent:.4f} spent, budget is ${args.budget_usd}. "
                            f"Raise --budget-usd to continue; finished cases are kept."
                        )
                        return
                    scored = await run_one(
                        connection, clients, condition, case, schemas[case.table], ddl
                    )
                    cost = Decimal(scored.cost_usd) if scored.cost_usd is not None else None
                    if cost is not None:
                        spent += cost
                    shown_cost = f"${cost:.4f}" if cost is not None else "unpriced"
                    print(
                        f"{spec.label:<28} {condition:<17} {case.id:<8} "
                        f"{scored.correct}/{scored.cells} correct, {scored.wrong_silent} wrong, "
                        f"{scored.wrong_null} blank, {scored.rows_rejected} rows rejected, "
                        f"{scored.input_tokens}+{scored.output_tokens} tok, "
                        f"{shown_cost}, {scored.ms} ms, run total ${spent:.3f}"
                        + (f"  {scored.error}" if scored.error else ""),
                        flush=True,
                    )
    finally:
        await connection.close()


# --- report ----------------------------------------------------------------


def load_scores() -> list[Score]:
    scores = []
    for path in sorted((RESULTS / "raw").glob("*/*/*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))["score"]
        scores.append(Score(**payload))
    return scores


def pct(numerator: int, denominator: int) -> str:
    return f"{100 * numerator / denominator:.1f}%" if denominator else "n/a"


def money_sum(group: list[Score]) -> str:
    if any(s.cost_usd is None for s in group):
        return "unpriced"
    return f"${sum(Decimal(s.cost_usd) for s in group):.4f}"


def report() -> str:
    scores = load_scores()
    if not scores:
        return "No results yet."
    lines = ["# Ingestion benchmark results", ""]
    groups: dict[tuple[str, str], list[Score]] = defaultdict(list)
    for s in scores:
        groups[(s.model, s.condition)].append(s)

    for model in sorted({s.model for s in scores}):
        lines.append(f"## {model}")
        lines.append("")
        lines.append("Documents that need a model (invoices, statements, line items, receipts):")
        lines.append("")
        lines.append(
            "| approach | docs | cells correct | wrong value stored | left blank | flagged "
            "| held for review | rejected by DB | missing | rows inserted | phantom cols "
            "| inconsistent docs caught | median ms | tokens in | tokens out | cost |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for condition in CONDITIONS:
            group = [
                s
                for s in groups.get((model, condition), [])
                if s.kind != "tabular" and not s.unsupported
            ]
            if not group:
                continue
            cells = sum(s.cells for s in group)
            broken = [s for s in group if s.flags_expected]
            caught = sum(1 for s in broken if s.inconsistency_caught)
            lines.append(
                f"| {condition} | {len(group)} | {pct(sum(s.correct for s in group), cells)} "
                f"| {sum(s.wrong_silent for s in group)} | {sum(s.wrong_null for s in group)} "
                f"| {sum(s.wrong_flagged for s in group)} | {sum(s.held for s in group)} "
                f"| {sum(s.rejected for s in group)} | {sum(s.missing for s in group)} "
                f"| {sum(s.rows_inserted for s in group)}/{sum(s.rows_expected for s in group)} "
                f"| {sum(s.phantom_columns for s in group)} "
                f"| {caught}/{len(broken)} "
                f"| {int(statistics.median(s.ms for s in group))} "
                f"| {sum(s.input_tokens for s in group)} | {sum(s.output_tokens for s in group)} "
                f"| {money_sum(group)} |"
            )
        lines.append("")
        sent = Counter((s.condition, s.sent_as) for s in scores if s.model == model and s.sent_as)
        if sent:
            lines.append(
                "How the document was sent: "
                + ", ".join(
                    f"{condition} as {how} ({count})"
                    for (condition, how), count in sorted(sent.items())
                )
            )
            lines.append("")
        free = [
            s
            for s in scores
            if s.model == model and s.condition == "schemagate" and s.constrained is False
        ]
        if free:
            lines.append(
                f"The provider returned free JSON rather than constrained output on "
                f"{len(free)} schemagate cases; the gate checked them instead."
            )
            lines.append("")
        tabular = [s for s in scores if s.model == model and s.kind == "tabular"]
        if tabular:
            lines.append("Spreadsheets and CSV files:")
            lines.append("")
            lines.append(
                "| approach | case | rows | cells correct | wrong value stored | left blank "
                "| rejected | missing | truncated | ms | tokens in | tokens out | cost |"
            )
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for s in sorted(tabular, key=lambda s: (s.case, CONDITIONS.index(s.condition))):
                lines.append(
                    f"| {s.condition} | {s.case} | {s.rows_inserted}/{s.rows_expected} "
                    f"| {pct(s.correct, s.cells)} | {s.wrong_silent} | {s.wrong_null} "
                    f"| {s.rejected} | {s.missing} | {'yes' if s.truncated else ''} "
                    f"| {s.ms} | {s.input_tokens} | {s.output_tokens} "
                    f"| {money_sum([s])} |"
                )
            lines.append("")
        skipped = [s for s in scores if s.model == model and s.unsupported]
        if skipped:
            by = Counter(s.error for s in skipped)
            lines.append(
                "Not attempted: " + ", ".join(f"{count} cases, {why}" for why, count in by.items())
            )
            lines.append("")
        errors = [s for s in scores if s.model == model and s.error and not s.unsupported]
        if errors:
            lines.append("Errors:")
            lines.append("")
            for s in errors:
                lines.append(f"- {s.condition} {s.case}: {s.error}")
            lines.append("")
        classes: dict[str, Counter[str]] = defaultdict(Counter)
        for s in scores:
            if s.model == model:
                classes[s.condition].update(s.insert_errors)
        if any(classes.values()):
            lines.append("Why the database refused rows:")
            lines.append("")
            for condition in CONDITIONS:
                if classes.get(condition):
                    lines.append(
                        f"- {condition}: "
                        + ", ".join(f"{k} {v}" for k, v in classes[condition].most_common())
                    )
            lines.append("")
        lines.append(
            "Examples of values stored wrong or left blank without anything flagging them:"
        )
        lines.append("")
        for condition in CONDITIONS:
            examples = [
                f"{s.case} {e}"
                for s in groups.get((model, condition), [])
                for e in s.wrong_examples
            ]
            if examples:
                lines.append(f"- {condition}:")
                lines.extend(f"  - {e}" for e in examples[:10])
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--dsn", default=os.environ.get("BENCH_DSN", DEFAULT_DSN))
    run.add_argument(
        "--models",
        nargs="+",
        default=["anthropic:claude-haiku-4-5"],
        help="provider:model, for example cerebras:gpt-oss-120b or ollama:qwen3.5:4b",
    )
    run.add_argument("--conditions", nargs="+", default=list(CONDITIONS))
    run.add_argument("--kinds", nargs="*")
    run.add_argument("--cases", nargs="*")
    run.add_argument("--limit", type=int, default=0)
    run.add_argument(
        "--max-tabular-rows",
        type=int,
        default=2000,
        help="Tabular files above this many rows run through schemagate only.",
    )
    run.add_argument(
        "--budget-usd",
        type=float,
        default=1.0,
        help="Stop the run once this much has been spent. Default $1. "
        "Unpriced models count as free.",
    )
    run.add_argument("--redo", action="store_true")
    run.add_argument("--perfect", action="store_true", help="Insert the expected rows and stop.")
    sub.add_parser("report")
    sub.add_parser("chart")
    args = parser.parse_args()
    if args.command == "chart":
        from chart import draw

        for path in draw():
            print(path)
        return
    if args.command == "report":
        text = report()
        RESULTS.mkdir(exist_ok=True)
        (RESULTS / "summary.md").write_text(text, encoding="utf-8")
        print(text)
        return
    asyncio.run(main_run(args))


if __name__ == "__main__":
    main()
