# Security

SchemaGate holds database credentials and parses files that arrive from outside, so both are
treated as part of the design rather than as an afterthought.

## Reporting a vulnerability

Report privately through GitHub's
[security advisory form](https://github.com/birreb/SchemaGate/security/advisories/new) rather
than a public issue. Please include the version, what an attacker gains, and the smallest input
that shows it.

Expect a first response within a week.

## What the design already assumes

Worth knowing when assessing the service, and worth preserving when changing it.

**Connection strings are never accepted over HTTP.** Callers reference a connection by a name
registered in configuration. A service that took a DSN in a request body would connect wherever
a caller pointed it, including hosts inside the deploying network that are not reachable from
outside it.

**Credentials are held as secrets and never rendered.** A test asserts that a database password
cannot appear in a string representation of the settings, and another that it cannot appear in
an error response.

**Table and schema names are query parameters, never interpolated.** Discovery also runs before
anything else touches the table, so the catalog acts as an allowlist: a name that is not in it
never reaches another statement.

**Uploads are identified by content, not by name.** A filename and a declared content type both
come from the caller. A PDF named `statement.csv` is treated as a PDF.

**Upload size is capped before the body is read.** Reading stops one byte past the limit, so an
oversized upload is never held whole in memory.

**Arithmetic rules are data, not expressions.** Rules arrive from configuration and are
declared as terms and a target. An expression evaluator that took configuration would be a way
to run arbitrary code next to a production database.

**The container runs unprivileged** as a non-root user, and ships only the virtualenv and the
source.

**The playground fetches nothing from the internet.** Everything on the page is inline, and a
test enforces it, so the service works inside a private network and cannot leak a request to a
third party.

**API keys are optional and cover every `/v1` endpoint at once.** Setting
`SCHEMAGATE_API_KEYS` requires `Authorization: Bearer` or `X-API-Key` on all of them.
`/health` stays open so a load balancer probe still works. A presented key is compared against
every configured key with `hmac.compare_digest`, and the loop does not stop at the first
match, so neither the time taken nor the response distinguishes a near miss from a wrong key.
An error body names neither the key presented nor how many are configured.

**A rate limit and a concurrency bound are available.** The first caps requests per caller per
minute; the second caps documents in flight, each of which holds its upload and its rendered
pages in memory. Both count within one process and are not distributed limiters.

**Every document reports what it cost.** The per-request `usage` block and the log line carry
tokens and, where a price is configured, money, so unusual spend is visible without reading a
provider dashboard.

## What it does not do

- It never writes to your database. Inserting is the caller's decision.
- It does not persist uploads. A file lives in memory for the life of the request.
- Authentication is off unless you configure keys. Keys are a bearer credential and nothing
  more: no scopes, no expiry, no per-key permissions. It is designed to run inside a private
  network, behind whatever your other internal services use, not on the public internet.
- The rate limit counts within one process. Four workers allow four times the configured
  limit. Use a gateway if that matters.
