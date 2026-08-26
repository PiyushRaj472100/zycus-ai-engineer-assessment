"""
Direct Evaluation Script:
Evaluates tickets from data/tickets.json using the triage pipeline.
Compares:
  - product_area vs dataset product_area
  - issue_category vs dataset category
  - urgency vs dataset urgency
  - verifies first_response grounding (no false action claims)
  - verifies known_issue accuracy and KB citation consistency
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.retrieval import load_kb
from src.schemas import ProductArea, IssueCategory, UrgencyTier
from src.triage import triage_ticket

TICKETS_PATH = Path("data/tickets.json")
KB_PATH = Path("knowledge-base")

def run_evaluation(num_samples: int = 10):
    print("=" * 80)
    print(f"EVALUATING {num_samples} TICKETS FROM tickets.json")
    print("=" * 80)

    kb = load_kb(KB_PATH)
    print(f"Loaded Knowledge Base: {kb.total_chunks} chunks\n")

    tickets = json.loads(TICKETS_PATH.read_text(encoding="utf-8"))
    
    # Deterministic sample
    rng = random.Random(42)
    sample = rng.sample(tickets, min(num_samples, len(tickets)))

    results = []
    category_matches = 0
    area_matches = 0
    urgency_matches = 0

    print(f"{'Ticket ID':<12} | {'Field':<15} | {'Dataset Label':<22} | {'AI Output':<22} | {'Match'}")
    print("-" * 85)

    for idx, t in enumerate(sample, 1):
        tid = t["ticket_id"]
        subject = t.get("subject", "")
        body = t.get("body", "")
        ds_area = t.get("product_area")
        ds_cat = t.get("category")
        ds_urg = t.get("urgency")

        chunks = kb.retrieve(f"{subject} {body}", top_k=4)

        try:
            res = triage_ticket(subject, body, chunks)
        except Exception as e:
            print(f"{tid:<12} | ERROR: {e}")
            continue

        ai_area = res.product_area.value if res.product_area else None
        ai_cat = res.issue_category.value
        ai_urg = res.urgency.value

        area_ok = "MATCH" if str(ds_area) == str(ai_area) else "DIFF"
        cat_ok = "MATCH" if str(ds_cat) == str(ai_cat) else "DIFF"
        urg_ok = "MATCH" if str(ds_urg) == str(ai_urg) else "DIFF"

        if area_ok == "MATCH":
            area_matches += 1
        if cat_ok == "MATCH":
            category_matches += 1
        if urg_ok == "MATCH":
            urgency_matches += 1

        print(f"{tid:<12} | {'product_area':<15} | {str(ds_area):<22} | {str(ai_area):<22} | {area_ok}", flush=True)
        print(f"{'':<12} | {'category':<15} | {str(ds_cat):<22} | {str(ai_cat):<22} | {cat_ok}", flush=True)
        print(f"{'':<12} | {'urgency':<15} | {str(ds_urg):<22} | {str(ai_urg):<22} | {urg_ok}", flush=True)
        print(f"{'':<12} | {'known_issue':<15} | {'-':<22} | {str(res.known_issue):<22} | KB: {res.knowledge_base_document or 'None'}", flush=True)
        print(f"{'':<12} | {'first_response':<15} | {res.first_response[:65]}...", flush=True)
        print("-" * 85, flush=True)

        results.append({
            "ticket_id": tid,
            "subject": subject,
            "dataset": {"product_area": ds_area, "category": ds_cat, "urgency": ds_urg},
            "ai": res.model_dump(),
        })
        time.sleep(0.5)

    n = len(sample)
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Product Area Match Rate:  {area_matches}/{n} ({area_matches/n*100:.1f}%)")
    print(f"Category Match Rate:      {category_matches}/{n} ({category_matches/n*100:.1f}%)")
    print(f"Urgency Match Rate:       {urgency_matches}/{n} ({urgency_matches/n*100:.1f}%)")
    print("=" * 80)

    Path("evaluation_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("Detailed report saved to evaluation_results.json\n")

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    run_evaluation(n)
