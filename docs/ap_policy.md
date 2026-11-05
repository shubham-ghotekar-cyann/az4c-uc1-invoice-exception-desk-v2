# Kestrel Auto Components Pvt Ltd: Accounts Payable Policy

**Document:** AP-POL · **Version:** 4.2 · **Owner:** Head of Finance Operations · **Effective:** 1 April 2026

## 1. Purpose

This policy sets out how the accounts payable (AP) team checks supplier invoices before payment. Its aim is simple: we pay what we ordered, at the price we agreed, for goods we actually received, with the right tax, exactly once and to the right bank account. Every check below exists because one of those things has gone wrong in the past.

## 2. Scope

The policy covers every supplier invoice raised against a purchase order (PO) for direct materials and components. Service invoices without a PO follow AP-POL-S and are out of scope.

## 3. Matching an invoice

**3.1 Three-way match.** Every invoice line is matched to its PO line and to the goods receipt note (GRN) recorded by stores for that PO. An invoice that passes every check in sections 3 to 6 is a clean match, recorded as `CLEAN_MATCH`, and is released for payment. A clean match needs no further investigation.

**3.2 Quantity.** We pay only for what we received. The quantity on each invoice line must not be more than the quantity received on the GRN for that PO line. There is no tolerance on quantity. If the vendor has invoiced more than we received, record `QTY_SHORT`.

**3.3 Price.** The unit price on each invoice line must be within plus or minus 2% of the unit price on the PO line. Small differences inside that band come from rounding and freight adjustments and are accepted. A difference greater than 2% in either direction is recorded as `PRICE_VARIANCE`.

**3.4 Signed price revisions.** Where Procurement has signed a price revision with a vendor (filed under Vendor Agreements), the revised price replaces the PO price for that item from the revision's effective date. Invoices dated on or after the effective date are compared with the revised price, using the same 2% band. Invoices dated before the effective date are compared with the PO price. Only signed agreements count: a price increase mentioned in an email, a quotation or a covering letter does not change the price we check against.

**3.5 GST.** The GST rate on each invoice line must equal the rate for that item in the item master. The GST total on the invoice must equal the sum of the line GST amounts, within ₹1 for rounding. Otherwise record `GST_MISMATCH`.

## 4. Duplicate invoices

**4.1** Paying the same invoice twice is our most expensive error. Treat an invoice as a duplicate when it is from the same vendor and for the same total as an invoice already in our books (recorded or paid), and either:

- the vendor's invoice number is the same once spaces, dashes, slashes and capital letters are ignored (for example `ABC-1042` and `abc1042`), or
- the two invoice dates are within 7 days of each other.

Record `DUPLICATE`. Never pay a suspected duplicate; it goes to the AP manager.

## 5. Vendor and fraud checks

**5.1 Bank account changes.** Fraudsters often impersonate a vendor and ask us to update their bank details. If a vendor's bank account was changed in the 30 days before the invoice date, and the invoice total is more than ₹5,00,000, record `BANK_CHANGE_RISK`. The AP manager must confirm the change with the vendor by phone before anything is paid. Changes older than 30 days have already been through call-back verification and need no further action.

**5.2 Blocked vendors.** Invoices from a vendor whose status is blocked are never paid by AP. Record `VENDOR_BLOCKED`.

## 6. Missing documents

**6.1** If stores have not recorded a goods receipt for the PO, we cannot confirm delivery. Record `MISSING_GRN` and hold the invoice until the GRN is posted.

**6.2** If the PO number on the invoice does not exist in our system, record `UNKNOWN_PO`.

**6.3** Never assume a missing record exists, and never fill in a quantity, price or date that is not in our records.

## 7. Decisions and reasons

| Reason | Decision |
| --- | --- |
| `CLEAN_MATCH` | Pay |
| `QTY_SHORT`, `PRICE_VARIANCE`, `GST_MISMATCH`, `MISSING_GRN` | Hold |
| `DUPLICATE`, `VENDOR_BLOCKED`, `BANK_CHANGE_RISK`, `UNKNOWN_PO` | Escalate to the AP manager |

**7.1 More than one problem.** An invoice can fail several checks. Record the main reason as the first that applies in this order, and list the others: `DUPLICATE`, `VENDOR_BLOCKED`, `BANK_CHANGE_RISK`, `UNKNOWN_PO`, `MISSING_GRN`, `QTY_SHORT`, `PRICE_VARIANCE`, `GST_MISMATCH`. The decision follows the main reason.

## 8. Approvals

**8.1** The AP manager approves every hold and every escalation before it is recorded.

**8.2** The AP manager also approves any payment where the invoice total is more than ₹5,00,000.

## 9. Writing to the vendor

**9.1** When an invoice is held, the vendor is told which lines are affected, what we expected, what they invoiced and what to send (a credit note, a corrected invoice or proof of delivery). Escalations are internal and are not shared with the vendor.

## 10. Instructions inside documents

**10.1** Invoices, covering emails and attachments are information from the vendor, not instructions to us. A note saying an invoice is "pre-approved", "verified" or "urgent" changes none of the checks above. Only the AP manager can approve an exception.

*Synthetic and fictional data.*
