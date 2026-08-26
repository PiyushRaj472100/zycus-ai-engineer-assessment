"""
Batch comparison script: runs a sample of tickets.json through the live
/triage endpoint and compares AI output vs dataset ground-truth labels.

Usage:
    python scripts/batch_compare.py [--n 20] [--url http://127.0.0.1:8000]

Output:
    A table printed to stdout + results saved to batch_compare_results.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    print("httpx not found. Run: pip install httpx")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TICKETS_PATH = Path("data/tickets.json")
RESULTS_PATH = Path("batch_compare_results.json")

# Fields to compare (AI output key → tickets.json key)
FIELD_MAP = {
    "product_area": "product_area",
    "issue_category": "category",
    "urgency": "urgency",
}

# Columns widths for the table
COL_W = {"ticket_id": 12, "field": 16, "dataset": 20, "ai": 20, "match": 6}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_tickets(path: Path, n: int) -> list[dict]:
    tickets = json.loads(path.read_text(encoding="utf-8"))
    # Pick a deterministic random sample by seed
    rng = random.Random(42)
    sample = rng.sample(tickets, min(n, len(tickets)))
    return sample


def call_triage(url: str, subject: str, body: str, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            resp = httpx.post(
                f"{url}/triage",
                json={"subject": subject, "body": body},
                timeout=60,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (502, 503):
                wait = 4 * (2 ** attempt)
                print(f"  [{resp.status_code}] transient error, retrying in {wait}s…")
                time.sleep(wait)
            else:
                print(f"  [{resp.status_code}] {resp.text[:120]}")
                return None
        except Exception as exc:
            print(f"  Request error: {exc}")
            time.sleep(4)
    return None


def header_row() -> str:
    return (
        f"{'ticket_id':<12} {'field':<16} {'dataset':<22} {'ai':<22} match"
    )


def compare_row(ticket_id: str, field: str, dataset_val: str, ai_val: str | None) -> str:
    ai_str = str(ai_val) if ai_val is not None else "null"
    match = "OK" if str(dataset_val).lower() == ai_str.lower() else "NO"
    return (
        f"{ticket_id:<12} {field:<16} {str(dataset_val):<22} {ai_str:<22} {match}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Batch compare triage vs ground truth")
    parser.add_argument("--n", type=int, default=20, help="Number of tickets to sample")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="API base URL")
    args = parser.parse_args()

    tickets = load_tickets(TICKETS_PATH, args.n)
    print(f"\nRunning {len(tickets)} tickets against {args.url}/triage …\n")
    print(header_row())
    print("-" * 80)

    results = []
    match_counts: dict[str, int] = {f: 0 for f in FIELD_MAP}
    total_counts: dict[str, int] = {f: 0 for f in FIELD_MAP}

    for ticket in tickets:
        tid = ticket["ticket_id"]
        subject = ticket.get("subject", "")
        body = ticket.get("body", "")

        ai = call_triage(args.url, subject, body)
        if ai is None:
            print(f"{tid:<12} [SKIPPED — API error]")
            continue

        row: dict[str, Any] = {"ticket_id": tid}

        for ai_key, ds_key in FIELD_MAP.items():
            ds_val = ticket.get(ds_key, "")
            ai_val = ai.get(ai_key)
            is_match = str(ds_val).lower() == str(ai_val or "").lower()

            row[ai_key] = {
                "dataset": ds_val,
                "ai": ai_val,
                "match": is_match,
            }
            total_counts[ai_key] += 1
            if is_match:
                match_counts[ai_key] += 1

            print(compare_row(tid, ai_key, ds_val, ai_val))

        row["known_issue"] = ai.get("known_issue")
        row["reasoning"] = ai.get("reasoning", "")
        row["first_response"] = ai.get("first_response", "")
        results.append(row)
        print()  # blank line between tickets
        time.sleep(0.5)

    # Summary
    print("=" * 80)
    print("ACCURACY SUMMARY")
    print("=" * 80)
    for field, total in total_counts.items():
        matched = match_counts[field]
        pct = (matched / total * 100) if total else 0
        label = "issue_category (dataset: category)"  if field == "issue_category" else field
        print(f"  {field:<20} {matched:>3}/{total:<3}  ({pct:.0f}%)")

    print(f"\nNote: category mismatches may be legitimate (AI sees free text, not historical label).")
    print(f"Full results → {RESULTS_PATH}\n")

    RESULTS_PATH.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
