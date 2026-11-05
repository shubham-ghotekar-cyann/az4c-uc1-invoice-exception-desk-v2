# AZ4C assignment data pack: use case 1, Invoice exception desk

This pack holds everything you need to build and test your system for this use case.

## What's inside

```
README.md        this file
USE_CASE.md      the use case on its own
COMMON.md        commands, DATA_DIR, result format, mock system
data/            ap.sqlite plus one CSV per table (10 files)
docs/            company documents as Markdown (69 files)
samples/         5 test cases and their expected answers (10 files)
schema/          result.schema.json, the exact result format
```

## The use case

**Who it's for:** the accounts payable manager at Kestrel Auto Components Pvt Ltd, a fictional auto-parts maker in Pune.

**The problem:** the team receives about 200 supplier invoices a day. About 15% don't match the purchase order or what was actually delivered. A clerk spends around 25 minutes on each one.

## What your system does

Takes one invoice, checks it against the purchase order, the goods received and the company's AP policy, and decides whether to pay it, hold it or escalate it. It then records the decision in the mock ERP.

## How a run starts

```json
{ "case_id": "UC1-S01", "input": { "invoice_id": "INV-24017" } }
```

## What you get

- **Tables** in `data/ap.sqlite` (also as CSV): vendors, items, purchase orders, goods receipts, invoices, payments.
- **Documents** in `docs/`:
  - `ap_policy.md`: the company's rules for checking invoices. Your system must follow it.
  - `vendor_agreements/`: price agreements signed with some vendors.
  - `vendor_emails/`: covering emails vendors sent with some invoices.

## What your system returns

- `decision`: `pay`, `hold` or `escalate`
- `reason_code`: `CLEAN_MATCH`, `QTY_SHORT`, `PRICE_VARIANCE`, `GST_MISMATCH`, `DUPLICATE`, `BANK_CHANGE_RISK`, `VENDOR_BLOCKED`, `MISSING_GRN` or `UNKNOWN_PO`
- `amount`: the invoice total
- `citations`: the invoice lines, PO, goods receipt and policy sections the decision rests on
- `needs_human`: see below

## Who approves (Parts B and C)

The AP manager approves every hold and escalation, and any payment over ₹5 lakh.

## What it writes

- A line in `mock_system/erp_payment_status.jsonl`: invoice ID, status, reason and time
- For a hold, an email to the vendor in `mock_system/outbox/<invoice_id>.md`

## How to load the data

Unzip the pack and set `DATA_DIR` to its folder.

```python
import os, sqlite3
con = sqlite3.connect(os.path.join(os.environ["DATA_DIR"], "data", "ap.sqlite"))
print(con.execute("SELECT * FROM invoices WHERE invoice_id = ?", ("INV-21834",)).fetchone())
```

The same tables are in `data/csv/` if you prefer CSV. Documents are Markdown files in `docs/`: the AP policy, signed vendor price agreements (one file per vendor), and vendors' covering emails (one file per invoice, where the vendor sent one).

## Samples and scoring

`samples/` holds 5 test cases. `samples/expected/` holds the expected answer for each. We score these fields: `decision`, `reason_code`, `amount` (within the tolerance shown), `key_numbers` where present (they must appear among the numbers in your result), the IDs in `must_cite` (your `citations` must include them) and `needs_human`. We run our own hidden cases the same way.

The exact result format is in `schema/result.schema.json`, and the shared conventions (commands, `DATA_DIR`, the mock system) are in `COMMON.md`.

*Synthetic and fictional data. All companies, people and figures are invented.*
