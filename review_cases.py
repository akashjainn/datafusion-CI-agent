"""Review proposals from build_cases.py one at a time and write confirmed
labels to cases.jsonl. You decide every category and write every cause.

Keys at the category prompt:
    Enter      accept the proposed category
    1-8        pick a category by number
    s          skip (stays proposed, shown again next time)
    d          drop (marked rejected, never shown again)
    q          quit
"""
import json
import os

from build_cases import CATEGORIES, CASES, PROPOSED


def load(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def save_all(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def main():
    proposals = load(PROPOSED)
    pending = [p for p in proposals if p["status"] == "proposed"]
    print(f"{len(pending)} proposals to review\n")

    for p in pending:
        prop = p["proposal"]
        print("=" * 80)
        print(f"PR #{p['meta']['pr']}  run {p['input']['run_id']}  "
              f"[{p['input']['workflow']}]  {p['input']['pr_title']}")
        print(f"proposed: {prop['category']} / {prop['subtype']}")
        if p.get("review_note"):
            print(f"note: {p['review_note']}")
        print(f"fix commit: {prop['fix_message']}")
        print("fix files:")
        for f in p["label"]["fix_files"]:
            print(f"  {f}")
        print("-" * 80)
        print(prop["evidence"][-1500:])
        print("-" * 80)
        for i, c in enumerate(CATEGORIES, 1):
            print(f"  {i}. {c}", end="")
        print()

        choice = input(f"category [Enter={prop['category']}]: ").strip().lower()
        if choice == "q":
            break
        if choice == "s":
            continue
        if choice == "d":
            p["status"] = "rejected"
            save_all(PROPOSED, proposals)
            continue
        if choice == "":
            category = prop["category"]
        elif choice.isdigit() and 1 <= int(choice) <= len(CATEGORIES):
            category = CATEGORIES[int(choice) - 1]
        else:
            print("unrecognized; skipping")
            continue
        if category not in CATEGORIES:
            print(f"'{category}' is a proposal-only bucket; pick 1-8")
            continue

        cause = ""
        while not cause:
            cause = input("cause (one sentence, required): ").strip()
        difficulty = input("difficulty [easy/medium/hard, Enter=medium]: ").strip() or "medium"

        case = {
            "id": p["id"],
            "input": p["input"],
            "label": dict(p["label"], category=category, cause=cause),
            "difficulty": difficulty,
            "status": "ready",
            "meta": dict(p["meta"], proposed=prop["category"],
                         subtype=prop["subtype"]),
        }
        with open(CASES, "a") as f:
            f.write(json.dumps(case) + "\n")
        p["status"] = "labeled"
        save_all(PROPOSED, proposals)
        print(f"saved {case['id']} as {category}\n")


if __name__ == "__main__":
    main()