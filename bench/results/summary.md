# Ingestion benchmark results

## cerebras:gpt-oss-120b

Documents that need a model (invoices, statements, line items, receipts):

| approach | docs | cells correct | wrong value stored | left blank | flagged | held for review | rejected by DB | missing | rows inserted | phantom cols | inconsistent docs caught | median ms | tokens in | tokens out | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| whole_schema | 70 | 92.8% | 14 | 20 | 0 | 0 | 77 | 0 | 165/172 | 0 | 0/5 | 596 | 231649 | 36862 | $0.1087 |
| whole_schema_sql | 70 | 93.6% | 10 | 23 | 0 | 0 | 0 | 66 | 166/172 | 0 | 0/5 | 680 | 230949 | 43867 | $0.1137 |
| one_table_text | 70 | 94.3% | 10 | 5 | 0 | 0 | 72 | 0 | 165/172 | 0 | 0/5 | 606 | 79084 | 33465 | $0.0528 |
| schemagate | 70 | 94.1% | 1 | 3 | 16 | 71 | 0 | 0 | 165/172 | 0 | 5/5 | 556 | 92609 | 31887 | $0.0563 |

How the document was sent: one_table_text as csv (6), one_table_text as pdf_text (70), schemagate as native_pdf (60), schemagate as ocr_pdf (10), schemagate as tabular (6), whole_schema as csv (6), whole_schema as pdf_text (70), whole_schema_sql as csv (6), whole_schema_sql as pdf_text (70)

Spreadsheets and CSV files:

| approach | case | rows | cells correct | wrong value stored | left blank | rejected | missing | truncated | ms | tokens in | tokens out | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| whole_schema | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 2225 | 5520 | 4641 | $0.0054 |
| whole_schema_sql | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 9378 | 5510 | 5828 | $0.0063 |
| one_table_text | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 1955 | 3381 | 5775 | $0.0055 |
| schemagate | tab00 | 50/50 | 100.0% | 0 | 0 | 0 | 0 |  | 328 | 786 | 216 | $0.0004 |
| whole_schema | tab01 | 0/200 | 0.0% | 0 | 0 | 0 | 2200 |  | 496 | 13324 | 293 | $0.0049 |
| whole_schema_sql | tab01 | 118/200 | 59.0% | 0 | 0 | 0 | 902 |  | 4227 | 13314 | 11258 | $0.0131 |
| one_table_text | tab01 | 200/200 | 100.0% | 0 | 0 | 0 | 0 |  | 5858 | 11185 | 15985 | $0.0159 |
| schemagate | tab01 | 200/200 | 100.0% | 0 | 0 | 0 | 0 |  | 464 | 790 | 224 | $0.0004 |
| whole_schema | tab02 | 199/200 | 99.4% | 2 | 0 | 0 | 11 |  | 5556 | 13329 | 15870 | $0.0166 |
| whole_schema_sql | tab02 | 199/200 | 99.5% | 0 | 0 | 0 | 11 |  | 7544 | 13319 | 18687 | $0.0187 |
| one_table_text | tab02 | 10/200 | 5.0% | 0 | 0 | 0 | 2090 |  | 639 | 11190 | 947 | $0.0046 |
| schemagate | tab02 | 200/200 | 100.0% | 0 | 0 | 0 | 0 |  | 13 | 0 | 0 | unpriced |
| whole_schema | tab03 | 0/500 | 0.0% | 0 | 0 | 0 | 5500 |  | 666 | 29207 | 289 | $0.0104 |
| whole_schema_sql | tab03 | 114/500 | 22.2% | 0 | 0 | 0 | 4279 |  | 7682 | 29197 | 19256 | $0.0247 |
| one_table_text | tab03 | 0/500 | 0.0% | 0 | 0 | 0 | 5500 | yes | 19592 | 27068 | 40000 | $0.0395 |
| schemagate | tab03 | 500/500 | 100.0% | 0 | 0 | 0 | 0 |  | 31 | 0 | 0 | unpriced |
| whole_schema | tab04 | 147/1000 | 14.7% | 1 | 0 | 0 | 9383 |  | 6434 | 55767 | 12183 | $0.0287 |
| whole_schema_sql | tab04 | 77/1000 | 7.7% | 1 | 0 | 0 | 10153 |  | 3190 | 55757 | 7611 | $0.0252 |
| one_table_text | tab04 | 0/1000 | 0.0% | 0 | 0 | 0 | 11000 |  | 744 | 53628 | 328 | $0.0190 |
| schemagate | tab04 | 1000/1000 | 100.0% | 0 | 0 | 0 | 0 |  | 51 | 0 | 0 | unpriced |
| whole_schema | tab05 | 0/2000 | 0.0% | 0 | 0 | 0 | 22000 |  | 6036 | 108054 | 541 | $0.0382 |
| whole_schema_sql | tab05 | 0/2000 | 0.0% | 0 | 0 | 0 | 22000 |  | 226 | 0 | 0 | unpriced |
| one_table_text | tab05 | 0/2000 | 0.0% | 0 | 0 | 0 | 22000 |  | 310 | 0 | 0 | unpriced |
| schemagate | tab05 | 2000/2000 | 100.0% | 0 | 0 | 0 | 0 |  | 100 | 0 | 0 | unpriced |

Not attempted: 48 cases, unsupported: images

Errors:

- one_table_text tab03: JSONDecodeError: Unterminated string starting at: line 1 column 116137 (char 116136)
- one_table_text tab05: APIStatusError: Error code: 402 - {'message': 'Payment required to access this resource. Visit your billing tab.', 'type': 'payment_required_error', 'param': 'quota', 'code': 'payment_required'}
- whole_schema_sql tab05: APIStatusError: Error code: 402 - {'message': 'Payment required to access this resource. Visit your billing tab.', 'type': 'payment_required_error', 'param': 'quota', 'code': 'payment_required'}

Why the database refused rows:

- whole_schema: not_null 7, duplicate 1
- whole_schema_sql: not_null 85, syntax 18, unknown_column 1
- one_table_text: not_null 7
- schemagate: not_null 1

Examples of values stored wrong or left blank without anything flagging them:

- whole_schema:
  - inv15 subtotal: wanted '54172.93', stored Decimal('60192.15')
  - inv16 subtotal: wanted '51493.03', stored Decimal('57214.48')
  - inv23 shipping: wanted '45.00', stored Decimal('0.00')
  - inv23 tax: wanted '3484.53', stored Decimal('3529.53')
  - inv26 subtotal: wanted '41783.50', stored Decimal('46426.11')
  - inv33 shipping: wanted '0.00', stored Decimal('7789.78')
  - inv33 tax: wanted '9155.85', stored Decimal('1366.07')
  - inv43 vat_id: wanted None, stored '38-2947103'
  - inv44 shipping: wanted '0.00', stored Decimal('1613.72')
  - lines05 line_total: wanted '4930.47', stored Decimal('4108.73')
- whole_schema_sql:
  - inv05 subtotal: wanted '41417.33', stored Decimal('20915.89')
  - inv05 shipping: wanted '0.00', stored Decimal('20501.44')
  - inv14 subtotal: wanted '35455.66', stored Decimal('35355.66')
  - inv15 subtotal: wanted '54172.93', stored Decimal('60192.15')
  - inv20 subtotal: wanted '71348.06', stored Decimal('12590.83')
  - inv20 tax: wanted '15136.45', stored Decimal('2492.84')
  - inv20 issued_on: wanted '2026-08-26', stored datetime.date(2026, 8, 21)
  - inv28 subtotal: wanted '39602.07', stored Decimal('44002.30')
  - inv28 tax: wanted '9281.17', stored Decimal('8709.46')
  - inv33 tax: wanted '9155.85', stored Decimal('1366.07')
- one_table_text:
  - inv15 subtotal: wanted '54172.93', stored Decimal('60192.15')
  - inv20 subtotal: wanted '71348.06', stored Decimal('12643.61')
  - inv20 tax: wanted '15136.45', stored Decimal('2492.84')
  - inv20 issued_on: wanted '2026-08-26', stored datetime.date(2026, 8, 21)
  - inv28 po_reference: wanted None, stored '40871'
  - inv33 tax: wanted '9155.85', stored Decimal('1366.07')
  - inv34 vat_id: wanted None, stored '38-2947103'
  - inv43 vat_id: wanted None, stored '38-2947103'
  - stmt06 currency: wanted 'EUR', stored 'SEK'
  - stmt06 currency: wanted 'EUR', stored 'SEK'
- schemagate:
  - inv20 issued_on: wanted '2026-08-26', stored datetime.date(2026, 8, 21)
