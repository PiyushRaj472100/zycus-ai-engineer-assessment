"""
Evaluation harness scorer — rule-based checks and quality scoring.

Each check accepts the HTTP response (status + JSON body) and returns a
``CheckResult`` with pass/fail status, the actual value observed, and what
was expected.  Scores are computed as ``passed / total`` in [0, 1].
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Severity ordering (for max_risk_severity checks)
# ---------------------------------------------------------------------------

_SEVERITY_ORDER: Dict[str, int] = {"Low": 0, "Medium": 1, "High": 2}


# ---------------------------------------------------------------------------
# CheckResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """Outcome of a single acceptance criterion."""

    name: str
    passed: bool
    actual: Any = None
    expected: Any = None
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "actual": self.actual,
            "expected": self.expected,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# Core check dispatcher
# ---------------------------------------------------------------------------


def run_check(
    body: Optional[Dict[str, Any]],
    http_status: int,
    check: Dict[str, Any],
) -> CheckResult:
    """
    Evaluate a single acceptance check against an HTTP response.

    Supported ``type`` values:
        http_status           – response status code equals ``expected``
        field_equals          – ``body[field] == expected``
        field_in              – ``body[field] in expected`` (list)
        field_is_null         – ``body[field]`` is None or absent
        field_not_null        – ``body[field]`` is not None
        field_is_bool         – ``body[field] == expected`` (boolean)
        field_min_length      – ``len(body[field]) >= expected``
        text_contains         – ``expected.lower()`` is a substring of ``body[field]``
        text_not_contains     – ``expected.lower()`` is NOT a substring of ``body[field]``
        full_text_not_contains – ``expected.lower()`` is NOT anywhere in the entire response JSON
        list_not_empty        – ``body[field]`` is a non-empty list
        list_is_empty         – ``body[field]`` is an empty list
        max_risk_severity     – no item in ``open_risks`` exceeds the given severity tier
        talking_points_min    – ``len(body["talking_points"]) >= expected``
    """
    check_type: str = check["type"]
    name: str = check.get("name", check_type)

    # ── http_status: no body needed ──────────────────────────────────────────
    if check_type == "http_status":
        expected = check["expected"]
        passed = http_status == expected
        return CheckResult(name, passed, actual=http_status, expected=expected)

    # ── all other checks require a parsed body ────────────────────────────────
    if body is None:
        return CheckResult(
            name,
            False,
            actual=None,
            expected=check.get("expected"),
            note=f"No JSON body in response (HTTP {http_status})",
        )

    if check_type == "field_equals":
        field = check["field"]
        expected = check["expected"]
        actual = body.get(field)
        return CheckResult(name, actual == expected, actual=actual, expected=expected)

    if check_type == "field_in":
        field = check["field"]
        expected_list = check["expected"]
        actual = body.get(field)
        return CheckResult(
            name, actual in expected_list, actual=actual, expected=expected_list
        )

    if check_type == "field_is_null":
        field = check["field"]
        actual = body.get(field)
        return CheckResult(name, actual is None, actual=actual, expected=None)

    if check_type == "field_not_null":
        field = check["field"]
        actual = body.get(field)
        passed = actual is not None
        return CheckResult(
            name, passed, actual=repr(actual)[:120], expected="not None"
        )

    if check_type == "field_is_bool":
        field = check["field"]
        expected = check["expected"]
        actual = body.get(field)
        return CheckResult(
            name, actual is expected, actual=actual, expected=expected
        )

    if check_type == "field_min_length":
        field = check["field"]
        min_len: int = check["expected"]
        actual = body.get(field, "")
        length = len(actual) if isinstance(actual, str) else 0
        return CheckResult(
            name, length >= min_len, actual=length, expected=f">={min_len} chars"
        )

    if check_type == "text_contains":
        field = check["field"]
        substring = check["expected"].lower()
        raw = str(body.get(field, ""))
        passed = substring in raw.lower()
        return CheckResult(
            name,
            passed,
            actual=raw[:150],
            expected=f"contains '{check['expected']}'",
        )

    if check_type == "text_not_contains":
        field = check["field"]
        substring = check["expected"].lower()
        raw = str(body.get(field, ""))
        passed = substring not in raw.lower()
        return CheckResult(
            name,
            passed,
            actual=raw[:150],
            expected=f"must NOT contain '{check['expected']}'",
        )

    if check_type == "full_text_not_contains":
        # Serialize the entire body to JSON and check the substring is absent.
        substring = check["expected"].lower()
        serialized = json.dumps(body).lower()
        passed = substring not in serialized
        return CheckResult(
            name,
            passed,
            actual="(full response JSON)",
            expected=f"must NOT contain '{check['expected']}' anywhere in response",
        )

    if check_type == "list_not_empty":
        field = check["field"]
        lst = body.get(field, [])
        count = len(lst) if isinstance(lst, list) else 0
        return CheckResult(
            name, count > 0, actual=count, expected=">=1 items"
        )

    if check_type == "list_is_empty":
        field = check["field"]
        lst = body.get(field, [])
        count = len(lst) if isinstance(lst, list) else -1
        return CheckResult(
            name, count == 0, actual=count, expected="empty list"
        )

    if check_type == "max_risk_severity":
        # Ensure no risk in open_risks exceeds the specified max severity level.
        max_allowed: str = check["expected"]  # "Low", "Medium", or "High"
        max_ord = _SEVERITY_ORDER.get(max_allowed, 2)
        risks: List[Dict[str, Any]] = body.get("open_risks", [])
        violators = [
            r.get("severity")
            for r in risks
            if isinstance(r, dict)
            and _SEVERITY_ORDER.get(r.get("severity", "Low"), 0) > max_ord
        ]
        passed = len(violators) == 0
        return CheckResult(
            name,
            passed,
            actual=violators if violators else "none exceed limit",
            expected=f"all severities <= {max_allowed}",
        )

    if check_type == "talking_points_min":
        min_count: int = check["expected"]
        tps = body.get("talking_points", [])
        count = len(tps) if isinstance(tps, list) else 0
        return CheckResult(
            name, count >= min_count, actual=count, expected=f">={min_count}"
        )

    # Unknown check type — always fail with a diagnostic note.
    return CheckResult(
        name,
        False,
        note=f"Unknown check type: '{check_type}'",
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_case(results: List[CheckResult]) -> float:
    """Return fraction of passed checks in [0.0, 1.0], rounded to 4 d.p."""
    if not results:
        return 0.0
    return round(sum(1 for r in results if r.passed) / len(results), 4)
