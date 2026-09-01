## What changes, and why

<!-- The diff shows what. Explain why, especially if the change looks arbitrary
     without knowing what goes wrong otherwise. -->

## How it was verified

<!-- Which test fails without this change? If the change rests on how a library
     or PostgreSQL behaves, say how that was measured rather than assumed. -->

- [ ] A test fails without this change
- [ ] `uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest`
- [ ] Anything that touches the catalog query is covered by a `postgres` test

## Anything a reviewer should push back on

<!-- Shortcuts taken, cases not handled, or a decision you are unsure about.
     Saying so is faster than having it found. -->
