# Contributing

## Getting set up

```console
$ uv sync --all-extras
$ uv run pytest
```

Tests needing PostgreSQL are marked `postgres` and skip unless `SCHEMAGATE_TEST_DSN` is set, so
the suite runs on a machine without a database. CI runs them for real against PostgreSQL 17 on
Python 3.11 through 3.14.

Before opening a pull request:

```console
$ uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest
```

## How changes are made here

**Test first.** Write the failing test that names the behaviour, watch it fail, then write the
smallest thing that passes it. A test that has never failed has not been shown to test
anything.

**Say why, not what.** The diff already shows what changed. A commit message and a comment are
for the reasoning that is about to be forgotten, especially when a decision looks arbitrary
until you know what went wrong without it.

**Measure before claiming.** Several decisions in this project reversed once they were checked
against reality. If a change rests on how a library behaves, test that behaviour rather than
describing it, and prefer an assertion in the suite to a sentence in a comment.

**Prefer refusing to guessing.** This service feeds data into other people's databases. Where a
value is genuinely ambiguous, report it and say what would resolve it. A rejected row can be
fixed; a silently wrong one is found months later, if ever.

## Where the reasoning lives

[docs/architecture.md](docs/architecture.md) records the design and why each decision was made,
including the ones that changed after measurement. If you are about to alter something that
looks needlessly careful, it is probably explained there.

Some behaviour is deliberately pinned by tests that would otherwise look like candidates for
tidying up: exact numbers leaving as JSON strings, nullable columns staying in `required`,
length limits kept out of the generated schema, and PDF routing ignoring the parser's own
per-page OCR hint. Each has a comment saying what breaks without it.
