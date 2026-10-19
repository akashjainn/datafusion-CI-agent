import json, subprocess, sys

REPO = "apache/datafusion"

def gh(*args):
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else None

for line in open("failed_runs.tsv"):
    run_id, sha, branch, workflow, title = line.rstrip("\n").split("\t")
    prs = gh("pr", "list", "--repo", REPO, "--state", "all", "--search", sha, "--json", "number")
    if not prs:
        continue
    pr = prs[0]["number"]
    commits = (gh("pr", "view", str(pr), "--repo", REPO, "--json", "commits") or {}).get("commits", [])
    shas = [c["oid"] for c in commits]
    if sha not in shas or shas.index(sha) == len(shas) - 1:
        continue  # force-pushed away, or last commit (no fix)
    nxt = commits[shas.index(sha) + 1]
    if nxt["messageHeadline"].lower().startswith("merge"):
        continue  # fix may have come from main
    runs = gh("run", "list", "--repo", REPO, "--commit", nxt["oid"], "--json", "conclusion") or []
    failed = [r for r in runs if r["conclusion"] == "failure"]
    if runs and not failed:
        print(f"PR #{pr}\trun {run_id}\t{workflow}\t{sha[:9]} -> {nxt['oid'][:9]}\t{nxt['messageHeadline'][:50]}")
    sys.stdout.flush()