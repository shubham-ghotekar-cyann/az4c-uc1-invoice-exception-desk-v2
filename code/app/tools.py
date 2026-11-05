"""Deterministic tools. All arithmetic and every policy test lives here; agents only reason over the output.

Each check returns a ToolResult: pass / fail / not_applicable, the findings (reason codes) it raised,
the numbers it used and the record IDs it read. Tools never raise on missing data: they report it
(AP-POL §6.3: never assume a missing record exists).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from . import config
from .data import Repo

ReasonCode = Literal["CLEAN_MATCH", "QTY_SHORT", "PRICE_VARIANCE", "GST_MISMATCH", "DUPLICATE",
                     "BANK_CHANGE_RISK", "VENDOR_BLOCKED", "MISSING_GRN", "UNKNOWN_PO"]

POLICY = {  # reason code -> AP policy section it comes from
    "CLEAN_MATCH": "AP-POL-3.1", "QTY_SHORT": "AP-POL-3.2", "PRICE_VARIANCE": "AP-POL-3.3",
    "GST_MISMATCH": "AP-POL-3.5", "DUPLICATE": "AP-POL-4.1", "BANK_CHANGE_RISK": "AP-POL-5.1",
    "VENDOR_BLOCKED": "AP-POL-5.2", "MISSING_GRN": "AP-POL-6.1", "UNKNOWN_PO": "AP-POL-6.2",
}


class Finding(BaseModel):
    code: ReasonCode
    line_id: str = ""  # "" for an invoice-level finding
    detail: str
    numbers: dict[str, float] = Field(default_factory=dict)
    citations: list[str] = Field(default_factory=list)


class ToolResult(BaseModel):
    tool: str
    status: Literal["pass", "fail", "not_applicable"]
    findings: list[Finding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    data: dict = Field(default_factory=dict)

    def numbers(self) -> set[float]:
        """Every number this tool produced, for checking an agent did not invent one."""
        out: set[float] = set()
        for f in self.findings:
            out.update(round(float(v), 2) for v in f.numbers.values())
        _collect_numbers(self.data, out)
        return out


def _collect_numbers(obj, out: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(round(float(obj), 2))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)


def _d(s: str) -> date:
    return date.fromisoformat(s)


def normalise_invoice_no(s: str) -> str:
    """AP-POL §4.1: ignore spaces, dashes, slashes and capital letters."""
    return re.sub(r"[\s\-/]", "", s or "").lower()


class InvoiceContext:
    """Everything the tools need about one invoice, read once."""

    def __init__(self, repo: Repo, invoice_id: str):
        self.repo = repo
        self.invoice = repo.invoice(invoice_id)
        self.invoice_id = invoice_id
        self.lines = repo.invoice_lines(invoice_id)
        self.vendor = repo.vendor(self.invoice["vendor_id"])
        self.po = repo.purchase_order(self.invoice["po_id"])
        self.po_lines = repo.po_lines(self.invoice["po_id"]) if self.po else {}
        self.grns = repo.goods_receipts(self.invoice["po_id"]) if self.po else []
        self.received = repo.received_qty(self.invoice["po_id"]) if self.grns else {}
        self.invoice_date = _d(self.invoice["invoice_date"])
        self.total = float(self.invoice["total"])

    @property
    def grn_ids(self) -> list[str]:
        return [g["grn_id"] for g in self.grns]


# ---------------------------------------------------------------- matching tools
def check_documents(ctx: InvoiceContext) -> ToolResult:
    """AP-POL §6.1/§6.2: the PO must exist and stores must have posted a GRN for it."""
    po_id = ctx.invoice["po_id"]
    if ctx.po is None:
        return ToolResult(tool="check_documents", status="fail", citations=[ctx.invoice_id], data={"po_exists": False},
                          findings=[Finding(code="UNKNOWN_PO", detail=f"PO {po_id} on the invoice does not exist",
                                            citations=[ctx.invoice_id, POLICY["UNKNOWN_PO"]])])
    if not ctx.grns:
        return ToolResult(tool="check_documents", status="fail", citations=[po_id],
                          data={"po_exists": True, "grn_ids": []},
                          findings=[Finding(code="MISSING_GRN", detail=f"no goods receipt recorded for {po_id}",
                                            citations=[po_id, POLICY["MISSING_GRN"]])])
    return ToolResult(tool="check_documents", status="pass", citations=[po_id, *ctx.grn_ids],
                      data={"po_exists": True, "grn_ids": ctx.grn_ids})


def check_quantity(ctx: InvoiceContext) -> ToolResult:
    """AP-POL §3.2: invoiced qty must not exceed qty received on the GRN for that PO line. No tolerance."""
    if ctx.po is None or not ctx.grns:
        return ToolResult(tool="check_quantity", status="not_applicable",
                          notes=["no PO or no GRN: received quantity cannot be compared"])
    findings, rows = [], []
    for ln in ctx.lines:
        received = ctx.received.get(ln["po_line_no"], 0)
        ordered = ctx.po_lines.get(ln["po_line_no"], {}).get("qty")
        rows.append({"line_id": ln["line_id"], "qty_invoiced": ln["qty"], "qty_received": received,
                     "qty_ordered": ordered})
        if ln["qty"] > received:
            short = ln["qty"] - received
            findings.append(Finding(
                code="QTY_SHORT", line_id=ln["line_id"],
                detail=f"invoiced {ln['qty']} but received {received} (over-billed by {short})",
                numbers={"qty_invoiced": ln["qty"], "qty_received": received, "qty_over_billed": short},
                citations=[ln["line_id"], *ctx.grn_ids, POLICY["QTY_SHORT"]]))
    return ToolResult(tool="check_quantity", status="fail" if findings else "pass", findings=findings,
                      citations=[*ctx.grn_ids], data={"lines": rows})


def reference_price(ctx: InvoiceContext, line: dict) -> tuple[float | None, str, str | None]:
    """AP-POL §3.4: a signed revision replaces the PO price from its effective date (invoice date decides)."""
    po_line = ctx.po_lines.get(line["po_line_no"])
    po_price = float(po_line["unit_price"]) if po_line else None
    for a in ctx.repo.agreements:
        if a.vendor_id == ctx.invoice["vendor_id"] and a.item_code == line["item_code"] and a.signed:
            in_force = ctx.invoice_date >= a.effective_from and (a.valid_until is None or ctx.invoice_date <= a.valid_until)
            if in_force:
                return a.revised_price, a.agreement_id, a.agreement_id
            return po_price, ctx.invoice["po_id"], a.agreement_id  # agreement exists but not in force
    return po_price, ctx.invoice["po_id"], None


def check_price(ctx: InvoiceContext) -> ToolResult:
    """AP-POL §3.3: unit price within ±2% (inclusive) of the reference price."""
    if ctx.po is None:
        return ToolResult(tool="check_price", status="not_applicable", notes=["PO does not exist: no reference price"])
    findings, rows, notes, cites = [], [], [], [ctx.invoice["po_id"]]
    for ln in ctx.lines:
        ref, source, agreement = reference_price(ctx, ln)
        if ref is None:
            notes.append(f"{ln['line_id']}: PO line {ln['po_line_no']} not found, price not checked")
            continue
        dev = round((float(ln["unit_price"]) - ref) / ref * 100, 2)
        row = {"line_id": ln["line_id"], "unit_price_invoiced": ln["unit_price"], "unit_price_reference": ref,
               "reference_source": source, "price_deviation_pct": dev}
        if agreement:
            row["agreement_id"] = agreement
            row["agreement_in_force"] = source == agreement
            cites.append(agreement)
            if source == agreement:
                cites += [ln["line_id"], "AP-POL-3.4"]  # the line priced at the revised rate rests on the agreement
            else:
                notes.append(f"{ln['line_id']}: {agreement} exists but is not in force on {ctx.invoice_date}; PO price used")
        rows.append(row)
        if abs(dev) > config.PRICE_BAND_PCT + 1e-9:
            findings.append(Finding(
                code="PRICE_VARIANCE", line_id=ln["line_id"],
                detail=f"unit price {ln['unit_price']} vs reference {ref} ({source}): {dev:+.2f}%",
                numbers={"unit_price_invoiced": ln["unit_price"], "unit_price_reference": ref,
                         "price_deviation_pct": dev,
                         "price_difference": round(float(ln["unit_price"]) - ref, 2)},
                citations=[ln["line_id"], source, POLICY["PRICE_VARIANCE"]] + (["AP-POL-3.4"] if agreement else [])))
    return ToolResult(tool="check_price", status="fail" if findings else "pass", findings=findings,
                      notes=notes, citations=list(dict.fromkeys(cites)), data={"lines": rows})


def check_gst(ctx: InvoiceContext) -> ToolResult:
    """AP-POL §3.5: line GST rate = item master rate; header GST = sum of line GST within ₹1."""
    findings, rows = [], []
    for ln in ctx.lines:
        item = ctx.repo.item(ln["item_code"])
        master = item["gst_rate"] if item else None
        rows.append({"line_id": ln["line_id"], "gst_rate_invoiced": ln["gst_rate"], "gst_rate_master": master})
        if master is None or ln["gst_rate"] != master:
            why = "item not in item master" if master is None else f"billed {ln['gst_rate']}% but item master says {master}%"
            findings.append(Finding(code="GST_MISMATCH", line_id=ln["line_id"], detail=why,
                                    numbers={"gst_rate_invoiced": ln["gst_rate"], **({"gst_rate_master": master} if master is not None else {})},
                                    citations=[ln["line_id"], ln["item_code"], POLICY["GST_MISMATCH"]]))
    line_sum = round(sum(float(ln["gst_amount"]) for ln in ctx.lines), 2)
    header = float(ctx.invoice["gst_amount"])
    diff = round(header - line_sum, 2)
    if abs(diff) > config.GST_HEADER_TOLERANCE + 1e-9:
        findings.append(Finding(code="GST_MISMATCH", detail=f"header GST {header} vs sum of lines {line_sum}",
                                numbers={"gst_header": header, "gst_lines_sum": line_sum, "gst_difference": diff},
                                citations=[ctx.invoice_id, POLICY["GST_MISMATCH"]]))
    return ToolResult(tool="check_gst", status="fail" if findings else "pass", findings=findings,
                      citations=[ctx.invoice_id],
                      data={"lines": rows, "gst_header": header, "gst_lines_sum": line_sum})


# ---------------------------------------------------------------- vendor risk tools
def check_vendor_status(ctx: InvoiceContext) -> ToolResult:
    """AP-POL §5.2: invoices from a blocked vendor are never paid."""
    vid = ctx.invoice["vendor_id"]
    if ctx.vendor is None:
        return ToolResult(tool="check_vendor_status", status="fail", notes=[f"vendor {vid} not in vendor master"],
                          findings=[Finding(code="VENDOR_BLOCKED", detail=f"vendor {vid} not found in vendor master",
                                            citations=[vid, POLICY["VENDOR_BLOCKED"]])])
    status = (ctx.vendor["status"] or "").strip().lower()
    data = {"vendor_id": vid, "vendor_status": status}
    if status == "blocked":
        return ToolResult(tool="check_vendor_status", status="fail", citations=[vid], data=data,
                          findings=[Finding(code="VENDOR_BLOCKED", detail=f"vendor {vid} status is blocked",
                                            citations=[vid, POLICY["VENDOR_BLOCKED"]])])
    notes = [] if status == "active" else [f"vendor status '{status}' is neither active nor blocked"]
    return ToolResult(tool="check_vendor_status", status="pass", citations=[vid], data=data, notes=notes)


def bank_change_days(ctx: InvoiceContext) -> int | None:
    changed = (ctx.vendor or {}).get("bank_account_changed_on")
    return (ctx.invoice_date - _d(changed)).days if changed else None


def check_bank_change(ctx: InvoiceContext) -> ToolResult:
    """AP-POL §5.1: bank account changed 0..30 days before the invoice date AND total > ₹5,00,000."""
    vid = ctx.invoice["vendor_id"]
    days = bank_change_days(ctx)
    if days is None:
        return ToolResult(tool="check_bank_change", status="pass", citations=[vid], notes=["no bank change on record"])
    data = {"bank_account_changed_on": ctx.vendor["bank_account_changed_on"], "days_before_invoice": days,
            "invoice_total": ctx.total, "threshold": config.APPROVAL_THRESHOLD}
    in_window = 0 <= days <= config.BANK_CHANGE_WINDOW_DAYS
    notes = []
    if days < 0:
        notes.append(f"bank account changed {-days} days after the invoice date; §5.1 covers changes before it")
    if in_window and ctx.total > config.APPROVAL_THRESHOLD:
        return ToolResult(tool="check_bank_change", status="fail", citations=[vid], data=data, findings=[Finding(
            code="BANK_CHANGE_RISK", detail=f"bank account changed {days} days before the invoice; total {ctx.total} > 500000",
            numbers={"days_before_invoice": days, "invoice_total": ctx.total},
            citations=[vid, ctx.invoice_id, POLICY["BANK_CHANGE_RISK"]])])
    if in_window:
        notes.append(f"bank account changed {days} days before the invoice, but total {ctx.total} is not above 500000")
    return ToolResult(tool="check_bank_change", status="pass", citations=[vid], data=data, notes=notes)


def duplicate_candidates(ctx: InvoiceContext) -> list[dict]:
    """Other invoices from the same vendor for the same total (cheap pre-screen for the orchestrator)."""
    return [o for o in ctx.repo.vendor_invoices(ctx.invoice["vendor_id"])
            if o["invoice_id"] != ctx.invoice_id and abs(float(o["total"]) - ctx.total) < 0.005]


def check_duplicates(ctx: InvoiceContext) -> ToolResult:
    """AP-POL §4.1: same vendor + same total as an EARLIER invoice in our books, and same normalised
    vendor invoice number or invoice dates within 7 days. The earlier invoice is the original."""
    me = ctx.invoice
    key = (me["invoice_date"], me["invoice_id"])
    findings, notes = [], []
    for o in duplicate_candidates(ctx):
        same_no = normalise_invoice_no(o["vendor_invoice_no"]) == normalise_invoice_no(me["vendor_invoice_no"])
        gap = abs((_d(o["invoice_date"]) - ctx.invoice_date).days)
        if not (same_no or gap <= config.DUPLICATE_DATE_WINDOW_DAYS):
            continue
        if (o["invoice_date"], o["invoice_id"]) >= key:
            notes.append(f"{o['invoice_id']} matches but is later; it would be the duplicate of this one")
            continue
        pays = ctx.repo.payments_for(o["invoice_id"])
        why = f"same vendor invoice number ({o['vendor_invoice_no']} vs {me['vendor_invoice_no']})" if same_no \
            else f"invoice dates {gap} days apart"
        findings.append(Finding(
            code="DUPLICATE",
            detail=f"same vendor and total {ctx.total} as {o['invoice_id']} dated {o['invoice_date']}"
                   f"{' (paid ' + pays[0]['payment_id'] + ')' if pays else ' (recorded, unpaid)'}; {why}",
            numbers={"invoice_total": ctx.total, "days_apart": gap},
            citations=[o["invoice_id"], *[p["payment_id"] for p in pays], POLICY["DUPLICATE"]]))
    own = ctx.repo.payments_for(ctx.invoice_id)
    if own:
        notes.append(f"this invoice already has payment record {own[0]['payment_id']} (does not change the decision)")
    return ToolResult(tool="check_duplicates", status="fail" if findings else "pass", findings=findings, notes=notes,
                      citations=[f.citations[0] for f in findings])


# ---------------------------------------------------------------- document tools
INSTRUCTION_PATTERNS = [
    r"\bpre-?approved\b", r"\bapprove(d)? for payment\b", r"\bskip\b.*\bcheck", r"\bignore\b",
    r"\bmark (it )?as paid\b", r"\b(AP|AI) (agent|automation|system|bot)\b", r"\bnote to\b",
    r"\boverride\b", r"\bverified by\b", r"\burgent\b", r"\bchange (of |our )?bank\b", r"\bnew bank account\b",
]


def scan_for_instructions(text: str) -> list[str]:
    """Deterministic backstop: phrases that try to instruct the system. Hits are data, never obeyed."""
    hits = []
    for pat in INSTRUCTION_PATTERNS:
        for m in re.finditer(pat, text, re.I):
            start, end = max(0, m.start() - 40), min(len(text), m.end() + 40)
            hits.append(text[start:end].replace("\n", " ").strip())
    return list(dict.fromkeys(hits))


def read_vendor_email(ctx: InvoiceContext) -> ToolResult:
    """The vendor's covering email for this invoice, if any. UNTRUSTED text."""
    text = ctx.repo.vendor_email(ctx.invoice_id)
    if text is None:
        return ToolResult(tool="read_vendor_email", status="not_applicable", notes=["no covering email"])
    body = re.sub(r"\*Synthetic and fictional data\.\*", "", text).strip()
    return ToolResult(tool="read_vendor_email", status="pass", citations=[f"EMAIL-{ctx.invoice_id}"],
                      data={"text": body, "instruction_like_phrases": scan_for_instructions(body)})


def read_free_text(ctx: InvoiceContext) -> ToolResult:
    """Free-text invoice fields that differ from master data (line descriptions). UNTRUSTED text."""
    odd = []
    for ln in ctx.lines:
        item = ctx.repo.item(ln["item_code"])
        if item and (ln["description"] or "").strip() != (item["description"] or "").strip():
            odd.append({"line_id": ln["line_id"], "description": ln["description"],
                        "item_master_description": item["description"],
                        "instruction_like_phrases": scan_for_instructions(ln["description"] or "")})
    if not odd:
        return ToolResult(tool="read_free_text", status="not_applicable", notes=["all line descriptions match item master"])
    return ToolResult(tool="read_free_text", status="pass", citations=[o["line_id"] for o in odd], data={"fields": odd})


# ---------------------------------------------------------------- summary for the orchestrator
def invoice_summary(ctx: InvoiceContext) -> dict:
    """Facts the orchestrator plans from. Lookups only: no policy judgement is made here."""
    v = ctx.vendor or {}
    days = bank_change_days(ctx)
    free = read_free_text(ctx)
    return {
        "invoice_id": ctx.invoice_id,
        "vendor_invoice_no": ctx.invoice["vendor_invoice_no"],
        "invoice_date": ctx.invoice["invoice_date"],
        "total": ctx.total,
        "line_count": len(ctx.lines),
        "po_id": ctx.invoice["po_id"],
        "po_exists": ctx.po is not None,
        "grn_ids": ctx.grn_ids,
        "vendor_id": ctx.invoice["vendor_id"],
        "vendor_status": v.get("status"),
        "vendor_bank_changed_on": v.get("bank_account_changed_on"),
        "days_from_bank_change_to_invoice": days,
        "same_vendor_same_total_invoices": len(duplicate_candidates(ctx)),
        "has_vendor_email": ctx.repo.vendor_email(ctx.invoice_id) is not None,
        "free_text_fields_differing_from_master": len(free.data.get("fields", [])),
        "price_agreements_for_vendor": sorted({a.agreement_id for a in ctx.repo.agreements
                                               if a.vendor_id == ctx.invoice["vendor_id"]}),
    }


MATCHING_TOOLS = [check_documents, check_quantity, check_price, check_gst]
VENDOR_RISK_TOOLS = [check_vendor_status, check_bank_change, check_duplicates]
DOCUMENT_TOOLS = [read_vendor_email, read_free_text]
