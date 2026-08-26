"""
Task 3: Evaluation Harness -- entry point.

Usage
-----
# Run against the embedded FastAPI app (default, no server needed):
    python -m evals.run_evals

# Run against a live server:
    python -m evals.run_evals --base-url http://localhost:8000

# Save JSON report to a custom path:
    python -m evals.run_evals --output evals/my_report.json

# Run only Task 1 or Task 2 cases:
    python -m evals.run_evals --task t1
    python -m evals.run_evals --task t2

Exit codes
----------
0  All cases passed (score >= threshold).
1  One or more cases failed.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Reconfigure stdout to UTF-8 so emoji / special chars render on all platforms.
try:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
except AttributeError:
    pass  # already reconfigured or not a buffered stream

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent
_CASES_DIR = _HERE / "cases"
_DEFAULT_REPORT_PATH = _HERE / "report.json"

# ---------------------------------------------------------------------------
# Imports from this package
# ---------------------------------------------------------------------------

from evals.scorer import run_check, score_case, CheckResult  # noqa: E402


# ---------------------------------------------------------------------------
# HTTP client abstraction
# ---------------------------------------------------------------------------


def _make_client(base_url: Optional[str]):
    """
    Return an HTTP client.

    If *base_url* is given we use ``httpx`` against a live server.
    Otherwise we use FastAPI's ``TestClient`` (which runs the ASGI app
    in-process and properly triggers the lifespan / KB load).
    """
    if base_url:
        try:
            import httpx
        except ImportError:
            sys.exit(
                "[eval] httpx is required for --base-url mode.  "
                "Install with: pip install httpx"
            )

        class _HttpxAdapter:
            def __init__(self, client: httpx.Client) -> None:
                self._c = client

            def post(self, path: str, json: Any) -> Any:
                return self._c.post(path, json=json)

        return _HttpxAdapter(
            httpx.Client(base_url=base_url, timeout=120.0)
        )
    else:
        # In-process TestClient — starts lifespan (KB load) automatically.
        from starlette.testclient import TestClient  # type: ignore
        from src.main import app  # noqa: PLC0415

        return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Case runner
# ---------------------------------------------------------------------------


def run_case(client: Any, case: Dict[str, Any]) -> Dict[str, Any]:
    """Send a single test case request and evaluate all checks."""
    endpoint: str = case["endpoint"]
    request_body: Dict[str, Any] = case["request"]
    checks: List[Dict[str, Any]] = case.get("checks", [])
    pass_threshold: float = case.get("pass_threshold", 0.7)

    start = time.monotonic()
    http_status = 0
    body: Optional[Dict[str, Any]] = None
    error_note = ""

    try:
        resp = client.post(endpoint, json=request_body)
        http_status = resp.status_code
        try:
            body = resp.json()
        except Exception:
            body = None
    except Exception as exc:
        error_note = str(exc)

    elapsed_ms = round((time.monotonic() - start) * 1000)

    if error_note:
        check_results = [
            CheckResult(
                name="request_succeeded",
                passed=False,
                note=f"Request failed: {error_note}",
            )
        ]
    else:
        check_results = [run_check(body, http_status, c) for c in checks]

    score = score_case(check_results)
    passed = score >= pass_threshold

    return {
        "id": case["id"],
        "name": case["name"],
        "task": case["id"].split("-")[0],  # "T1" or "T2"
        "endpoint": endpoint,
        "http_status": http_status,
        "elapsed_ms": elapsed_ms,
        "score": score,
        "passed": passed,
        "pass_threshold": pass_threshold,
        "checks": [r.to_dict() for r in check_results],
    }


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

_PASS_ICON = "[PASS]"
_FAIL_ICON = "[FAIL]"
_CHECK_PASS = "  PASS"
_CHECK_FAIL = "  FAIL"


def _fmt_score(score: float) -> str:
    return f"{score:.2f}"


def print_markdown_report(results: List[Dict[str, Any]]) -> None:
    """Print a rich Markdown summary to stdout."""
    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    failed_count = total - passed_count
    overall_score = (
        round(sum(r["score"] for r in results) / total, 4) if total else 0.0
    )

    run_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print()
    print("=" * 70)
    print("  EVALUATION HARNESS REPORT")
    print(f"  Run: {run_time}")
    print("=" * 70)
    print("")

    # Summary table
    print(f"{'ID':<10} {'Name':<45} {'Score':>6}  {'Pass?'}")
    print("-" * 70)
    for r in results:
        icon = _PASS_ICON if r["passed"] else _FAIL_ICON
        score_str = _fmt_score(r["score"])
        name_trunc = r["name"][:44]
        print(f"{r['id']:<10} {name_trunc:<45} {score_str:>6}  {icon}")
    print("-" * 70)
    print(
        f"{'TOTAL':<10} {f'{passed_count}/{total} passed':<45} "
        f"{_fmt_score(overall_score):>6}  "
        f"{'ALL PASS' if failed_count == 0 else f'{failed_count} FAILED'}"
    )
    print()

    # Per-task breakdown
    for task_label in ("T1", "T2"):
        task_results = [r for r in results if r["task"] == task_label]
        if not task_results:
            continue
        task_pass = sum(1 for r in task_results if r["passed"])
        task_score = (
            round(sum(r["score"] for r in task_results) / len(task_results), 4)
            if task_results
            else 0.0
        )
        label = "Task 1 -- Triage" if task_label == "T1" else "Task 2 -- Account Health"
        print(
            f"  {label}: {task_pass}/{len(task_results)} passed, "
            f"avg score {_fmt_score(task_score)}"
        )
    print()

    # Detailed check breakdown for failed cases
    failed = [r for r in results if not r["passed"]]
    if failed:
        print("-" * 70)
        print("  FAILED CASE DETAILS")
        print("-" * 70)
        for r in failed:
            print()
            print(f"  {_FAIL_ICON}  {r['id']} — {r['name']}")
            print(f"     HTTP {r['http_status']}  |  score {_fmt_score(r['score'])}  |  {r['elapsed_ms']}ms")
            for chk in r["checks"]:
                icon = _CHECK_PASS if chk["passed"] else _CHECK_FAIL
                actual_str = str(chk.get("actual", ""))[:80]
                expected_str = str(chk.get("expected", ""))[:60]
                print(f"  {icon}  {chk['name']}")
                if not chk["passed"]:
                    print(f"        actual:   {actual_str}")
                    print(f"        expected: {expected_str}")
                if chk.get("note"):
                    print(f"        note:     {chk['note']}")
    else:
        print("  All cases passed. No failures to detail.")
    print()
    print("=" * 70)
    print()


def save_json_report(results: List[Dict[str, Any]], output_path: Path) -> None:
    """Save the full structured JSON report."""
    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    overall_score = (
        round(sum(r["score"] for r in results) / total, 4) if total else 0.0
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_cases": total,
            "passed": passed_count,
            "failed": total - passed_count,
            "overall_score": overall_score,
        },
        "results": results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"  [eval] JSON report saved → {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def load_cases(path: Path) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the evaluation harness for Task 1 (triage) and Task 2 (account-health)."
    )
    parser.add_argument(
        "--base-url",
        default=None,
        metavar="URL",
        help="Base URL of a running server (e.g. http://localhost:8000). "
             "If omitted, the FastAPI app is started in-process via TestClient.",
    )
    parser.add_argument(
        "--output",
        default=str(_DEFAULT_REPORT_PATH),
        metavar="PATH",
        help="Where to write the JSON report (default: evals/report.json).",
    )
    parser.add_argument(
        "--task",
        choices=["t1", "t2", "all"],
        default="all",
        help="Which task's cases to run (default: all).",
    )
    args = parser.parse_args(argv)

    # ── Load test cases ──────────────────────────────────────────────────────
    cases: List[Dict[str, Any]] = []
    if args.task in ("t1", "all"):
        cases += load_cases(_CASES_DIR / "t1_cases.json")
    if args.task in ("t2", "all"):
        cases += load_cases(_CASES_DIR / "t2_cases.json")

    if not cases:
        print("[eval] No test cases loaded — nothing to run.")
        return 1

    # ── Build client ─────────────────────────────────────────────────────────
    print(f"\n[eval] Starting evaluation harness ({len(cases)} cases)…")
    if args.base_url:
        print(f"[eval] Mode: live server → {args.base_url}")
    else:
        print("[eval] Mode: in-process TestClient (no server required)")

    client = _make_client(args.base_url)

    # ── Run cases ────────────────────────────────────────────────────────────
    all_results: List[Dict[str, Any]] = []
    with client:  # type: ignore[attr-defined]  # TestClient is a context manager
        for case in cases:
            case_id = case["id"]
            print(f"  [{case_id}] {case['name'][:55]}", end="… ", flush=True)
            result = run_case(client, case)
            all_results.append(result)
            icon = _PASS_ICON if result["passed"] else _FAIL_ICON
            print(
                f"{icon}  score={_fmt_score(result['score'])}  "
                f"({result['elapsed_ms']}ms)"
            )

    # ── Report ───────────────────────────────────────────────────────────────
    print_markdown_report(all_results)
    save_json_report(all_results, Path(args.output))

    any_failed = any(not r["passed"] for r in all_results)
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
