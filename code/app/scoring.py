"""Score one result against an expected file (samples/expected format). Each scored field counts equally."""
from __future__ import annotations

import re


def numbers_in(obj) -> list[float]:
    """Every number in the result, including numbers written inside strings."""
    out: list[float] = []
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, str):
        return [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*\.?\d*", obj) if x.replace(",", "").strip("-.")]
    if isinstance(obj, dict):
        obj = list(obj.values())
    for v in obj:
        out += numbers_in(v)
    return out


def field_checks(result: dict, expected: dict) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    if "decision" in expected:
        checks["decision"] = result.get("decision") == expected["decision"]
    if "reason_code" in expected:
        accepted = expected["reason_code"] if isinstance(expected["reason_code"], list) else [expected["reason_code"]]
        checks["reason_code"] = result.get("reason_code") in accepted
    if "amount" in expected:
        exp = expected["amount"]
        value, tol = (exp["value"], exp.get("tolerance", 0.0)) if isinstance(exp, dict) else (exp, 0.0)
        got = result.get("amount")
        checks["amount"] = (got is None and value is None) or (
            got is not None and value is not None and abs(float(got) - float(value)) <= tol + 1e-9)
    if expected.get("key_numbers"):
        have = numbers_in(result)
        wants = [k["value"] if isinstance(k, dict) else k for k in expected["key_numbers"]]
        checks["key_numbers"] = all(any(abs(h - float(w)) <= 0.01 for h in have) for w in wants)
    if "must_cite" in expected:
        checks["must_cite"] = set(expected["must_cite"]) <= set(result.get("citations", []))
    if "needs_human" in expected:
        checks["needs_human"] = result.get("needs_human") == expected["needs_human"]
    return checks


def score_case(result: dict, expected: dict) -> float:
    checks = field_checks(result, expected)
    return round(sum(checks.values()) / len(checks), 4) if checks else 0.0
