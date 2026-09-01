# SchemaGate

Extract documents into rows that match a PostgreSQL table you already own.

SchemaGate reads a table definition from your live database, compiles it into a validation
model at runtime, and constrains extraction so the output cannot disagree with your schema.
It returns JSON over HTTP. There is no UI and no dashboard.

## Status

Early development. Not yet usable. See [docs/architecture.md](docs/architecture.md) for the
design and [the milestones](docs/architecture.md#milestones) for what is built so far.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
