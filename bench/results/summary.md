# Ingestion benchmark results

## cerebras:gpt-oss-120b

Documents that need a model (invoices, statements, line items, receipts):

| approach | docs | cells correct | wrong value stored | left blank | flagged | held for review | rejected by DB | missing | rows inserted | phantom cols | inconsistent docs caught | median ms | tokens in | tokens out | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| whole_schema | 70 | 86.8% | 48 | 63 | 0 | 0 | 70 | 10 | 164/172 | 0 | 0/5 | 624 | 221951 | 35708 | $0.1045 |
| whole_schema_sql | 70 | 86.8% | 49 | 65 | 0 | 0 | 0 | 76 | 164/172 | 0 | 0/5 | 554 | 221251 | 42842 | $0.1096 |
| one_table_text | 70 | 86.6% | 39 | 63 | 0 | 0 | 62 | 30 | 162/172 | 0 | 0/5 | 505 | 72716 | 33060 | $0.0502 |
| schemagate | 70 | 88.8% | 9 | 52 | 35 | 6 | 0 | 60 | 165/172 | 0 | 5/5 | 469 | 85931 | 30323 | $0.0528 |

How the document was sent: one_table_text as csv (6), one_table_text as pdf_text (70), schemagate as native_pdf (60), schemagate as ocr_pdf (10), schemagate as tabular (6), whole_schema as csv (6), whole_schema as pdf_text (70), whole_schema_sql as csv (6), whole_schema_sql as pdf_text (70)

Spreadsheets and CSV files:

| approach | case | rows | cells correct | wrong value stored | left blank | rejected | missing | truncated | ms | tokens in | tokens out | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| whole_schema | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 1968 | 5402 | 4200 | $0.0050 |
| whole_schema_sql | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 2387 | 5392 | 4639 | $0.0054 |
| one_table_text | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 1934 | 3302 | 4218 | $0.0043 |
| schemagate | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 318 | 724 | 206 | $0.0004 |
| whole_schema | tab01 | 200/200 | 99.9% | 2 | 0 | 0 | 0 |  | 7239 | 13206 | 16023 | $0.0166 |
| whole_schema_sql | tab01 | 200/200 | 100.0% | 0 | 0 | 0 | 0 |  | 6603 | 13196 | 18861 | $0.0188 |
| one_table_text | tab01 | 199/200 | 99.5% | 1 | 0 | 0 | 10 |  | 5317 | 11106 | 16566 | $0.0163 |
| schemagate | tab01 | 200/200 | 100.0% | 0 | 0 | 0 | 0 |  | 339 | 728 | 213 | $0.0004 |
| whole_schema | tab02 | 200/200 | 99.8% | 4 | 0 | 0 | 0 |  | 5792 | 13211 | 15900 | $0.0165 |
| whole_schema_sql | tab02 | 200/200 | 100.0% | 0 | 0 | 0 | 0 |  | 6278 | 13201 | 18765 | $0.0187 |
| one_table_text | tab02 | 199/200 | 99.5% | 1 | 0 | 0 | 10 |  | 7843 | 11111 | 15986 | $0.0159 |
| schemagate | tab02 | 200/200 | 100.0% | 0 | 0 | 0 | 0 |  | 11 | 0 | 0 | unpriced |
| whole_schema | tab03 | 0/500 | 0.0% | 0 | 0 | 0 | 5000 |  | 18687 | 29089 | 39790 | $0.0400 |
| whole_schema_sql | tab03 | 166/500 | 32.8% | 0 | 0 | 0 | 3360 |  | 5973 | 29079 | 15833 | $0.0221 |
| one_table_text | tab03 | 372/500 | 73.9% | 4 | 0 | 0 | 1300 |  | 12888 | 26989 | 29847 | $0.0318 |
| schemagate | tab03 | 500/500 | 100.0% | 0 | 0 | 0 | 0 |  | 28 | 0 | 0 | unpriced |
| whole_schema | tab04 | 0/1000 | 0.0% | 0 | 0 | 0 | 10000 |  | 853 | 55650 | 493 | $0.0198 |
| whole_schema_sql | tab04 | 58/1000 | 5.8% | 0 | 0 | 0 | 9420 |  | 2568 | 55640 | 5821 | $0.0238 |
| one_table_text | tab04 | 0/1000 | 0.0% | 0 | 0 | 0 | 10000 |  | 1022 | 53550 | 265 | $0.0189 |
| schemagate | tab04 | 1000/1000 | 100.0% | 0 | 0 | 0 | 0 |  | 49 | 0 | 0 | unpriced |
| whole_schema | tab05 | 0/2000 | 0.0% | 0 | 0 | 0 | 20000 |  | 1066 | 107938 | 420 | $0.0381 |
| whole_schema_sql | tab05 | 0/2000 | 0.0% | 0 | 0 | 0 | 20000 |  | 3393 | 107928 | 518 | $0.0382 |
| one_table_text | tab05 | 0/2000 | 0.0% | 0 | 0 | 0 | 20000 |  | 5721 | 105838 | 473 | $0.0374 |
| schemagate | tab05 | 2000/2000 | 100.0% | 0 | 0 | 0 | 0 |  | 85 | 0 | 0 | unpriced |

Not attempted: 48 cases, unsupported: images

Errors:

- whole_schema tab03: JSONDecodeError: Expecting ',' delimiter: line 1 column 101333 (char 101332)

Why the database refused rows:

- whole_schema: not_null 8
- whole_schema_sql: not_null 5, syntax 1
- one_table_text: not_null 7
- schemagate: not_null 1

Examples of values stored wrong or left blank without anything flagging them:

- whole_schema:
  - inv01 subtotal: wanted '30295.85', stored Decimal('35466.88')
  - inv02 tax: wanted '23131.21', stored Decimal('23531.21')
  - inv07 subtotal: wanted '22378.10', stored Decimal('22128.10')
  - inv08 currency: wanted 'NOK', stored 'SEK'
  - inv09 subtotal: wanted '85413.93', stored Decimal('85264.93')
  - inv09 tax: wanted '18418.20', stored Decimal('18218.20')
  - inv10 subtotal: wanted '20433.33', stored Decimal('20338.33')
  - inv14 subtotal: wanted '35455.66', stored Decimal('39395.18')
  - inv15 subtotal: wanted '54172.93', stored Decimal('60192.15')
  - inv16 subtotal: wanted '51743.03', stored Decimal('57214.48')
- whole_schema_sql:
  - inv01 subtotal: wanted '30295.85', stored Decimal('35466.88')
  - inv04 subtotal: wanted '48878.99', stored Decimal('57504.70')
  - inv07 subtotal: wanted '22378.10', stored Decimal('22128.10')
  - inv08 subtotal: wanted '71038.12', stored Decimal('83574.26')
  - inv09 subtotal: wanted '85413.93', stored Decimal('85264.93')
  - inv09 tax: wanted '18418.20', stored Decimal('18218.20')
  - inv10 subtotal: wanted '20433.33', stored Decimal('20338.33')
  - inv14 subtotal: wanted '35455.66', stored Decimal('39395.18')
  - inv15 subtotal: wanted '54172.93', stored Decimal('60192.15')
  - inv16 subtotal: wanted '51743.03', stored Decimal('57214.48')
- one_table_text:
  - inv01 subtotal: wanted '30295.85', stored Decimal('35466.88')
  - inv07 subtotal: wanted '22378.10', stored Decimal('22128.10')
  - inv09 subtotal: wanted '85413.93', stored Decimal('85264.93')
  - inv10 subtotal: wanted '20433.33', stored Decimal('20338.33')
  - inv14 subtotal: wanted '35455.66', stored Decimal('39395.18')
  - inv15 subtotal: wanted '54172.93', stored Decimal('60192.15')
  - inv16 subtotal: wanted '51743.03', stored Decimal('57214.48')
  - inv20 subtotal: wanted '71348.06', stored Decimal('83938.89')
  - inv20 tax: wanted '15136.45', stored Decimal('2492.84')
  - inv20 issued_on: wanted '2026-08-26', stored datetime.date(2026, 8, 21)
- schemagate:
  - inv20 issued_on: wanted '2026-08-26', stored datetime.date(2026, 8, 21)
  - lines06 description: wanted 'Hydraulic hose 3/8in', stored '77638 Hydraulic hose 3/8in'
  - lines06 description: wanted 'Hydraulic hose 3/8in', stored '77638 Hydraulic hose 3/8in'
  - lines06 description: wanted 'Hydraulic hose 3/8in', stored '77638 Hydraulic hose 3/8in'
  - lines06 description: wanted 'Hydraulic hose 3/8in', stored '77638 Hydraulic hose 3/8in'
  - lines06 description: wanted 'Site commissioning', stored '21212 Site commissioning'
  - lines06 description: wanted 'Control panel enclosure', stored '73071 Control panel enclosure'
  - lines13 tax_rate: wanted '7', stored Decimal('19.000')
  - lines13 tax_rate: wanted '19', stored Decimal('15.000')
