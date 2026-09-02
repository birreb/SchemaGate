# Ingestion benchmark results

## cerebras:gpt-oss-120b

Documents that need a model (invoices, statements, line items, receipts):

| approach | docs | cells correct | wrong value stored | left blank | flagged | held for review | rejected by DB | missing | rows inserted | phantom cols | inconsistent docs caught | median ms | tokens in | tokens out | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| whole_schema | 70 | 88.2% | 39 | 55 | 0 | 0 | 76 | 0 | 164/172 | 0 | 0/5 | 528 | 222351 | 36803 | $0.1054 |
| whole_schema_sql | 70 | 88.9% | 51 | 39 | 0 | 0 | 0 | 70 | 165/172 | 0 | 0/5 | 540 | 221651 | 41264 | $0.1085 |
| one_table_text | 70 | 87.2% | 44 | 59 | 0 | 0 | 82 | 0 | 163/172 | 0 | 0/5 | 492 | 73116 | 33820 | $0.0510 |
| schemagate | 70 | 91.8% | 5 | 25 | 40 | 29 | 0 | 20 | 166/172 | 0 | 5/5 | 492 | 86331 | 32295 | $0.0544 |

How the document was sent: one_table_text as csv (6), one_table_text as pdf_text (70), schemagate as native_pdf (60), schemagate as ocr_pdf (10), schemagate as tabular (6), whole_schema as csv (6), whole_schema as pdf_text (70), whole_schema_sql as csv (6), whole_schema_sql as pdf_text (70)

Spreadsheets and CSV files:

| approach | case | rows | cells correct | wrong value stored | left blank | rejected | missing | truncated | ms | tokens in | tokens out | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| whole_schema | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 2342 | 5402 | 6689 | $0.0069 |
| whole_schema_sql | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 2983 | 5392 | 6974 | $0.0071 |
| one_table_text | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 1743 | 3302 | 4265 | $0.0044 |
| schemagate | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 412 | 724 | 209 | $0.0004 |
| whole_schema | tab01 | 200/200 | 100.0% | 1 | 0 | 0 | 0 |  | 6515 | 13206 | 15962 | $0.0166 |
| whole_schema_sql | tab01 | 199/200 | 99.5% | 0 | 0 | 0 | 10 |  | 7299 | 13196 | 18857 | $0.0188 |
| one_table_text | tab01 | 200/200 | 100.0% | 1 | 0 | 0 | 0 |  | 4995 | 11106 | 15963 | $0.0159 |
| schemagate | tab01 | 200/200 | 100.0% | 0 | 0 | 0 | 0 |  | 468 | 728 | 209 | $0.0004 |
| whole_schema | tab02 | 200/200 | 100.0% | 1 | 0 | 0 | 0 |  | 6692 | 13211 | 16030 | $0.0166 |
| whole_schema_sql | tab02 | 200/200 | 99.8% | 4 | 0 | 0 | 0 |  | 5892 | 13201 | 18777 | $0.0187 |
| one_table_text | tab02 | 20/200 | 10.0% | 0 | 0 | 0 | 1800 |  | 1323 | 11111 | 1690 | $0.0052 |
| schemagate | tab02 | 200/200 | 100.0% | 0 | 0 | 0 | 0 |  | 11 | 0 | 0 | unpriced |
| whole_schema | tab03 | 0/500 | 0.0% | 0 | 0 | 0 | 5000 |  | 20688 | 29089 | 39982 | $0.0402 |
| whole_schema_sql | tab03 | 63/500 | 12.2% | 0 | 0 | 0 | 4390 |  | 2628 | 29079 | 6236 | $0.0149 |
| one_table_text | tab03 | 0/500 | 0.0% | 0 | 0 | 0 | 5000 |  | 565 | 26989 | 257 | $0.0096 |
| schemagate | tab03 | 500/500 | 100.0% | 0 | 0 | 0 | 0 |  | 28 | 0 | 0 | unpriced |
| whole_schema | tab04 | 0/1000 | 0.0% | 0 | 0 | 0 | 10000 |  | 795 | 55650 | 400 | $0.0198 |
| whole_schema_sql | tab04 | 194/1000 | 8.2% | 77 | 8 | 0 | 9100 |  | 8461 | 55640 | 18660 | $0.0335 |
| one_table_text | tab04 | 300/1000 | 29.9% | 2 | 0 | 0 | 7010 |  | 12456 | 53550 | 25255 | $0.0377 |
| schemagate | tab04 | 1000/1000 | 100.0% | 0 | 0 | 0 | 0 |  | 44 | 0 | 0 | unpriced |
| whole_schema | tab05 | 3/2000 | 0.1% | 0 | 0 | 0 | 19970 |  | 5187 | 107938 | 660 | $0.0383 |
| whole_schema_sql | tab05 | 181/2000 | 6.9% | 46 | 4 | 0 | 18570 |  | 11868 | 107928 | 17878 | $0.0512 |
| one_table_text | tab05 | 10/2000 | 0.5% | 0 | 0 | 0 | 19900 |  | 2137 | 105838 | 1576 | $0.0382 |
| schemagate | tab05 | 2000/2000 | 100.0% | 0 | 0 | 0 | 0 |  | 86 | 0 | 0 | unpriced |

Not attempted: 48 cases, unsupported: images

Errors:

- whole_schema tab03: JSONDecodeError: Expecting ',' delimiter: line 1 column 99030 (char 99029)

Why the database refused rows:

- whole_schema: not_null 8
- whole_schema_sql: not_null 7, syntax 4, duplicate 2
- one_table_text: not_null 9, duplicate 1
- schemagate: not_null 4

Examples of values stored wrong or left blank without anything flagging them:

- whole_schema:
  - inv01 subtotal: wanted '30295.85', stored Decimal('35466.88')
  - inv02 tax: wanted '23131.21', stored Decimal('23531.21')
  - inv07 subtotal: wanted '22378.10', stored Decimal('22128.10')
  - inv09 subtotal: wanted '85413.93', stored Decimal('85264.93')
  - inv10 subtotal: wanted '20433.33', stored Decimal('20338.33')
  - inv14 subtotal: wanted '35455.66', stored Decimal('39395.18')
  - inv15 subtotal: wanted '54172.93', stored Decimal('60192.15')
  - inv16 subtotal: wanted '51743.03', stored Decimal('57214.48')
  - inv20 subtotal: wanted '71348.06', stored Decimal('83938.89')
  - inv20 tax: wanted '15136.45', stored Decimal('2492.84')
- whole_schema_sql:
  - inv01 subtotal: wanted '30295.85', stored Decimal('35466.88')
  - inv02 tax: wanted '23131.21', stored Decimal('23531.21')
  - inv04 subtotal: wanted '48878.99', stored Decimal('57504.70')
  - inv07 subtotal: wanted '22378.10', stored Decimal('22128.10')
  - inv09 subtotal: wanted '85413.93', stored Decimal('85264.93')
  - inv10 subtotal: wanted '20433.33', stored Decimal('20338.33')
  - inv14 subtotal: wanted '35455.66', stored Decimal('39395.18')
  - inv15 subtotal: wanted '54172.93', stored Decimal('60192.15')
  - inv16 subtotal: wanted '51743.03', stored Decimal('57214.48')
  - inv20 subtotal: wanted '71348.06', stored Decimal('83938.89')
- one_table_text:
  - inv01 subtotal: wanted '30295.85', stored Decimal('35466.88')
  - inv07 subtotal: wanted '22378.10', stored Decimal('22128.10')
  - inv08 subtotal: wanted '71038.12', stored Decimal('83574.26')
  - inv09 subtotal: wanted '85413.93', stored Decimal('85264.93')
  - inv09 tax: wanted '18418.20', stored Decimal('18218.20')
  - inv10 subtotal: wanted '20433.33', stored Decimal('20338.33')
  - inv14 subtotal: wanted '35455.66', stored Decimal('39395.18')
  - inv15 subtotal: wanted '54172.93', stored Decimal('60192.15')
  - inv16 subtotal: wanted '51743.03', stored Decimal('51493.03')
  - inv17 currency: wanted 'NOK', stored 'SEK'
- schemagate:
  - inv20 issued_on: wanted '2026-08-26', stored datetime.date(2026, 8, 21)
  - stmt06 subtotal: wanted '36247.51', stored Decimal('41727.67')
  - stmt06 tax: wanted '5480.16', stored Decimal('0.00')
  - stmt06 subtotal: wanted '60915.87', stored Decimal('68413.65')
  - stmt06 tax: wanted '7497.78', stored Decimal('0.00')
