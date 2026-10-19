"""Re-point a case at the run whose log actually contains its failure.

Two cases were labeled from one run of a PR but saved with a different run's
id. For each, this finds the PR's failing run whose log contains the case's
signature, then the next commit whose same workflow succeeded, and the files
changed between them. Category and cause are kept as labeled.

Usage:
    python3 fix_runs.py           # dry run: show what would change
    python3 fix_runs.py --write   # apply (backs up cases.jsonl first)
"""
import json
import shutil
import sys

from build_cases import CASES, REPO, gh, load_jsonl, strip_log_prefix

# case id -> (PR number, text that must appear in the failing run's log)
TARGETS = {
    "df-38329077088": (26132, "at test_files/predicates.slt"),
    "df-38212522570": (26009, "optimized_filter_after_projection"),
}


def find_run(pr, signature):
    commits = (gh("pr", "view", str(pr), "--repo", REPO, "--json", "commits",
                  required=True) or {}).get("commits", [])
    for i, c in enumerate(commits):
        runs = gh("run", "list", "--repo", REPO, "--commit", c["oid"],
                  "--json", "databaseId,workflowName,conclusion") or []
        for r in runs:
            if r["conclusion"] != "failure":
                continue
            raw = gh("run", "view", str(r["databaseId"]), "--repo", REPO,
                     "--log-failed", as_json=False)
            if not raw or signature not in raw:
                continue
            log = strip_log_prefix(raw)
            # first later commit where the same workflow succeeded
            for nxt in commits[i + 1:]:
                nruns = gh("run", "list", "--repo", REPO, "--commit", nxt["oid"],
                           "--json", "workflowName,conclusion") or []
                same = [x for x in nruns if x["workflowName"] == r["workflowName"]]
                if same and all(x["conclusion"] == "success" for x in same):
                    cmp = gh("api", f"repos/{REPO}/compare/{c['oid']}...{nxt['oid']}") or {}
                    files = [f["filename"] for f in cmp.get("files", [])]
                    return r, c, nxt, files, log
            print(f"  run {r['databaseId']} matches but no later green "
                  f"{r['workflowName']} run; skipping")
    return None


def main():
    write = "--write" in sys.argv
    cases = load_jsonl(CASES)
    by_id = {c["id"]: c for c in cases}
    for cid, (pr, sig) in TARGETS.items():
        c = by_id.get(cid)
        if not c:
            print(f"{cid}: not in {CASES}; skipping")
            continue
        print(f"{cid} (PR #{pr}): looking for a failing run containing {sig!r}")
        found = find_run(pr, sig)
        if not found:
            print("  NOT FOUND - leave this case as is and relabel it by hand")
            continue
        r, head, nxt, files, log = found
        print(f"  run {r['databaseId']} [{r['workflowName']}] on {head['oid'][:9]}")
        print(f"  fixed by {nxt['oid'][:9]} '{nxt['messageHeadline']}'")
        print(f"  fix files: {files}")
        new_id = f"df-{r['databaseId']}"
        c["id"] = new_id
        c["input"].update(run_id=r["databaseId"], head_sha=head["oid"],
                          workflow=r["workflowName"])
        c["label"].update(breaking_commit=head["oid"], fixing_commit=nxt["oid"],
                          fix_files=files)
        c.setdefault("meta", {})["repointed_from"] = cid
        if write:
            with open(f"logs/{r['databaseId']}.log", "w") as f:
                f.write(log)
    if write:
        shutil.copy(CASES, CASES + ".bak")
        with open(CASES, "w") as f:
            for c in cases:
                f.write(json.dumps(c) + "\n")
        print(f"wrote {CASES} (backup at {CASES}.bak)")
    else:
        print("dry run; rerun with --write to apply")


if __name__ == "__main__":
    main()
