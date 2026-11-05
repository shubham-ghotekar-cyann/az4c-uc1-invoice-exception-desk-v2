# How I used AI coding tools

**Tool:** Claude Code (Claude Opus 5.5) in VS Code, with the activity hooks shipped in the pack enabled.

## What the assistant did

1. **Analysis, before any code.** It read the pack, mapped the tables, listed the policy rules with their
   priority, and found the planted traps:
   - the injection in the INV-20813 email and in the INV-29388-L1 description;
   - the AJA-1042 / aja1042 duplicate;
   - AGR-3121 not yet in force on the invoice date.

   It wrote a throwaway rule-engine prototype (outside the repo) to check its reading of the policy on all
   901 invoices and the 5 samples.
2. **Design.** It proposed deterministic tools for all the arithmetic, with LLMs used only for language
   work. I changed the design: an LLM orchestrator must pick the team on every case, there are 3
   specialists that each own their tools, and a final code step combines and validates the results.
3. **Build,** in the order I set: `verify-pack` → tools → specialists → orchestrator → CLI → tests → docs.
   The 5 samples were run after each stage.

## What I checked or changed myself

- I set the scope and the hard rules: edit only `code/`, read data only via `DATA_DIR`, Groq/Gemini only,
  one specialist call per case, strict schemas.
- I answered the policy questions it raised:
  - duplicate direction;
  - an invoice's own payment record;
  - a bank change after the invoice date;
  - boundary inclusivity;
  - citation format;
  - `mock_system/` location;
  - committing the data pack.
- I replaced its code-only orchestrator with an LLM orchestrator, plus a coverage guard.

## Things the tooling caught that would otherwise have shipped

- **`core.autocrlf=true` on this machine** had stored the 9 CSVs in git with converted line endings. A
  clone would have failed the pack checksum gate, which was the cause of my previous attempt's failure.
  `verify-pack` now hashes the git index copy as well as the working copy.
- **API keys pasted into `.env.example`,** a committed file. They were moved to the gitignored `.env`
  before any commit; git history shows the template was always committed empty.
- **The screener quoting with a non-breaking hyphen** (U+2011) failed the verbatim-quote check and cost a
  retry. Quote comparison now normalises typographic hyphens and quotes.
