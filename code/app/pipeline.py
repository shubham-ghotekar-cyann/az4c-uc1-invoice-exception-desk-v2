"""One case end to end: summary -> orchestrator agent -> specialists (each with its tools) -> combine in code
-> approval (Part B) -> mock ERP writes."""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .agents import (SPECIALISTS, TOOLS_FOR, required_specialists, run_orchestrator, run_screener,
                     run_specialist, untrusted_sources)
from .data import Repo
from .llm import LLM, Trace
from .rules import DECISION, combine
from .tools import InvoiceContext, ToolResult, invoice_summary, scan_for_instructions


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _runs_dir() -> Path:
    d = config.out_dir() / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_tool(trace: Trace, agent: str, r: ToolResult) -> None:
    trace.log("tool", agent=agent, tool=r.tool, status=r.status, findings=[f.code for f in r.findings],
              notes=r.notes)


def plan_team(trace: Trace, llm: LLM, summary: dict) -> list[str]:
    """Orchestrator agent picks the team; the coverage guard adds back any specialist it wrongly skipped."""
    run = run_orchestrator(llm, summary)
    required = required_specialists(summary)
    if run.ok:
        chosen = {name: c.reason for name, c in run.output.steps if c.run}
        for name, c in run.output.steps:
            trace.log("plan_step", specialist=name, run=c.run, reason=c.reason, by="orchestrator")
    else:
        chosen = {}
        trace.log("plan_fallback", reason=f"orchestrator failed ({run.error}); using required specialists")
    for name, why in required.items():
        if name not in chosen:
            chosen[name] = why
            trace.log("plan_step", specialist=name, run=True, by="coverage_guard",
                      reason=f"signal present ({why}) but {'orchestrator skipped it' if run.ok else 'no plan'}")
    return [s for s in SPECIALISTS if s in chosen]


def run_case(case: dict, auto_approve: bool, out_path: str | None = None) -> dict:
    case_id = case["case_id"]
    invoice_id = case["input"]["invoice_id"]
    run_id = f"{case_id}-{uuid.uuid4().hex[:8]}"
    trace = Trace(run_id)
    trace.log("start", case_id=case_id, invoice_id=invoice_id, data_dir=str(config.data_dir()))
    repo = Repo(config.data_dir())
    try:
        ctx = InvoiceContext(repo, invoice_id)
        llm = LLM(trace)
        summary = invoice_summary(ctx)
        trace.log("tool", agent="orchestrator", tool="invoice_summary", output=summary)
        team = plan_team(trace, llm, summary)

        results: dict[str, list[ToolResult]] = {}
        reports: dict[str, dict] = {}
        for name in team:
            results[name] = [tool(ctx) for tool in TOOLS_FOR[name]]
            for r in results[name]:
                _log_tool(trace, name, r)
            if name == "document_screener":
                others = results.get("matching", []) + results.get("vendor_risk", [])
                run = run_screener(llm, ctx, results[name], others)
            else:
                run = run_specialist(llm, name, ctx, results[name])
            reports[name] = {"status": "accepted" if run.ok else "failed", "attempts": run.attempts,
                             "error": run.error or None,
                             "output": run.output.model_dump() if run.ok else None}

        # Final step, in code: tool findings + R16 priority. Agent outputs were already validated against
        # these same tool results; a failed agent changes nothing here.
        policy_results = results.get("matching", []) + results.get("vendor_risk", [])
        screener = reports.get("document_screener", {}).get("output") or {}
        doc_text = untrusted_sources(results.get("document_screener", []))
        instructions = bool(screener.get("contains_instructions_to_system")) or any(
            scan_for_instructions(t) for t in doc_text.values())
        combined = combine(ctx, policy_results, instructions_found=instructions)
        trace.log("combine", decision=combined["decision"], reason_code=combined["reason_code"],
                  other_reasons=combined["other_reasons"], needs_human=combined["needs_human"])

        result = {
            "case_id": case_id,
            "decision": combined["decision"],
            "reason_code": combined["reason_code"],
            "amount": combined["amount"],
            "citations": combined["citations"],
            "needs_human": combined["needs_human"],
            "trace_id": run_id,
            "other_reasons": combined["other_reasons"],
            "invoice_id": invoice_id,
            "lines": combined["lines"],
            "findings": combined["findings"],
            "explanation": _explanation(combined, reports),
            "document_findings": screener.get("claims", []),
            "instructions_in_documents_ignored": instructions,
            "team": team,
            "agents": {"orchestrator": "ok" if any(e["type"] == "plan_step" and e["by"] == "orchestrator"
                                                  for e in trace.events) else "failed",
                       **{k: v["status"] for k, v in reports.items()}},
            "llm_usage": {"calls": llm.budget.calls, "tokens": llm.budget.tokens},
        }
    finally:
        repo.close()

    if not result["needs_human"]:
        result["approval"] = {"required": False, "status": "not_required"}
        finalize(result, trace)
    elif auto_approve:
        result["approval"] = {"required": True, "status": "auto_approved", "by": "AP manager (--auto-approve)",
                              "at": _now()}
        finalize(result, trace)
    else:
        result["approval"] = {"required": True, "status": "pending", "run_id": run_id,
                              "resume": f"python -m app approve {run_id} --action approve|reject|change"}
        trace.log("approval_pending", run_id=run_id)
        print(f"Paused for AP manager approval. Resume with: python -m app approve {run_id} "
              f"--action approve|reject|change", file=sys.stderr)
    _save_state(run_id, result, out_path)
    trace.log("end", decision=result["decision"], approval=result["approval"]["status"], **result["llm_usage"])
    return result


def _explanation(combined: dict, reports: dict) -> str:
    parts = []
    for name in ("matching", "vendor_risk", "document_screener"):
        out = (reports.get(name) or {}).get("output")
        if out and out.get("assessment"):
            parts.append(f"{name}: {out['assessment']}")
    if not parts:  # every agent failed: fall back to the tool details
        parts = [f["detail"] for f in combined["findings"]] or ["All checks passed."]
    return " ".join(parts)


# --------------------------------------------------------------------- approval + writes
def _save_state(run_id: str, result: dict, out_path: str | None) -> None:
    state = {"result": result, "out_path": str(Path(out_path).resolve()) if out_path else None}
    (_runs_dir() / f"{run_id}.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def finalize(result: dict, trace: Trace | None = None) -> None:
    """Write to the mock ERP only once the decision is final (and approved when approval is required)."""
    mock = config.mock_system_dir()
    mock.mkdir(parents=True, exist_ok=True)
    record = {"invoice_id": result["invoice_id"], "status": result["decision"], "reason": result["reason_code"],
              "time": _now(), "run_id": result["trace_id"], "approval": result["approval"]["status"]}
    with (mock / "erp_payment_status.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    written = ["erp_payment_status.jsonl"]
    if result["decision"] == "hold":
        outbox = mock / "outbox"
        outbox.mkdir(exist_ok=True)
        (outbox / f"{result['invoice_id']}.md").write_text(vendor_letter(result), encoding="utf-8")
        written.append(f"outbox/{result['invoice_id']}.md")
    result["mock_system_writes"] = written
    if trace:
        trace.log("mock_write", files=written, record=record)


def inr(x: float) -> str:
    """Indian digit grouping: 1351706.41 -> 13,51,706.41"""
    whole, frac = f"{abs(x):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    body = ",".join(([head] if head else []) + groups + [tail])
    return f"{'-' if x < 0 else ''}₹{body}.{frac}"


def vendor_letter(result: dict) -> str:
    """AP-POL §9.1: affected lines, what we expected, what was invoiced, what to send. Built from tool numbers
    only; nothing from the vendor's own text is repeated, and escalation reasons are never shared."""
    asks, rows = set(), []
    for f in result["findings"]:
        n, line = f["numbers"], f["line_id"] or "invoice total"
        if f["code"] == "QTY_SHORT":
            rows.append(f"| {line} | quantity {n['qty_received']:g} received (goods receipt) | quantity {n['qty_invoiced']:g} |")
            asks.add("a credit note for the quantity not received, or proof of delivery for it")
        elif f["code"] == "PRICE_VARIANCE":
            rows.append(f"| {line} | unit price {inr(n['unit_price_reference'])} | unit price {inr(n['unit_price_invoiced'])} |")
            asks.add("a corrected invoice or a credit note at the agreed price")
        elif f["code"] == "GST_MISMATCH" and "gst_rate_master" in n:
            rows.append(f"| {line} | GST {n['gst_rate_master']:g}% | GST {n['gst_rate_invoiced']:g}% |")
            asks.add("a corrected invoice with the applicable GST rate")
        elif f["code"] == "GST_MISMATCH":
            rows.append(f"| {line} | GST total {inr(n.get('gst_lines_sum', 0))} (sum of lines) | GST total {inr(n.get('gst_header', 0))} |")
            asks.add("a corrected invoice with the GST total equal to the sum of the lines")
        elif f["code"] == "MISSING_GRN":
            rows.append(f"| {line} | a goods receipt for the PO | no goods receipt recorded yet |")
            asks.add("proof of delivery (signed delivery note) for the goods on this invoice")
    inv = result["invoice_id"]
    return "\n".join([
        f"**To:** vendor accounts team", f"**Subject:** Invoice {inv} on hold", "",
        "Dear Sir/Madam,", "",
        f"We have placed invoice {inv} (total {inr(result['amount'])}) on hold because it does not match our "
        "records for the lines below.", "",
        "| Line | What we expected | What was invoiced |", "| --- | --- | --- |", *rows, "",
        "To release the invoice, please send " + "; and ".join(sorted(asks)) + ".", "",
        "Regards,", "Accounts Payable, Kestrel Auto Components Pvt Ltd", ""])


def approve(run_id: str, action: str, decision: str | None = None, reason_code: str | None = None,
            note: str | None = None) -> dict:
    state_file = _runs_dir() / f"{run_id}.json"
    if not state_file.is_file():
        raise SystemExit(f"no run {run_id} under {state_file.parent}")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    result = state["result"]
    if result["approval"]["status"] != "pending":
        raise SystemExit(f"run {run_id} is not pending (status: {result['approval']['status']})")
    trace = Trace(run_id)
    base = {"required": True, "by": "AP manager", "at": _now(), **({"note": note} if note else {})}
    if action == "approve":
        result["approval"] = {**base, "status": "approved"}
        finalize(result, trace)
    elif action == "reject":
        result["approval"] = {**base, "status": "rejected"}
        result["mock_system_writes"] = []  # COMMON.md: a rejected approval writes nothing
    else:  # change
        if decision not in ("pay", "hold", "escalate"):
            raise SystemExit("--action change needs --decision pay|hold|escalate (and optionally --reason-code)")
        new_reason = reason_code or result["reason_code"]
        if new_reason not in DECISION:
            raise SystemExit(f"unknown reason code {new_reason}")
        result["approval"] = {**base, "status": "changed",
                              "original": {"decision": result["decision"], "reason_code": result["reason_code"]}}
        result["decision"], result["reason_code"] = decision, new_reason
        finalize(result, trace)
    trace.log("approval", action=action, approval=result["approval"])
    _save_state(run_id, result, state["out_path"])
    if state["out_path"]:
        Path(state["out_path"]).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
