# Ingestion benchmark results

## cerebras:gpt-oss-120b

Documents that need a model (invoices, statements, line items, receipts):

| approach | docs | cells correct | wrong value stored | left blank | flagged | held for review | rejected by DB | missing | rows inserted | phantom cols | inconsistent docs caught | median ms | tokens in | tokens out | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| whole_schema | 70 | 90.6% | 14 | 37 | 0 | 0 | 83 | 11 | 163/172 | 0 | 0/5 | 627 | 231509 | 38842 | $0.1102 |
| whole_schema_sql | 70 | 90.5% | 26 | 43 | 0 | 0 | 0 | 77 | 165/172 | 0 | 0/5 | 645 | 230809 | 44739 | $0.1143 |
| one_table_text | 70 | 91.4% | 18 | 32 | 0 | 0 | 83 | 0 | 164/172 | 0 | 0/5 | 493 | 78944 | 34582 | $0.0536 |
| schemagate | 70 | 91.9% | 2 | 11 | 39 | 28 | 0 | 44 | 165/172 | 0 | 5/5 | 547 | 92469 | 32523 | $0.0568 |

How the document was sent: one_table_text as csv (6), one_table_text as pdf_text (70), schemagate as native_pdf (60), schemagate as ocr_pdf (10), schemagate as tabular (6), whole_schema as csv (6), whole_schema as pdf_text (70), whole_schema_sql as csv (6), whole_schema_sql as pdf_text (70)

Spreadsheets and CSV files:

| approach | case | rows | cells correct | wrong value stored | left blank | rejected | missing | truncated | ms | tokens in | tokens out | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| whole_schema | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 1611 | 5520 | 4281 | $0.0051 |
| whole_schema_sql | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 3258 | 5510 | 4856 | $0.0056 |
| one_table_text | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 1466 | 3381 | 4173 | $0.0043 |
| schemagate | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 389 | 786 | 206 | $0.0004 |
| whole_schema | tab01 | 42/200 | 21.0% | 0 | 0 | 0 | 1738 |  | 3048 | 13324 | 5005 | $0.0084 |
| whole_schema_sql | tab01 | 200/200 | 100.0% | 0 | 0 | 0 | 0 |  | 5857 | 13314 | 18905 | $0.0188 |
| one_table_text | tab01 | 200/200 | 100.0% | 1 | 0 | 0 | 0 |  | 6566 | 11185 | 16004 | $0.0159 |
| schemagate | tab01 | 200/200 | 100.0% | 0 | 0 | 0 | 0 |  | 405 | 790 | 223 | $0.0004 |
| whole_schema | tab02 | 0/200 | 0.0% | 0 | 0 | 0 | 2200 |  | 1624 | 13329 | 4230 | $0.0078 |
| whole_schema_sql | tab02 | 54/200 | 27.0% | 1 | 0 | 0 | 1606 |  | 2483 | 13319 | 5218 | $0.0086 |
| one_table_text | tab02 | 200/200 | 100.0% | 0 | 0 | 0 | 0 |  | 5959 | 11190 | 16122 | $0.0160 |
| schemagate | tab02 | 200/200 | 100.0% | 0 | 0 | 0 | 0 |  | 11 | 0 | 0 | unpriced |
| whole_schema | tab03 | 0/500 | 0.0% | 0 | 0 | 0 | 5500 | yes | 19400 | 29207 | 40000 | $0.0402 |
| whole_schema_sql | tab03 | 49/500 | 9.4% | 0 | 0 | 0 | 4983 |  | 2090 | 29197 | 5043 | $0.0140 |
| one_table_text | tab03 | 0/500 | 0.0% | 0 | 0 | 0 | 5500 | yes | 18589 | 27068 | 40000 | $0.0395 |
| schemagate | tab03 | 500/500 | 100.0% | 0 | 0 | 0 | 0 |  | 27 | 0 | 0 | unpriced |
| whole_schema | tab04 | 0/1000 | 0.0% | 0 | 0 | 0 | 11000 | yes | 22139 | 55767 | 40000 | $0.0495 |
| whole_schema_sql | tab04 | 418/1000 | 41.7% | 2 | 1 | 0 | 6413 | yes | 20825 | 55757 | 40000 | $0.0495 |
| one_table_text | tab04 | 0/1000 | 0.0% | 0 | 0 | 0 | 11000 |  | 667 | 53628 | 273 | $0.0190 |
| schemagate | tab04 | 1000/1000 | 100.0% | 0 | 0 | 0 | 0 |  | 42 | 0 | 0 | unpriced |
| whole_schema | tab05 | 0/2000 | 0.0% | 0 | 0 | 0 | 22000 |  | 4479 | 108054 | 348 | $0.0381 |
| whole_schema_sql | tab05 | 241/2000 | 8.8% | 107 | 5 | 0 | 19943 | yes | 16184 | 108044 | 22956 | $0.0550 |
| one_table_text | tab05 | 0/2000 | 0.0% | 0 | 0 | 0 | 22000 |  | 874 | 105915 | 269 | $0.0373 |
| schemagate | tab05 | 2000/2000 | 100.0% | 0 | 0 | 0 | 0 |  | 84 | 0 | 0 | unpriced |

Not attempted: 48 cases, unsupported: images

Errors:

- one_table_text tab03: JSONDecodeError: Expecting ',' delimiter: line 1 column 112238 (char 112237)
- whole_schema tab02: JSONDecodeError: Expecting ',' delimiter: line 1 column 11974 (char 11973)
- whole_schema tab03: JSONDecodeError: Invalid control character at: line 1 column 5637 (char 5636)
- whole_schema tab04: JSONDecodeError: Expecting ',' delimiter: line 453 column 161 (char 105972)

Why the database refused rows:

- whole_schema: not_null 9
- whole_schema_sql: not_null 7, unknown_column 2, syntax 2
- one_table_text: not_null 8
- schemagate: not_null 3

Examples of values stored wrong or left blank without anything flagging them:

- whole_schema:
  - inv04 subtotal: wanted '48878.99', stored Decimal('57504.70')
  - inv14 subtotal: wanted '35455.66', stored Decimal('39395.18')
  - inv15 subtotal: wanted '54172.93', stored Decimal('60192.15')
  - inv16 subtotal: wanted '51493.03', stored Decimal('57214.48')
  - inv20 subtotal: wanted '71348.06', stored Decimal('12590.83')
  - inv20 tax: wanted '15136.45', stored Decimal('2492.84')
  - inv20 issued_on: wanted '2026-08-26', stored datetime.date(2026, 8, 21)
  - inv24 tax: wanted '7009.12', stored Decimal('7010.12')
  - inv33 tax: wanted '9155.85', stored Decimal('1366.07')
  - inv37 subtotal: wanted '83424.56', stored Decimal('98146.54')
- whole_schema_sql:
  - inv05 shipping: wanted '0.00', stored Decimal('20501.44')
  - inv13 tax: wanted '4832.51', stored Decimal('4922.51')
  - inv14 subtotal: wanted '35455.66', stored Decimal('39395.18')
  - inv15 subtotal: wanted '54172.93', stored Decimal('60192.15')
  - inv20 subtotal: wanted '71348.06', stored Decimal('12643.61')
  - inv20 tax: wanted '15136.45', stored Decimal('2492.84')
  - inv20 issued_on: wanted '2026-08-26', stored datetime.date(2026, 8, 21)
  - inv21 subtotal: wanted '39288.57', stored Decimal('41356.39')
  - inv28 subtotal: wanted '39602.07', stored Decimal('44002.30')
  - inv28 tax: wanted '9281.17', stored Decimal('8709.46')
- one_table_text:
  - inv01 subtotal: wanted '30146.85', stored Decimal('35466.88')
  - inv02 tax: wanted '23131.21', stored Decimal('23531.21')
  - inv04 subtotal: wanted '48878.99', stored Decimal('57504.70')
  - inv05 shipping: wanted '0.00', stored Decimal('20501.44')
  - inv15 subtotal: wanted '54172.93', stored Decimal('60192.15')
  - inv16 subtotal: wanted '51493.03', stored Decimal('51492.03')
  - inv20 subtotal: wanted '71348.06', stored Decimal('12590.83')
  - inv20 tax: wanted '15136.45', stored Decimal('2492.84')
  - inv20 issued_on: wanted '2026-08-26', stored datetime.date(2026, 8, 21)
  - inv21 subtotal: wanted '39288.57', stored Decimal('41356.39')
- schemagate:
  - inv20 issued_on: wanted '2026-08-26', stored datetime.date(2026, 8, 21)
  - inv22 po_reference: wanted None, stored 'RE-20260193'
