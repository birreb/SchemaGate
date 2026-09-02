-- The database the ingestion benchmark extracts into.
--
-- A small ERP: twenty-four tables of which three are extraction targets. The
-- rest exist because a real database has them, and because one of the
-- approaches under test sends the whole schema to the model on every request.
--
-- Loaded once by `bench/run.py`. Every benchmark insert runs inside a
-- transaction that is rolled back, so the tables stay empty.

BEGIN;

CREATE TYPE invoice_status AS ENUM ('draft', 'sent', 'paid', 'void');
CREATE TYPE payment_method AS ENUM ('bank_transfer', 'card', 'cash', 'direct_debit');
CREATE TYPE expense_category AS ENUM ('travel', 'meals', 'lodging', 'office', 'software', 'other');
CREATE TYPE order_status AS ENUM ('open', 'confirmed', 'shipped', 'closed', 'cancelled');
CREATE TYPE claim_status AS ENUM ('submitted', 'approved', 'rejected', 'reimbursed');

CREATE TABLE currencies (
    code        char(3) PRIMARY KEY,
    name        text NOT NULL,
    minor_units int2 NOT NULL DEFAULT 2
);

CREATE TABLE exchange_rates (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    currency    char(3) NOT NULL REFERENCES currencies(code),
    rate_date   date NOT NULL,
    to_sek      numeric(14, 6) NOT NULL,
    UNIQUE (currency, rate_date)
);

CREATE TABLE tax_codes (
    code        varchar(8) PRIMARY KEY,
    description text NOT NULL,
    rate        numeric(5, 2) NOT NULL
);

CREATE TABLE gl_accounts (
    account_no  varchar(8) PRIMARY KEY,
    name        text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('asset', 'liability', 'equity', 'revenue', 'expense'))
);

CREATE TABLE departments (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        varchar(64) NOT NULL UNIQUE,
    cost_centre varchar(16) NOT NULL
);

CREATE TABLE employees (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_no varchar(16) NOT NULL UNIQUE,
    full_name   text NOT NULL,
    email       text NOT NULL UNIQUE,
    department_id bigint REFERENCES departments(id),
    hired_on    date NOT NULL,
    active      boolean NOT NULL DEFAULT true
);

CREATE TABLE suppliers (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text NOT NULL,
    vat_id      varchar(20),
    country     char(2) NOT NULL,
    email       text,
    payment_terms_days int2 NOT NULL DEFAULT 30,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE customers (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text NOT NULL,
    vat_id      varchar(20),
    country     char(2) NOT NULL,
    billing_address text,
    credit_limit numeric(12, 2),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE products (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku         varchar(32) NOT NULL UNIQUE,
    name        text NOT NULL,
    unit        varchar(8) NOT NULL DEFAULT 'pcs',
    list_price  numeric(12, 2) NOT NULL,
    tax_code    varchar(8) REFERENCES tax_codes(code),
    discontinued boolean NOT NULL DEFAULT false
);

CREATE TABLE warehouses (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code        varchar(8) NOT NULL UNIQUE,
    name        text NOT NULL,
    country     char(2) NOT NULL
);

CREATE TABLE stock_levels (
    warehouse_id bigint NOT NULL REFERENCES warehouses(id),
    product_id   bigint NOT NULL REFERENCES products(id),
    on_hand      integer NOT NULL DEFAULT 0,
    reserved     integer NOT NULL DEFAULT 0,
    counted_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (warehouse_id, product_id)
);

CREATE TABLE purchase_orders (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    po_number   varchar(32) NOT NULL UNIQUE,
    supplier_id bigint NOT NULL REFERENCES suppliers(id),
    ordered_on  date NOT NULL,
    expected_on date,
    status      order_status NOT NULL DEFAULT 'open',
    currency    char(3) NOT NULL REFERENCES currencies(code),
    notes       text
);

CREATE TABLE purchase_order_lines (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    po_id       bigint NOT NULL REFERENCES purchase_orders(id),
    line_no     int2 NOT NULL,
    product_id  bigint REFERENCES products(id),
    description text NOT NULL,
    quantity    numeric(12, 3) NOT NULL,
    unit_price  numeric(12, 4) NOT NULL,
    UNIQUE (po_id, line_no)
);

CREATE TABLE sales_orders (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    so_number   varchar(32) NOT NULL UNIQUE,
    customer_id bigint NOT NULL REFERENCES customers(id),
    ordered_on  date NOT NULL,
    status      order_status NOT NULL DEFAULT 'open',
    currency    char(3) NOT NULL REFERENCES currencies(code)
);

CREATE TABLE sales_order_lines (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    so_id       bigint NOT NULL REFERENCES sales_orders(id),
    line_no     int2 NOT NULL,
    product_id  bigint REFERENCES products(id),
    quantity    numeric(12, 3) NOT NULL,
    unit_price  numeric(12, 4) NOT NULL,
    UNIQUE (so_id, line_no)
);

-- Extraction target. One row per supplier invoice received.
CREATE TABLE invoices (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_number  varchar(32) NOT NULL,
    supplier        text NOT NULL,
    vat_id          varchar(20),
    currency        char(3) NOT NULL DEFAULT 'SEK',
    status          invoice_status NOT NULL DEFAULT 'draft',
    subtotal        numeric(12, 2) NOT NULL,
    tax             numeric(12, 2) NOT NULL,
    total           numeric(12, 2) NOT NULL,
    shipping        numeric(12, 2) NOT NULL DEFAULT 0,
    issued_on       date NOT NULL,
    due_on          date,
    po_reference    varchar(32),
    received_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (supplier, invoice_number)
);
COMMENT ON COLUMN invoices.invoice_number IS 'The number the supplier assigned, exactly as printed.';
COMMENT ON COLUMN invoices.supplier IS 'Legal name of the company that issued the invoice, not the company being billed.';
COMMENT ON COLUMN invoices.vat_id IS 'The seller''s VAT or organisation number as printed, whatever it is labelled: Momsreg.nr, USt-IdNr., VAT Reg No, Org.nr. Never the buyer''s. Null only when the document carries none.';
COMMENT ON COLUMN invoices.currency IS 'ISO 4217 code of the amounts on the invoice.';
COMMENT ON COLUMN invoices.subtotal IS 'Net amount of the goods and services after any discount, before shipping and before tax.';
COMMENT ON COLUMN invoices.shipping IS 'Shipping, freight or carriage charged, before tax. 0 when the invoice has none.';
COMMENT ON COLUMN invoices.tax IS 'Total VAT charged.';
COMMENT ON COLUMN invoices.total IS 'Amount due.';
COMMENT ON COLUMN invoices.issued_on IS 'Invoice date. Not the due date and not the delivery date.';
COMMENT ON COLUMN invoices.due_on IS 'Payment due date, when stated.';
COMMENT ON COLUMN invoices.po_reference IS 'Our purchase order number quoted on the invoice, when there is one.';

-- Extraction target. One row per line item on a supplier invoice. Linked to the
-- invoice by its printed number, since a document cannot know a database id.
CREATE TABLE invoice_lines (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_number  varchar(32) NOT NULL,
    line_no         int2 NOT NULL,
    description     text NOT NULL,
    quantity        numeric(12, 3) NOT NULL,
    unit_price      numeric(12, 4) NOT NULL,
    line_total      numeric(12, 2) NOT NULL,
    tax_rate        numeric(6, 3)
);
COMMENT ON COLUMN invoice_lines.invoice_number IS 'The invoice this line belongs to, as printed on it.';
COMMENT ON COLUMN invoice_lines.line_no IS 'Position of the line on the invoice, counting from 1.';
COMMENT ON COLUMN invoice_lines.quantity IS 'Quantity as printed, without the unit.';
COMMENT ON COLUMN invoice_lines.description IS 'The item text only, without a position number or article number in front of it.';
COMMENT ON COLUMN invoice_lines.unit_price IS 'Price per unit before tax.';
COMMENT ON COLUMN invoice_lines.line_total IS 'Net amount for the line before tax.';
COMMENT ON COLUMN invoice_lines.tax_rate IS 'VAT rate for the line in percent, from the line''s own rate column when there is one.';

CREATE TABLE credit_notes (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    credit_number   varchar(32) NOT NULL,
    supplier        text NOT NULL,
    invoice_number  varchar(32),
    amount          numeric(12, 2) NOT NULL,
    issued_on       date NOT NULL,
    reason          text
);

CREATE TABLE payments (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_id      bigint REFERENCES invoices(id),
    paid_on         date NOT NULL,
    amount          numeric(12, 2) NOT NULL,
    method          payment_method NOT NULL,
    reference       varchar(64)
);

CREATE TABLE bank_accounts (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    iban            varchar(34) NOT NULL UNIQUE,
    bic             varchar(11),
    holder          text NOT NULL,
    currency        char(3) NOT NULL REFERENCES currencies(code)
);

-- Extraction target. One row per receipt on an employee expense claim.
CREATE TABLE expense_claims (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    merchant        text NOT NULL,
    spent_on        date NOT NULL,
    category        expense_category NOT NULL,
    amount          numeric(12, 2) NOT NULL,
    tax             numeric(12, 2),
    currency        char(3) NOT NULL DEFAULT 'SEK',
    payment         payment_method,
    receipt_number  varchar(32),
    status          claim_status NOT NULL DEFAULT 'submitted',
    submitted_at    timestamptz NOT NULL DEFAULT now()
);
COMMENT ON COLUMN expense_claims.merchant IS 'The shop or company on the receipt.';
COMMENT ON COLUMN expense_claims.spent_on IS 'Date of purchase as printed on the receipt.';
COMMENT ON COLUMN expense_claims.category IS 'What kind of expense this is, judged from the merchant and items.';
COMMENT ON COLUMN expense_claims.amount IS 'Total paid including tax.';
COMMENT ON COLUMN expense_claims.tax IS 'VAT included in the total, when printed.';
COMMENT ON COLUMN expense_claims.payment IS 'How it was paid, when the receipt says.';
COMMENT ON COLUMN expense_claims.receipt_number IS 'Receipt or transaction number, when printed.';

CREATE TABLE journal_entries (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entry_date      date NOT NULL,
    description     text NOT NULL,
    posted_by       bigint REFERENCES employees(id),
    posted_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE journal_lines (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entry_id        bigint NOT NULL REFERENCES journal_entries(id),
    account_no      varchar(8) NOT NULL REFERENCES gl_accounts(account_no),
    debit           numeric(14, 2) NOT NULL DEFAULT 0,
    credit          numeric(14, 2) NOT NULL DEFAULT 0,
    CHECK (debit = 0 OR credit = 0)
);

CREATE TABLE attachments (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity          text NOT NULL,
    entity_id       bigint NOT NULL,
    filename        text NOT NULL,
    content_type    text NOT NULL,
    bytes           bigint NOT NULL,
    uploaded_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    happened_at     timestamptz NOT NULL DEFAULT now(),
    actor           text NOT NULL,
    action          text NOT NULL,
    entity          text NOT NULL,
    entity_id       bigint,
    details         jsonb
);

CREATE TABLE app_users (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username        varchar(32) NOT NULL UNIQUE,
    employee_id     bigint REFERENCES employees(id),
    role            text NOT NULL CHECK (role IN ('admin', 'accountant', 'viewer')),
    last_login_at   timestamptz
);

INSERT INTO currencies (code, name) VALUES
    ('SEK', 'Swedish krona'), ('EUR', 'Euro'), ('USD', 'US dollar'),
    ('GBP', 'Pound sterling'), ('NOK', 'Norwegian krone'), ('DKK', 'Danish krone');

INSERT INTO tax_codes (code, description, rate) VALUES
    ('SE25', 'Sweden standard', 25.00), ('SE12', 'Sweden reduced', 12.00),
    ('SE6', 'Sweden books and transport', 6.00), ('DE19', 'Germany standard', 19.00),
    ('GB20', 'UK standard', 20.00), ('ZERO', 'Zero rated', 0.00);

COMMIT;
