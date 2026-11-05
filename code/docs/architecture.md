# Architecture

```mermaid
flowchart TD
    CASE["case.json<br/>{invoice_id}"] --> SUM["invoice_summary tool<br/>(lookups only)"]
    SUM --> ORCH["Orchestrator agent<br/>gpt-oss-20b<br/>picks the team"]
    ORCH --> GUARD{"Coverage guard (code)<br/>re-adds a specialist the plan<br/>skipped despite a signal"}

    GUARD --> M["Matching agent<br/>gpt-oss-120b"]
    GUARD -. "vendor blocked / bank change ≤30d /<br/>same-total invoice exists" .-> V["Vendor Risk agent<br/>gpt-oss-120b"]
    GUARD -. "vendor email or<br/>odd free text" .-> D["Document Screener agent<br/>gpt-oss-120b"]

    M --- MT["tools: check_documents · check_quantity<br/>check_price (PO / signed agreement) · check_gst"]
    V --- VT["tools: check_vendor_status<br/>check_bank_change · check_duplicates"]
    D --- DT["tools: read_vendor_email · read_free_text<br/>scan_for_instructions (regex backstop)"]

    M --> VAL["Validator (code)<br/>reason codes, numbers and citations<br/>must equal the tool results"]
    V --> VAL
    D --> VAL2["Validator (code)<br/>quotes must be verbatim"]
    VAL --> COMB["Combine (code)<br/>AP-POL 7.1 priority → decision,<br/>needs_human, citations"]
    VAL2 --> COMB
    MT --> COMB
    VT --> COMB

    COMB --> APP{"needs_human?<br/>hold / escalate / pay > ₹5L"}
    APP -- no --> WRITE["mock_system/erp_payment_status.jsonl<br/>+ outbox/&lt;invoice&gt;.md on hold"]
    APP -- "yes + --auto-approve" --> WRITE
    APP -- "yes" --> PAUSE["pending: out/runs/&lt;run_id&gt;.json"]
    PAUSE -- "approve / change" --> WRITE
    PAUSE -- reject --> NOTHING["nothing written"]
    COMB --> RES["result.json + out/traces/&lt;run_id&gt;.jsonl"]
```

**Where decisions come from:** only the tool findings in the Combine step. Agents choose the team,
explain the findings and screen untrusted text. Their output is checked against the same tool results,
and none of their schemas has a decision field.

**Calls per case:** 1 orchestrator call plus 1 per selected specialist, so 2 to 4. An agent gets a
second attempt only for a stated, logged reason (`provider_error` → Gemini, `schema_invalid`,
`validation`). The budget is 8 calls and 40k tokens per case.
