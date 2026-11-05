# Invoice exception desk (UC1)

**Role:** Associate / Lead · **Part:** A+B (decision and mock ERP writes, plus the AP manager approval
step: `--auto-approve` and `approve <run_id>`)

This system takes one supplier invoice and decides **pay**, **hold** or **escalate** under Kestrel's AP
policy. It cites the records the decision rests on, pauses for the AP manager when approval is needed
(Part B), and then writes to the mock ERP.

**Design rule:** plain Python does every calculation and makes the decision. LLM agents plan the work,
explain the tool results and screen untrusted vendor text. Each agent's reply is checked against the
tool results, so an agent cannot invent a reason, a number or a citation.

## Set up and run

Python 3.12. From a fresh clone:

```bash
cd code
python -m venv .venv && .venv/Scripts/activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # add GROQ_API_KEY and GEMINI_API_KEY
python -m app verify-pack                             # pack files unchanged, committed, not ignored

python -m app run --case ../samples/UC1-S01.json --out out/UC1-S01.json
python -m app run --case ../samples/UC1-S02.json --out out/UC1-S02.json                  # pauses for approval
python -m app approve <run_id> --action approve|reject|change [--decision hold --reason-code QTY_SHORT --note "..."]
python -m app run --case ../samples/UC1-S02.json --out out/UC1-S02.json --auto-approve
python -m app eval --cases ../samples
python -m app eval --cases tests/cases                # writes tests/score.json
```

- **Data:** read only through `DATA_DIR` (default: the repository root, which holds `data/` and `docs/`).
  The SQLite file is opened read-only (`mode=ro`).
- **Outputs:**
  - `mock_system/` at the repository root: `erp_payment_status.jsonl`, plus `outbox/<invoice_id>.md`
    for a hold. Override the location with `MOCK_SYSTEM_DIR`.
  - `code/out/`: results, traces (`out/traces/<trace_id>.jsonl`) and paused runs (`out/runs/`).

## Design

See [docs/architecture.md](docs/architecture.md) for the diagram.

| Step | Who | What |
|---|---|---|
| 1 | tool `invoice_summary` | Lookups only: PO/GRN exist, vendor status, days since bank change, same-total invoices, email present, free text that differs from master data |
| 2 | **Orchestrator agent** | Reads the summary and picks the team, with a reason per specialist. Matching always runs; Vendor Risk and Document Screener run only when the summary shows a signal |
| 3 | coverage guard (code) | If the orchestrator skipped a specialist despite a signal, adds it back and logs why. Without this, a planning slip could miss a blocked vendor or a duplicate |
| 4 | **Matching agent** | Runs its tools (`check_documents`, `check_quantity`, `check_price`, `check_gst`) and reports and explains the results |
| 4 | **Vendor Risk agent** | Runs its tools (`check_vendor_status`, `check_bank_change`, `check_duplicates`) and reports and explains the results |
| 4 | **Document Screener agent** | Reads the vendor email and odd free-text fields as untrusted data and classifies each claim (pre-approval, price change, delivery claim, instruction to the system…) against the records |
| 5 | validator (code) | Reason codes + line IDs must equal the tool findings, every number must appear in the tool output, citations must be known IDs, and screener quotes must be verbatim. A mismatch triggers one retry with the problems fed back |
| 6 | combine (code) | Tool findings + the AP-POL §7.1 priority (`DUPLICATE` > `VENDOR_BLOCKED` > `BANK_CHANGE_RISK` > `UNKNOWN_PO` > `MISSING_GRN` > `QTY_SHORT` > `PRICE_VARIANCE` > `GST_MISMATCH`) → decision, `other_reasons`, `needs_human`, citations, per-line numbers |
| 7 | approval (Part B) | `needs_human` = every hold and escalation, plus pay over ₹5,00,000 (§8). The run pauses unless `--auto-approve`. `approve` writes, `reject` writes nothing, `change` writes the manager's decision |

**How each agent is built:** one agent = one role, one prompt (`app/prompts/<name>.md`), one Pydantic
output schema and its own tools. The runtime executes an agent's tools and passes the results into a
single model call. There are no tool-calling loops, so a specialist costs exactly one call unless a
logged retry is needed.

**Teams differ by invoice.** On the 5 samples: S01/S02/S03/S05 get `matching`, and S04 (vendor email)
gets `matching+document_screener`. Across the test cases, all four combinations occur.

### How I read the policy where it was ambiguous

- **Duplicate (§4.1):** only the *later* invoice of a matching pair is flagged; the earlier one is the
  original. An invoice's own payment record does not change its decision.
- **Bank change (§5.1):** counts if the change is 0 to 30 days *before* the invoice date (inclusive) and
  the total is strictly above ₹5,00,000. A change after the invoice date is noted in the trace but not
  flagged.
- **Tolerances:** the price band (±2%), GST (₹1) and the 7-day duplicate window are inclusive.
- **Signed agreements (§3.4):** an agreement applies from its effective date, judged on the invoice
  date. Emails never change the reference price.
- **Missing records:**
  - If the PO is missing, line checks are not applicable.
  - If the GRN is missing, the quantity check is not applicable (price and GST still run).
  - A GRN with no line for an invoiced PO line counts as 0 received.
- **Citations:** record IDs (invoice, lines, PO, GRN, agreement, vendor, payment) and policy sections as
  `AP-POL-3.2`.

## Tools

All tools are in `app/tools.py`. Each takes the invoice context and returns a `ToolResult`:
`status` (pass / fail / not_applicable), `findings` (reason code, line, detail, the numbers used,
citations), `notes` and `data`. Tools never raise on missing data; they report it (§6.3). Errors are
handled as follows:

- A missing invoice ID stops the run with a clear error.
- A missing PO, GRN, item or PO line becomes a finding or a note. Nothing is ever filled in.

## Models used and why

| Agent | Model | Why |
|---|---|---|
| Orchestrator | Groq `openai/gpt-oss-20b` | A small classification task; fast and cheap. It supports strict JSON-schema output |
| Matching, Vendor Risk, Document Screener | Groq `openai/gpt-oss-120b` | Better explanations and claim classification; also supports strict schema output |
| Fallback for any agent | Gemini `gemini-3.5-flash` | A different provider, so a Groq outage or rate limit does not stop the run |

All calls use temperature 0 and `reasoning_effort=low`. Model IDs can be changed in `.env`.

## Failure handling

- **Bad JSON:** strict `json_schema` response format, then Pydantic. If the reply still fails, retry once
  with the error fed back (logged as `schema_invalid`).
- **Wrong content:** a reason, number or citation that is not in the tool results, or a quote that is not
  verbatim. Retry once with the problems listed (logged as `validation`).
- **Rate limits (429):** wait for `retry-after` (capped at 30 s, up to 3 times). This is logged as
  `rate_limit_wait` and is not counted as an attempt, because no reply was produced.
- **Provider errors (timeout, 5xx, auth):** the second attempt goes to Gemini (logged as
  `provider_error`).
- **Max 2 attempts per agent, and a per-case budget** of 8 calls and 40k tokens. If an agent still fails,
  its tools' findings are used unchanged and the trace records `agent_failed`. The decision does not
  depend on the agent: with no API keys at all, every sample still gets the right decision.
- **Prompt injection:** vendor text is passed as data, the screener has no write access, and a regex
  backstop runs alongside it. Two planted injections (an email and a DB line description) are covered
  by test cases T01 and T02.

## Tests and score

`tests/cases/` holds 20 cases, chosen to cover every reason code and every trap found while analysing
the data:
- **Prompt injection:** in an email (T01) and in a database field (T02).
- **Duplicates:** the later invoice of a pair (T03) and its paid original (T19).
- **Agreements:** one not yet in force, where the email claims the new rate (T04), and one not yet
  effective while the invoice is at the PO price (T16).
- **Unknown PO:** T05.
- **Bank changes:** before the invoice (T06, T18), after it (T07, T20) and under the threshold (T08).
- **Blocked vendors:** T09, T10, T17.
- **Several problems on one invoice:** T06, T11, T17, T18.
- **Delivery claimed without a GRN:** T12.
- **Unsigned 5% price-rise email:** T13.
- **Either side of the ₹5L approval threshold:** T14, T15.

Expected answers were derived by hand from the policy, then cross-checked against an independent
prototype run over all 901 invoices.

**Score (live Groq models, 2026-11-05):**

| Set | Score | Model calls per case | Tokens per case |
|---|---|---|---|
| `samples/` (5) | **100%** | avg 2.2, max 3 | ~2.4k–4.1k |
| `tests/cases/` (20) | **100%** (`tests/score.json`) | avg 2.9, max 4 | ~2.2k–5.7k |

Over the 25 cases, 2 agent calls needed a second attempt, both logged with a reason:
- In T12, the Matching agent cited a non-existent `AP-PRO-6.1`. The validator caught it and attempt 2 was
  correct.
- In T16, Groq could not generate schema-valid JSON, so attempt 2 went to the same provider.

No case used more than one retry, and no specialist ran twice without a stated reason.
