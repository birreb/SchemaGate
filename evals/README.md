# Evaluations

Accuracy, latency and cost measured together, on documents whose correct reading is written
down.

The unit tests exercise the pipeline against a stub extractor. They do not say whether a given
model reads a given invoice correctly, which is what this measures.

## Running it

```console
$ export SCHEMAGATE_PROVIDER=anthropic
$ export ANTHROPIC_API_KEY=...
$ schemagate evaluate
```

Or compare two models directly:

```console
$ schemagate evaluate --provider anthropic --model claude-opus-5 \
    --prices '{"claude-opus-5": {"input": 5, "output": 25}}'
$ schemagate evaluate --provider anthropic --model claude-haiku-4-5 \
    --prices '{"claude-haiku-4-5": {"input": 1, "output": 5}}'
```

```
case                       route        rows   cells      ms   tokens       cost
--------------------------------------------------------------------------------
invoices-csv               tabular         3   24/24      34        0          -
invoices-european          tabular         3   24/24       0        0          -
invoice-pdf                native_pdf      1     8/8    4102     3184  $0.020920
--------------------------------------------------------------------------------
3/3 cases clean, 56/56 cells (100.0%), 4136 ms, 3184 tokens, $0.020920
```

With no provider configured, the two tabular cases still run and still score. They never call
a model, so any cost reported against them is a bug.

The exit code is zero only when every case is clean, which makes this usable as a gate on a
model change.

## Why all three numbers

The question is which model is cheap enough and still correct. Accuracy and cost do not answer
that separately.

Scored per cell rather than per row. A model that misreads one date out of eight columns and
one that invents the whole row are not the same failure, and a per-row score reports them
identically.

## Adding a case

One JSON file per case in `cases/`. It carries its own table definition, so a case runs against
a provider with no database involved.

```json
{
  "name": "supplier-b-statement",
  "why": "Two invoices on one page, and a credit note in between.",
  "document": "evals/documents/statement.pdf",
  "instructions": "One row per invoice. Ignore the credit note.",
  "table": {
    "schema": "public",
    "name": "invoices",
    "columns": [
      {"name": "invoice_number", "data_type": "text", "nullable": false, "ordinal": 1}
    ]
  },
  "rules": [{"terms": ["subtotal", "tax"], "equals": "total"}],
  "expected_flags": 0,
  "expected": [{"invoice_number": "INV-1"}]
}
```

`why` is required by a test, so that a fixture always records what it is for.

`expected_flags` is how many values the validation gate should reject. A document with a
deliberate arithmetic error is testing the gate, and scoring it as a miss would reward a model
that corrected the document instead of copying it.

Paths are relative to the repository root.

## What these cases cover

| Case | Covers |
| --- | --- |
| `invoices-csv` | The tabular route, which calls no model. The control for cost. |
| `invoices-european` | Semicolon separated, comma decimals, dotted thousands, and one total that does not add up. |
| `invoice-pdf` | A real PDF with a discount line between subtotal and total, a date written as words, and the buyer's VAT number next to the seller's. |

Every run calls a real model and is billed. The suite is small for that reason.
