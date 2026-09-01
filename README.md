# SchemaGate

[![CI](https://github.com/birreb/SchemaGate/actions/workflows/ci.yml/badge.svg)](https://github.com/birreb/SchemaGate/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%E2%80%93%203.14-blue)](https://www.python.org/downloads/)
[![Licence](https://img.shields.io/badge/licence-Apache%202.0-blue)](LICENSE)

Turn a document into rows that fit a PostgreSQL table you already own.

SchemaGate reads your table definition from the live database, compiles it into a validation
model at runtime, and constrains extraction so the output cannot disagree with your schema. It
returns JSON over HTTP and writes nothing. There is no dashboard.

```console
$ curl -s localhost:8000/v1/extract \
    -F file=@invoice.pdf \
    -F connection=primary \
    -F table=invoices
```

```json
{
  "status": "flagged",
  "table": "public.invoices",
  "route": "native_pdf",
  "rows": [
    { "invoice_number": "INV-1043", "subtotal": "1240.00", "tax": "310.00", "total": "1550.00" },
    { "invoice_number": "INV-1044", "subtotal": "890.00", "tax": "222.50", "total": "1100.00" }
  ],
  "validation": {
    "failures": [
      {
        "row": 1,
        "column": "total",
        "rule": "arithmetic",
        "detail": "subtotal + tax = total does not hold: the terms come to 1112.50, the document says 1100.00.",
        "value": "1100.00"
      }
    ]
  },
  "unmatched_headers": [],
  "missing_columns": [],
  "timings_ms": { "parse": 7, "validate": 0 }
}
```

`flagged` is not an error. Extraction worked and a check did not hold. The rows still come
back, nothing is silently repaired, and you decide what to do.

## Requirements

- A PostgreSQL database with a table to extract into. SchemaGate needs only `SELECT` on
  `pg_catalog`, which every role has, and does not write.
- Docker, or Python 3.11 or newer.
- For PDFs, a running [Ollama](https://ollama.com) server. Spreadsheets and CSVs need no model.

## Try it in one command

Starts a throwaway Postgres with an example `invoices` table and runs SchemaGate against it:

```console
$ docker compose -f examples/compose.demo.yaml up
```

Open <http://localhost:8000>, set the connection to `demo` and the table to `invoices`, and
drop in `examples/invoices-european.csv`. It is semicolon delimited with comma decimals, and
its last row does not add up, so you see the separator handling and the arithmetic check at
once.

## Quickstart against your own database

```console
$ cp .env.example .env      # then set SCHEMAGATE_CONNECTIONS
$ docker compose up
```

Open <http://localhost:8000> for a page to try it by hand, or post to `/v1/extract`. The main
compose file starts SchemaGate and nothing else, so it can never point you at a database other
than the one you configured.

Without Docker:

```console
$ uv sync --extra server
$ uv run --env-file .env uvicorn schemagate.api.app:create_app --factory
```

The service validates its configuration at startup and refuses to run without it, naming the
variable it needs. A misconfigured deployment fails immediately rather than an hour later.

## Configuration

Environment variables only. No config file.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SCHEMAGATE_CONNECTIONS__<name>` | at least one | One variable per connection, for example `SCHEMAGATE_CONNECTIONS__primary=postgresql://...`. Callers reference the name; a connection string is never accepted over HTTP. |
| `SCHEMAGATE_MAX_UPLOAD_BYTES` | `10485760` | Largest accepted upload. Enforced before the body is read. |
| `SCHEMAGATE_PROVIDER` | unset | `anthropic`, `openai`, `openai_compatible` or `ollama`. Unset, documents needing a model are refused rather than sent anywhere. |
| `SCHEMAGATE_ANTHROPIC_MODEL` | `claude-opus-5` | Model to extract with. |
| `SCHEMAGATE_OPENAI_MODEL` | unset | Required for `openai` and `openai_compatible`. Never defaulted, since OpenAI names change often. |
| `SCHEMAGATE_OPENAI_BASE_URL` | unset | For `openai_compatible`: Groq, OpenRouter, Together, DeepSeek, vLLM, LM Studio. |
| `SCHEMAGATE_OLLAMA_HOST` | `http://localhost:11434` | Where a local Ollama server is listening. |
| `SCHEMAGATE_OLLAMA_MODEL` | `qwen3` | Model to extract with. |
| `SCHEMAGATE_ALLOW_REQUEST_CREDENTIALS` | `false` | Lets a request name its own provider and key, which the playground uses. Off by default: it sends a credential over HTTP. |
| `SCHEMAGATE_RULES` | `{}` | Arithmetic checks per table, `{"public.invoices": [{"terms": ["subtotal", "tax"], "equals": "total"}]}`. |

API keys are never read by this project. Each SDK picks up its own standard variable
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`), so the credential stays out of the codebase.

Connection strings are held as secrets and never appear in a log line or an error body.

`SCHEMAGATE_RULES` is JSON because it has structure, and in an env file it must be wrapped in
single quotes. Without them `uv run --env-file` refuses to parse the line. Connections avoid
the problem entirely by being one variable each.

## What it reads

| Input | Parser | Model call |
| --- | --- | --- |
| CSV, TSV | stdlib `csv` with encoding detection | no |
| XLSX, XLS, XLSB, ODS | [calamine](https://github.com/tafia/calamine) (Rust) | no |
| PDF with a text layer | [pdf-inspector](https://github.com/firecrawl/pdf-inspector) (Rust) | yes |
| Scanned PDF, images | not yet | not yet |

Uploads are identified by content, not by filename or the declared content type. A PDF named
`statement.csv` is treated as a PDF.

Tabular files never reach a model, so they cost nothing to process. `timings_ms` in every
response shows where the time actually went.

## Column comments become extraction hints

If you document a column, the model reads it:

```sql
COMMENT ON COLUMN invoices.vat_id IS 'Seller VAT number, not the buyer';
```

That text becomes the field description in the schema sent to the model. Nothing to configure,
and it stays current because it lives in your database. Introspection runs on every request,
so a column you alter takes effect on the next upload.

## API

### `GET /v1/connections`

The configured connection names, and only the names. What each points at stays on the server.

### `GET /v1/tables?connection=<name>`

Every relation the connected role can `SELECT` from, so a caller can choose rather than type a
name and find out later whether it exists. Views and materialised views are included, labelled
with what they are.

### `POST /v1/extract`

Multipart form.

| Field | Required | Meaning |
| --- | --- | --- |
| `file` | yes | The document. |
| `connection` | yes | A name from `SCHEMAGATE_CONNECTIONS`. |
| `table` | yes | Target table. |
| `schema` | no | Postgres schema, default `public`. |
| `provider` | no | Overrides the configured provider. Requires `SCHEMAGATE_ALLOW_REQUEST_CREDENTIALS`. |
| `model`, `api_key`, `base_url` | no | Used with `provider`. The key is passed to the SDK and dropped: never stored, logged or returned. |

`200` with `status` of `ok` or `flagged`. `400` unknown connection, `404` unknown table,
`403` per-request credentials are off, `413` upload too large, `415` unsupported file type,
`422` unreadable document or an incomplete provider choice, `502` the model server failed,
`503` the database is unreachable or no model is configured.

Interactive docs at `/docs`. Liveness at `/health`.

### Numbers are strings

Exact numbers leave as JSON strings. JSON has one number type and every client parser reads it
as a float, which would throw away the exactness the rest of the pipeline preserves. Parse them
with a decimal type on your side.

## Limitations

Worth knowing before you evaluate it.

- **PostgreSQL only.** The design depends on `pg_catalog` for enum labels, length limits and
  column comments, which no portable interface exposes.
- **No writes.** SchemaGate returns JSON. Inserting is yours to do, deliberately, because a
  service that writes to your production database is a much larger thing to trust.
- **`json` and `jsonb` columns are not supported.** Strict structured output cannot express a
  free-form object.
- **Scanned documents and photographs are not supported yet.** A PDF with no text layer is
  refused rather than guessed at.
- **A twenty-digit number in a spreadsheet is already wrong before we see it.** Excel rounds
  through a float when saving, and no reader can recover the digits. SchemaGate refuses such a
  value instead of expanding it into a plausible, wrong identifier. Store long identifiers as
  text, or use CSV.
- **`1,234` is refused when nothing can resolve it.** It is either one thousand two hundred and
  thirty four or one point two three four. The column's declared scale settles it, and so does
  any unambiguous value elsewhere in the same column. With neither, guessing would be wrong by
  a factor of a thousand, so it is reported instead.
- **A local model constrained to your schema still returns wrong values sometimes.** The shape
  is guaranteed; the content is not. That is what the checks are for.

## Development

```console
$ uv sync --all-extras
$ uv run pytest
$ uv run ruff check . && uv run mypy
```

Tests that need PostgreSQL are marked `postgres` and skip unless `SCHEMAGATE_TEST_DSN` is set.
CI runs them against a real database on Python 3.11 through 3.14.

[docs/architecture.md](docs/architecture.md) records the design and the reasoning behind each
decision, including the ones that changed after being measured.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
