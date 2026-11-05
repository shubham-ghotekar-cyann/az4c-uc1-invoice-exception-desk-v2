"""Final step: combine tool findings with the AP-POL §7.1 priority. Pure code; no agent decides."""
from __future__ import annotations

from . import config
from .tools import POLICY, InvoiceContext, ToolResult

PRIORITY = ["DUPLICATE", "VENDOR_BLOCKED", "BANK_CHANGE_RISK", "UNKNOWN_PO",
            "MISSING_GRN", "QTY_SHORT", "PRICE_VARIANCE", "GST_MISMATCH"]
DECISION = {"CLEAN_MATCH": "pay", "QTY_SHORT": "hold", "PRICE_VARIANCE": "hold", "GST_MISMATCH": "hold",
            "MISSING_GRN": "hold", "DUPLICATE": "escalate", "VENDOR_BLOCKED": "escalate",
            "BANK_CHANGE_RISK": "escalate", "UNKNOWN_PO": "escalate"}


def merge_lines(ctx: InvoiceContext, results: list[ToolResult]) -> list[dict]:
    """One row per invoice line with every number the checks used."""
    rows = {ln["line_id"]: {"line_id": ln["line_id"], "item_code": ln["item_code"], "qty_invoiced": ln["qty"],
                            "unit_price_invoiced": ln["unit_price"], "line_amount": ln["line_amount"],
                            "gst_rate_invoiced": ln["gst_rate"], "gst_amount": ln["gst_amount"], "issues": []}
            for ln in ctx.lines}
    for r in results:
        for row in r.data.get("lines", []):
            if row["line_id"] in rows:
                rows[row["line_id"]].update({k: v for k, v in row.items() if k != "line_id"})
        for f in r.findings:
            if f.line_id in rows:
                rows[f.line_id]["issues"].append(f.code)
    return list(rows.values())


def combine(ctx: InvoiceContext, results: list[ToolResult], instructions_found: bool = False) -> dict:
    findings = [f for r in results for f in r.findings]
    codes = [c for c in PRIORITY if any(f.code == c for f in findings)]
    main = codes[0] if codes else "CLEAN_MATCH"
    decision = DECISION[main]
    needs_human = decision != "pay" or ctx.total > config.APPROVAL_THRESHOLD

    flagged = [f.line_id for f in findings if f.line_id]
    line_ids = list(dict.fromkeys(flagged + [ln["line_id"] for ln in ctx.lines]))
    cites: list[str] = [ctx.invoice_id]
    if ctx.po is not None:
        cites.append(ctx.invoice["po_id"])
    cites += ctx.grn_ids + line_ids
    for r in results:
        cites += r.citations
    for f in findings:
        cites += f.citations
    cites.append(POLICY[main])
    cites += [POLICY[c] for c in codes[1:]]
    if len(codes) > 1:
        cites.append("AP-POL-7.1")
    if decision != "pay":
        cites.append("AP-POL-8.1")
    elif needs_human:
        cites.append("AP-POL-8.2")
    if instructions_found:
        cites.append("AP-POL-10.1")

    return {
        "decision": decision,
        "reason_code": main,
        "other_reasons": codes[1:],
        "amount": round(ctx.total, 2),
        "needs_human": needs_human,
        "citations": [c for c in dict.fromkeys(cites) if c],
        "findings": [f.model_dump() for f in findings],
        "lines": merge_lines(ctx, results),
    }
