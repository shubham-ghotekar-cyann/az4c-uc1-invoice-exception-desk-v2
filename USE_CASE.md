# Use case 1: Invoice exception desk

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

*Synthetic and fictional data.*
