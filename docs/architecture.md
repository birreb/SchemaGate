# SchemaGate architecture

Status: milestones 0 to 9 built, and it runs. A document can be posted to the endpoint and
come back as validated rows: CSV, spreadsheets, digital PDFs, scanned PDFs through local OCR,
and photographs through a vision model. Four model providers, a playground, and a container.
What is left is the optional commit path and other databases. See
[Milestones](#milestones) for detail.

Decisions in this document are recorded with the reason behind them, including the ones that
were revised after measurement. Where a note says a field or library behaves in a particular
way, that behaviour was tested rather than assumed.

SchemaGate turns a file into rows that fit a Postgres table you already own. It reads the
table definition from the live database, builds a validation model from it at runtime, and
constrains an LLM to produce output that matches. The API is the product; the page served at
the root is a playground for judging it, not a dashboard for operating it.

## Scope

In scope for the first working version:

- Read a table definition from a live Postgres connection.
- Accept an uploaded file (CSV, XLSX, PDF, image).
- Return validated JSON rows shaped like that table, plus a report of what was checked.

Explicitly out of scope for now:

- Writing rows to the database. The service returns JSON and the caller decides. An opt-in
  commit path is milestone 10, and it stays opt-in.
- Any frontend, job queue, or persistent storage of uploaded files.
- Training or hosting models.

## Pipeline

Five stages, each a plain function with typed input and output. No agent loop, no retries
driven by model judgement.

```
upload -> discover -> route -> extract -> validate -> response
             |          |         |           |
        pg_catalog   file type   LLM or   arithmetic
          query      + PDF class  none    and type checks
```

### 1. Discover

Read the table definition from `pg_catalog`, not from `information_schema`. Produce a
`TableSchema`: an ordered list of `ColumnSpec` records holding name, Postgres type,
nullability, default, description, and whether the column is generated or an identity column.

`information_schema` is the portable SQL-standard view, and portability is the one property
this project does not need, being Postgres-only. It costs real capability in exchange:

- `information_schema.columns.data_type` reports the literal string `USER-DEFINED` for every
  enum column, so enum labels require falling back to `udt_name` and querying `pg_enum` anyway.
- It exposes no check constraints.
- It exposes no column comments.
- Its views carry permission-filtering joins, which makes them measurably slower than the
  catalog tables underneath.

One query against `pg_attribute`, `pg_type`, `pg_enum`, `pg_constraint`, and `pg_attrdef`
returns all of it.

**Column comments become field descriptions.** `col_description(attrelid, attnum)` returns
whatever the developer wrote in `COMMENT ON COLUMN`. That string flows into the Pydantic
`Field(description=...)`, into the generated JSON schema, and into what the model sees when
extracting. A developer who documents a column ("Seller VAT number, not the buyer") is
steering extraction without configuring anything, from inside their own database, and the hint
updates itself when the comment changes. This is the highest-value feature in the design and
`information_schema` cannot provide it at all.

Columns are skipped by default when they are generated, identity, or have a default and are
not nullable. Those are the database's job, not the model's. A caller can override with an
explicit include list.

The DSN comes from configuration, not from the request body. Accepting a connection string
over HTTP turns the service into an SSRF primitive that can reach anything on the deploying
network. Named connections registered in config and referenced by key keep that closed.

Table and schema names from a request are passed as query parameters, never interpolated.
Because discovery runs before anything else, a table name is validated against the catalog
before it is used anywhere, which makes the catalog an allowlist by construction.

Driver: `asyncpg` for the pool. `psycopg2` is in maintenance only; `psycopg` 3 is the current
line and is the fallback if we ever need its `sql.Identifier` composition or pipeline mode.

### 2. Compile

`TableSchema` becomes a Pydantic model through `create_model`. Type mapping:

| Postgres              | Python                 | Note                                          |
| --------------------- | ---------------------- | --------------------------------------------- |
| `text`, `varchar`     | `str`                  |                                               |
| `uuid`                | `str`                  | validated by our layer, not by JSON Schema    |
| `int2` `int4` `int8`  | `int`                  |                                               |
| `numeric`, `decimal`  | `str` at the boundary  | parsed to `Decimal` after. Never `float`.     |
| `float4`, `float8`    | `float`                |                                               |
| `boolean`             | `bool`                 |                                               |
| `date`                | `str`, then `date`     |                                               |
| `timestamp(tz)`       | `str`, then `datetime` |                                               |
| enum type             | `Literal[...]`         | labels read from `pg_enum`                    |
| `T[]`                 | `list[T]`              |                                               |
| `json`, `jsonb`       | `str` holding JSON     | parsed and checked after. See below.          |

Two decisions here matter more than the rest.

**Money is never a float.** An invoice total that round-trips through IEEE 754 is a corrupted
invoice. `numeric` columns cross the model boundary as strings and become `Decimal` in the
coercion step.

**Free-form objects go through a string.** Strict structured output cannot describe an object
of unknown shape, which is the documented limitation. It can carry one inside a string, so a
`jsonb` column compiles to `str`, the model writes JSON into it, and the gate parses and
checks the result. The same move as `numeric`, for the same reason: the boundary type is
chosen to survive the contract, not to look like the destination.

**Nullable is not optional.** Strict structured output on both providers requires every
property to appear in `required` and forbids additional properties. A nullable column
therefore compiles to a nullable union (`str | None`), not an omitted field. Absence and null
are different things, and only one of them survives the API contract.

Do not lean on JSON Schema `format` for validation. Provider support for format assertions is
inconsistent and it is not a guarantee we control. Coerce and check in our own layer.

Each field carries its column comment as `Field(description=...)`, per the discovery step.

**One file yields many rows.** A bank statement is many transactions, an invoice is many line
items. The compiled row model is therefore wrapped in a container model holding
`rows: list[Row]`, and that container is what goes to the provider. Both providers require a
JSON object at the top level, so a bare array is not a valid response format. The container is
also where document-level fields will live later if a table ever needs them.

Compiled models are cached, keyed on a fingerprint of the column list (name, type,
nullability, enum labels, ordinal). When someone alters the table the fingerprint changes and
the next request recompiles. Compiling on every request would burn the milliseconds we are
trying to save and would defeat the provider-side schema cache.

### 3. Route

Routing decides whether the file needs a model at all. Every input type gets the best parser
for that type rather than a single generic one.

| Input                  | Tool                                | Reason                                                          |
| ---------------------- | ----------------------------------- | --------------------------------------------------------------- |
| CSV, TSV               | stdlib `csv` + `charset-normalizer` | Encoding is the real problem, not speed. Latin-1 files are common. |
| XLSX, XLS, XLSB, ODS   | `python-calamine`                   | Rust engine. Also reads legacy `.xls`, which `openpyxl` cannot.   |
| PDF, digital           | `pdf-inspector`                     | Rust layout parser, markdown out.                                 |
| PDF, scanned or mixed  | `pdf-inspector` selective OCR       | Local OCR before any network call. See below.                     |
| Images                 | `pillow` + `pillow-heif`, then vision | EXIF rotation, flattening and downscaling, then the model.      |

`openpyxl` is the obvious alternative for spreadsheets and is the wrong choice here: pure
Python, last released mid-2024, and no support for `.xls` at all.

**What a spreadsheet can and cannot carry.** XLSX stores numbers as decimal text in XML, not
as binary doubles, so an ordinary monetary value survives the round trip through the reader
exactly. The limit is the writing application, not the format and not the reader: a value is
converted to a float before it is saved, so anything past 2**53 arrives already rounded. A
twenty-digit account number is in the file as `1.234567890123457e+19` and the missing digits
are not recoverable by any reader, because they are not there. Switching libraries does not
help; `xlsxr` and `pandas` with `dtype=object` return the stored text unconverted, which is
the same rounded value.

What SchemaGate controls is whether it makes the situation worse. Expanding such a float back
to integer notation produces twenty digits that were never in the file, and hands the database
a plausible, wrong identifier. So integer expansion stops at 2**53. Past that the value keeps
its float form, fails integer coercion, and is reported. A rejected account number is
recoverable; a silently fabricated one is not.

Routing signals:

- Tabular files are parsed directly. No model call, no cost. Matching headers against the
  schema is a string problem.
- `pdf_inspector.process_pdf` classifies in single-digit milliseconds and returns markdown for
  the text-based case. `pdf_type` (`text_based`, `scanned`, `image_based`, `mixed`) is the
  routing signal.
- `has_encoding_issues` on an otherwise text-based PDF forces the OCR path. Broken CID maps
  produce text that looks fine and is wrong, which is worse than no text at all.
- An empty text layer forces the OCR path, since `markdown` comes back as `None` for anything
  the native reader could not read.

**`pages_needing_ocr` is not the signal it appears to be.** Measured against a readable
three-page document, the parser listed all three pages while also returning clean markdown and
classifying the file as `text_based`. The field marks a page as sparse, not as failed. Routing
on it would send documents that already parsed perfectly to a paid vision model, which is the
single most expensive mistake available in this pipeline. It is carried through the result for
diagnostics and is deliberately not part of the decision.

Worth noting for the same reason: a blank page classifies as `scanned` with 0.9 confidence.
Confidence is not a usable gate either.

**OCR reports its own failures, and they are acted on.** `pages_recommending_hosted` names
the pages the parser could not read well enough. Measured on a small blurred scan, PP-OCR
returns a single wrong character while setting that field, so ignoring it would hand a model
nonsense as the document. Those pages are rasterised and sent to vision instead. A clean scan
never pays for it.

**Local OCR before vision.** `process_pdf_with_ocr` routes only the pages native extraction
rejected, and returns per-page provenance (`native`, `ocr`, `fused`). The wheel deliberately
ships no models, so ONNX artifacts are downloaded once and passed as `model_directory` with
`offline=True`. That costs image size. It buys the thing the product is built on: a scanned
document never leaves the customer's network. Sending scans to a hosted vision API would
contradict the privacy position for exactly the documents most likely to be sensitive, so
vision is an explicit opt-in fallback, never the automatic default for scanned input.

**Image normalization is not optional.** A phone photo carries EXIF orientation, so the pixels
are rotated even though every viewer displays it upright, and a vision model reads the pixels.
Apply `ImageOps.exif_transpose`, then downscale to the provider's optimal long edge before
sending. Skipping this means paying for tokens on an oversized sideways image and getting
worse extraction for the money. HEIC is the iPhone default and needs `pillow-heif` to open at
all.

`pdf-inspector` is compiled native code and holds the GIL for the duration of a call. It has
to run through `anyio.to_thread.run_sync`, or one large PDF stalls the event loop for every
concurrent request. Same for the spreadsheet parser.

The parser sits behind a `LayoutParser` protocol with `classify()` and `to_markdown()`. That
is not speculative abstraction. It is one small interface that lets the parser be replaced
without touching the pipeline, and lets tests run without the native wheel installed.

### 4. Extract

An `Extractor` protocol with a single method, taking markdown or images plus the compiled
model and returning an instance of it. Implementations, in build order:

- **Stub.** Deterministic, offline, free. Returns canned rows for a given fixture. This is the
  first implementation because the test suite needs it: a test that calls a paid API is a test
  that stops being run. Milestones 1 through 5 exercise the whole pipeline against it.
- **Ollama.** `format` takes a JSON Schema directly and Ollama constrains generation with
  XGrammar. Constrained decoding happens at token sampling inside a runtime the operator
  controls, which is a stronger guarantee than a hosted promise, and it costs nothing to run
  in development. It is also what makes "runs entirely inside your network" true of the whole
  pipeline rather than only the parsing half.
- **Anthropic.** `client.messages.parse(model="claude-opus-5", output_format=Model)`, reading
  `response.parsed_output`. Check `stop_reason` for `refusal` before touching content.
- **OpenAI.** `client.chat.completions.parse(response_format=Model)`, reading `message.parsed`
  and handling `message.refusal`.

The SDKs disagree on method name, parameter name, result attribute, and how a refusal is
signalled. That is precisely why the protocol exists instead of a set of
`if provider == ...` branches spread through the pipeline.

**Schema compliance is not accuracy.** A grammar guarantees the shape of the output, not the
truth of it, and a small local model returns well-formed wrong answers more often than a
frontier model does. This is the reason the validation gate is built before the first real
extractor rather than after it. Without the gate, the local path produces confident garbage
that passes every type check.

The system prompt is static and identical across requests so that it caches. The schema and
the document go after it. Anything that varies per request (timestamps, request ids) stays
out of the cached prefix.

Vision requests use the same protocol and the same compiled model, with normalized images in
place of markdown. `gpt-4o` is no longer a current vision default; use `claude-opus-5` or the
current OpenAI flagship.

#### Why not Instructor

Instructor is the obvious candidate for this block and was evaluated first. Two properties
rule it out as the core path, both checked against the published package rather than
recalled:

1. **It hard-pins `anthropic==0.93.0`** in its `anthropic` extra. Not a floor, an exact pin.
   The current Anthropic SDK is 1.2.0, a major version ahead, and 0.x to 1.x was breaking.
   Adopting Instructor means the project cannot run the current SDK and every future provider
   feature waits on Instructor's release cycle.
2. **Its Anthropic modes are `ANTHROPIC_TOOLS` and `ANTHROPIC_JSON`**, with `Mode.TOOLS` as
   the default. There is no Anthropic native-structured-outputs mode, although native modes
   exist for other providers. Tool calling is a weaker guarantee than constrained decoding
   against a strict schema, so routing through Instructor would quietly downgrade the one
   guarantee this product is built to make.

What it would save is roughly fifty lines per provider, plus a retry loop that re-asks the
model when validation fails. That retry is genuinely useful, but it multiplies latency by the
number of attempts, so it should be a deliberate and bounded choice rather than a library
default.

Instructor remains a good fit as an additional `Extractor` implementation later, covering
Gemini, local models, and anything else without native structured output. That is where its
multi-provider strength actually pays, and the protocol makes it a drop-in.

### 5. Validate

This stage is a validation gate, not a circuit breaker. A circuit breaker is a specific
resilience pattern that trips after repeated downstream failures. Calling a business-rule
check by that name will mislead anyone reading the code later.

Three layers, cheapest first:

1. Type coercion. Strings to `Decimal`, `date`, `datetime`, `UUID`. Failures are field-level
   and reported with the offending value.
2. Database constraints the LLM schema could not carry: `varchar(n)` length, `NOT NULL`, enum
   membership, and check constraints where they are simple enough to read from
   `pg_constraint`.
3. Arithmetic rules supplied per table in config. Line items summing to a subtotal, subtotal
   plus tax equalling total, and so on. Comparison is on `Decimal` with an explicit tolerance,
   never on floats.

A row that fails any layer comes back flagged, with the failing checks attached alongside the
extracted values. It is not silently dropped and it is not silently repaired. The caller
decides.

Each layer skips cells an earlier one already rejected. One wrong cell should produce one
finding, not the same problem restated three times in different words.

**Decimal separators are the riskiest conversion in the product.** Reading a grouping
separator as a decimal point is wrong by a factor of a thousand, on exactly the numbers people
care about most. The rule:

- Both separators present: the last one is the decimal point. This settles `1.234,56` and
  `1,234.56` outright.
- A separator that repeats can only be grouping, so `1.234.567` is unambiguous.
- A single separator followed by exactly three digits cannot be decided from the string.
  `1,234` is either one thousand two hundred and thirty four or one point two three four.

For that last case the string is out of evidence, so two other authorities are consulted in
order. This is the same move that makes column comments valuable: SchemaGate knows things
about the destination that a general-purpose parser does not.

**First, the column.** A `numeric(12,2)` column holds two decimal places, so three digits
after the separator cannot be a fraction and the separator must be grouping. Certain, not
inferred. An integer column settles it the same way, with scale zero. Money columns are almost
always `numeric(p,2)`, so this resolves the overwhelming majority of real cases, and it
outranks everything below it.

**Then, the rest of the file.** A scale of three or more, or a bare `numeric` with no declared
scale, leaves the column unable to decide. So every value in that column is read first: if any
of them is unambiguous, it reveals the file's convention, and that convention resolves the
rest. `10,50` elsewhere in the column proves the comma is this file's decimal point.
Contradictory evidence, one row saying `10,50` and another `9,876.50`, proves nothing and is
treated as no evidence at all.

**Only then, refusal.** What survives both is genuinely undecidable, and the report says so
and names the fix: declaring the column with a scale would resolve it.

The scale is read from `atttypmod` in the same catalog query as everything else, which costs
nothing extra.

Integer columns take digits only. An exponent arriving at this point means the source already
lost the exact value, which is precisely the case the spreadsheet reader leaves in float form
so that it fails here instead of being written as fabricated digits.

Real exports also carry currency symbols, no-break spaces between thousands groups (the French
and Nordic convention) and accounting negatives in parentheses. All three are handled.
Regional date formats are refused: `05/01/2026` is January or May depending on who wrote it.

**Arithmetic rules are declared as data, never parsed from an expression.** Rules arrive from
configuration, and an expression evaluator that takes configuration is a way to run arbitrary
code inside the service. Comparison is on `Decimal` with an explicit tolerance; on floats the
check would report `0.1 + 0.2` as unequal to `0.3` and flag correct invoices.

## Layout

```
src/schemagate/
  config.py            pydantic-settings, named connections, per-table rules
  api/
    app.py             app factory, lifespan owns the pool
    routes.py          POST /v1/extract
  db/
    pool.py            asyncpg pool
    introspect.py      pg_catalog -> TableSchema
  schema/
    spec.py            ColumnSpec, TableSchema, fingerprint
    factory.py         TableSchema -> Pydantic row and container models
    cache.py
  ingest/
    router.py          file type and PDF class -> Route
    tabular.py         CSV via stdlib, spreadsheets via calamine
    pdf.py             pdf-inspector, native then selective OCR
    images.py          EXIF transpose, downscale, HEIC
  extract/
    base.py            Extractor protocol
    anthropic.py
    openai.py
  validate/
    rules.py
    report.py
  errors.py
tests/
```

## Contract

```
POST /v1/extract
  multipart: file=<upload>, connection=<config key>, table=<name>

200 {
  "status": "ok" | "flagged",
  "table": "invoices",
  "route": "tabular" | "native_pdf" | "vision",
  "rows": [ { ... } ],
  "validation": { "checks": [...], "failures": [...] },
  "timings_ms": { "discover": 4, "parse": 120, "extract": 1400, "validate": 2 }
}
```

`status` is `flagged`, not an error code, when extraction succeeded but a check failed. HTTP
4xx is reserved for the caller's mistakes (unknown table, unsupported file type, file too
large) and 5xx for ours. A failed arithmetic check is neither.

## Latency

Worth being honest about the numbers up front, because they set the design.

- Discover: one indexed query, low single-digit milliseconds, and cached after the first hit.
- Parse: `pdf-inspector` reports under 200ms for text-based PDFs. Believable, and it is the
  part we control.
- Extract: the LLM call. Hundreds of milliseconds to several seconds depending on document
  length and model. This dominates everything else by an order of magnitude.
- Validate: microseconds.

So the tabular route is genuinely sub-100ms end to end, because it never calls a model. The
PDF route is parse fast, then wait on the model. The engineering worth doing is on raising the
share of documents that avoid the model entirely and on keeping the prompt prefix cacheable.
Shaving the parser is not where the time is.

Note that current top-tier models reject `temperature` and `top_p` outright, so determinism is
not a sampling parameter you get to set. It comes from the schema constraint and the
validation gate.

## Deployment

Self-hosting is the product, not a secondary distribution channel. Someone evaluating
SchemaGate should have it running against their own database in a couple of minutes, without
reading this document. That sets some hard requirements.

**One command to a running service.** A published image and a `compose.yaml` that starts the
API and nothing else it does not need. The database is theirs and already exists, so compose
must not silently start a second Postgres and connect to that instead.

**Configuration entirely through environment variables**, listed once in `.env.example`, with
every variable named in the README table. No config file format to learn, no flags that only
exist in code.

**Fail loudly at startup, not on the first request.** Validate settings, connect the pool, and
confirm the configured tables resolve, before the server reports ready. A misconfigured
deployment should refuse to start with a message naming the missing variable, rather than
returning 500s an hour later.

**OCR models are a build-time decision.** The base image stays small and reaches hosted
providers. A separate tag bundles the ONNX artifacts so scanned documents can be processed
with no outbound network at all. Document both, and say plainly which one leaves the network.

**`/health` for liveness and a distinct readiness check** that proves the pool is live, so
orchestrators can tell "starting" apart from "broken".

Non-Docker installation stays supported, since the native wheels (`pdf-inspector`,
`python-calamine`, `asyncpg`) all publish prebuilt binaries for the platforms that matter.
Docker is the recommended path, not the only one.

## README

The README is the first thing anyone sees and most readers never get past it, so it is written
once, deliberately, and kept short. Structure:

1. One sentence on what it does, in concrete terms.
2. A single code block showing a request and the JSON that comes back. Show, do not describe.
3. Requirements, then quickstart: the compose command and the two environment variables needed
   to reach a database.
4. Configuration table: variable, default, meaning.
5. Supported file types and which route each one takes.
6. API reference for the one endpoint.
7. Limitations, stated plainly. What it does not do, and what it does badly.
8. Licence.

What it will not contain: an emoji per heading, a feature list of adjectives, benchmark claims
without a reproducible method, a roadmap, or any sentence that would read identically in a
README for a different project. Every number in it must be one that someone can reproduce from
the repository. The limitations section is the credibility test, because an engineer
evaluating infrastructure looks for it first and distrusts a README that has none.

## Hygiene

This repository is public, which drives a few rules:

- No credentials in the tree. `.env` is ignored, `.env.example` is committed with empty values,
  configuration is read from environment variables only.
- Never log a DSN, an API key, or document content. Log the table, the route, the timings, and
  a request id.
- Uploaded files live in memory or a temp file for the life of the request and are not
  persisted.
- A request body size cap enforced before the file is read, not after.

Tooling: `ruff` for lint and format, `mypy --strict` over `src/`, `pytest` with
`pytest-asyncio`, `pre-commit`, and GitHub Actions running all of it on push. Postgres
integration tests run against a service container. The schema factory is tested against a real
database, because introspection verified against a mock proves nothing.

## Milestones

Each one ends with something that runs and is tested.

Development is test first. Each unit starts as a failing test that names the behaviour, then
the smallest implementation that passes it.

0. **Done.** Repository skeleton: packaging, configuration, lint, types, CI, an app that starts
   and answers `/health`.
1. **Done.** Introspection and the model factory, verified in CI against a real PostgreSQL:
   every type in the mapping above, enum labels, column comments, varchar length, numeric
   scale, and the identity, generated and default flags.
2. **Done.** Tabular fast path. CSV and spreadsheets to rows keyed by column, no model call.
3. **Done.** Native PDF path, thread-offloaded, plus content-based upload routing.
4. **Done.** Validation gate. Coercion, database constraints and arithmetic rules. Pure logic,
   no model, so it is fully testable on its own.
5. **Done.** First real extractor, Ollama. Container model, schema-constrained generation, and the
   validation gate already in place to catch well-formed wrong answers.
6. **Done.** Docker image, compose file, README and playground. Someone else runs it against
   their own database without asking a question.
7. **Done.** Hosted extractors, Anthropic and OpenAI, plus an OpenAI-compatible one covering
   Groq, OpenRouter, Together, DeepSeek, vLLM and LM Studio through the same adapter. Three
   more implementations of the protocol, which is what proved it was the right shape.
8. **Done.** Local OCR for scanned PDFs, behind an `ocr` extra. 822 ms on a scanned invoice
   with nothing leaving the machine, which is the point: the alternative was posting scans
   to a vision API, contradicting the reason to run this inside your own network.
9. **Done.** Image input, normalised then read by a vision model. Measured research settled
   the routing: vision beats traditional OCR by ten to fifteen points on degraded input,
   which is exactly what a photograph is, while OCR wins on clean fixed layouts.
10. Optional commit to the database, off by default.
11. Other databases. MySQL exposes column comments and enum members through
    `information_schema`, so it is genuinely viable; SQLite has neither but works for basic
    types. Both need the catalog read to become an interface rather than one query.

Two orderings here are deliberate. The validation gate precedes the first real extractor,
because a schema-constrained model produces well-formed wrong answers and the gate is what
catches them. And milestone 6 sits in the middle rather than at the end, because software
nobody else can deploy is not finished, and learning that after ten milestones is worse than
learning it after five.

## Settled

- One file produces many rows. The container model wraps `rows: list[Row]`.
- `pg_catalog` over `information_schema`, for enum labels, check constraints, and column
  comments.
- Native provider SDKs over Instructor for the core path, for the reasons recorded above.
- Per file type, the best available parser rather than one generic reader.
- Extractor order: stub for tests, then Ollama, then the hosted providers.
- Connections are named in configuration and referenced by key. Per-request DSNs would let any
  caller point the service at an arbitrary host on the deploying network, so they stay out
  until a real multi-tenant requirement arrives, and then only behind an allowlist.
- Apache 2.0. The usual choice for developer infrastructure adopted inside companies, since it
  grants patent rights explicitly and legal review already knows it.
- Environment variables only, no dotenv in the settings object. Compose reads `env_file`
  natively and `uv run --env-file` covers local development, so reading `.env` inside
  `Settings` would buy nothing and make tests non-hermetic.
