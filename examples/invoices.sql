-- A table to try SchemaGate against.
--
-- It deliberately uses the features SchemaGate reads from the catalog, so the
-- behaviour described in the README is visible rather than described:
--
--   * a column comment, which becomes an extraction hint the model sees
--   * an enum, whose labels constrain what may be written
--   * numeric(12,2), whose scale resolves numbers like 1,234
--   * an identity column and a generated column, which the database owns and
--     which are never asked of a document
--
-- Load it with:
--   psql "$SCHEMAGATE_DSN" -f examples/invoices.sql

CREATE TYPE invoice_status AS ENUM ('draft', 'sent', 'paid', 'void');

CREATE TABLE invoices (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_number varchar(32) NOT NULL,
    supplier       text        NOT NULL,
    vat_id         text,
    status         invoice_status NOT NULL DEFAULT 'draft',
    subtotal       numeric(12, 2) NOT NULL,
    tax            numeric(12, 2) NOT NULL,
    total          numeric(12, 2) NOT NULL,
    issued_on      date        NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN invoices.vat_id IS
    'Seller VAT number, not the buyer. Null when the invoice does not carry one.';

COMMENT ON COLUMN invoices.supplier IS
    'Legal name of the company that issued the invoice.';

COMMENT ON COLUMN invoices.invoice_number IS
    'The number the supplier assigned, exactly as printed.';
