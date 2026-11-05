"""Entry point for your system. Run these from the code/ folder:

    python -m app run --case ../samples/UC1-S01.json --out out/UC1-S01.json
    python -m app run --case <case.json> --out <result.json> --auto-approve
    python -m app approve <run_id> --action approve|reject|change
    python -m app eval --cases tests/cases

Keep the command names and options. Everything behind them is yours to build,
in this package or in modules it imports.

eval writes tests/score.json when it runs tests/cases, in this format:
    {"cases": 20, "score": 85.0, "per_case": [{"case_id": "...", "score": 1.0}]}
score is 0 to 100; each per_case score is 0 to 1.
"""
import argparse
import json
import os
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = CODE_DIR.parent


def load_env_file(path: Path) -> None:
    """Load KEY=value lines from code/.env. Variables already set take priority."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


load_env_file(CODE_DIR / ".env")

DATA_DIR = Path(os.environ.get("DATA_DIR", REPO_DIR))

for _stream in (sys.stdout, sys.stderr):  # vendor text and model replies are UTF-8; Windows consoles are not
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def run_case(case: dict, auto_approve: bool, out_path: str | None = None) -> dict:
    """Run one case end to end and return its result (format: ../schema/result.schema.json)."""
    from app.pipeline import run_case as _run
    return _run(case, auto_approve, out_path)


def approve(run_id: str, action: str, decision: str | None = None, reason_code: str | None = None,
            note: str | None = None) -> None:
    """Resume a paused run with the person's decision."""
    from app.pipeline import approve as _approve
    result = _approve(run_id, action, decision, reason_code, note)
    print(f"{run_id}: {result['approval']['status']} -> {result['decision']} / {result['reason_code']}; "
          f"writes: {result.get('mock_system_writes', [])}")


def score_case(result: dict, expected: dict) -> float:
    """Score one result against its expected answer, from 0 to 1."""
    from app.scoring import score_case as _score
    return _score(result, expected)


def cmd_run(args) -> None:
    case = json.loads(Path(args.case).read_text(encoding="utf-8"))
    result = run_case(case, args.auto_approve, args.out)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")


def cmd_eval(args) -> None:
    cases_dir = Path(args.cases).resolve()
    expected_dir = cases_dir / "expected"
    if not expected_dir.is_dir():
        expected_dir = cases_dir.parent / "expected"
    import jsonschema
    from app.scoring import field_checks
    schema = json.loads((REPO_DIR / "schema" / "result.schema.json").read_text(encoding="utf-8"))
    per_case, calls = [], []
    for case_file in sorted(cases_dir.glob("*.json")):
        case = json.loads(case_file.read_text(encoding="utf-8"))
        expected = json.loads((expected_dir / case_file.name).read_text(encoding="utf-8"))
        try:
            out = CODE_DIR / "out" / case_file.name
            result = run_case(case, auto_approve=True, out_path=str(out))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            jsonschema.validate(result, schema)
            score = score_case(result, expected)
            failed = [k for k, ok in field_checks(result, expected).items() if not ok]
            calls.append(result["llm_usage"]["calls"])
            detail = (f"{result['decision']:<8} {result['reason_code']:<16} team={'+'.join(result['team'])} "
                      f"calls={result['llm_usage']['calls']} tokens={result['llm_usage']['tokens']}"
                      + (f"  FAILED: {failed}" if failed else ""))
        except Exception as e:
            print(f"{case['case_id']}: {type(e).__name__}: {e}", file=sys.stderr)
            score, detail = 0.0, "error"
        per_case.append({"case_id": case["case_id"], "score": score})
        print(f"{case['case_id']}  {score:.2f}  {detail}")
    total = round(100 * sum(c["score"] for c in per_case) / len(per_case), 1) if per_case else 0.0
    print(f"Score: {total}% over {len(per_case)} cases"
          + (f"; model calls per case: avg {sum(calls) / len(calls):.1f}, max {max(calls)}" if calls else ""))
    if cases_dir.is_relative_to(CODE_DIR / "tests"):
        report = {"cases": len(per_case), "score": total, "per_case": per_case}
        (CODE_DIR / "tests" / "score.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def cmd_approve(args) -> None:
    approve(args.run_id, args.action, args.decision, args.reason_code, args.note)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app", description="UC1: Invoice exception desk")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="run one case end to end and write the result")
    p.add_argument("--case", required=True, help="case JSON file")
    p.add_argument("--out", required=True, help="where to write the result JSON")
    p.add_argument("--auto-approve", action="store_true", help="approve at the approval step without waiting")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("eval", help="run every case in a folder and print the score")
    p.add_argument("--cases", required=True, help="folder of case JSON files")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("approve", help="resume a paused run")
    p.add_argument("run_id")
    p.add_argument("--action", required=True, choices=["approve", "reject", "change"])
    p.add_argument("--decision", choices=["pay", "hold", "escalate"], help="with --action change: the new decision")
    p.add_argument("--reason-code", help="with --action change: the new reason code (default: keep)")
    p.add_argument("--note", help="AP manager's note, stored with the approval")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("verify-pack", help="check pack files are unchanged, committed and not ignored")
    p.set_defaults(func=lambda a: sys.exit(__import__("app.verify_pack", fromlist=["main"]).main()))

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
