import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.retrieval import load_kb
from src.triage import triage_ticket

def main():
    kb = load_kb(Path("knowledge-base"))
    tickets = json.loads(Path("data/tickets.json").read_text(encoding="utf-8"))

    # Select 3 tickets:
    # 1. TKT-10005 (SSO authentication issue)
    # 2. TKT-10008 (Billing seat discrepancy / overage query)
    # 3. TKT-10000 (DataBridge Pro bulk archive feature request)
    
    t1 = next((t for t in tickets if t["ticket_id"] == "TKT-10005"), tickets[5])
    t2 = next((t for t in tickets if t["ticket_id"] == "TKT-10008"), tickets[8])
    t3 = next((t for t in tickets if t["ticket_id"] == "TKT-10000"), tickets[0])

    selected = [t1, t2, t3]
    output = []

    for t in selected:
        chunks = kb.retrieve(f"{t['subject']} {t['body']}", top_k=4)
        res = triage_ticket(t['subject'], t['body'], chunks)
        output.append({
            "ticket_id": t["ticket_id"],
            "subject": t["subject"],
            "body": t["body"],
            "dataset_ground_truth": {
                "product": t.get("product"),
                "product_area": t.get("product_area"),
                "category": t.get("category"),
                "urgency": t.get("urgency")
            },
            "ai_triage_result": res.model_dump()
        })

    Path("results_3_tickets.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

if __name__ == "__main__":
    main()
