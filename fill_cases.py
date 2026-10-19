"""Fill missing fields in cases.jsonl from GitHub: meta.pr, pr_title,
fix_files, and status. Never changes category or cause.

Usage:
    python3 fill_cases.py           # show what would change
    python3 fill_cases.py --write   # apply (backs up to cases.jsonl.bak first)
"""
import json
import shutil
import sys

from build_cases import CASES, REPO, gh, load_jsonl


def fill(c):
    notes = []
    inp, label, meta = c["input"], c["label"], c.setdefault("meta", {})
    sha = inp["head_sha"]

    # Repair a pr_title key that got overwritten by a pasted command.
    for k in list(inp):
        if k != "pr_title" and k.startswith("gh pr list"):
            inp["pr_title"] = inp.pop(k)
            notes.append("repaired pr_title key")

    run = gh("run", "view", str(inp["run_id"]), "--repo", REPO,
             "--json", "headSha,displayTitle,conclusion") or {}
    if run and run.get("headSha") != sha:
        notes.append(f"WARNING run's headSha is {run.get('headSha')}, not {sha}")
    if run and run.get("conclusion") != "failure":
        notes.append(f"WARNING run conclusion is {run.get('conclusion')}")
    if (not inp.get("pr_title") or inp["pr_title"].startswith("<")) and run:
        inp["pr_title"] = run["displayTitle"]
        notes.append("filled pr_title")

    if not meta.get("pr"):
        prs = gh("pr", "list", "--repo", REPO, "--state", "all",
                 "--search", sha, "--json", "number")
        if prs:
            meta["pr"] = prs[0]["number"]
            notes.append(f"filled pr #{meta['pr']}")
        else:
            notes.append("could not find PR")

    if label.get("fixing_commit") and not label.get("fix_files"):
        cmp = gh("api", f"repos/{REPO}/compare/{sha}...{label['fixing_commit']}") or {}
        files = [f["filename"] for f in cmp.get("files", [])]
        if files:
            label["fix_files"] = files
            notes.append(f"filled {len(files)} fix files: {', '.join(files)}")
        else:
            notes.append("compare returned no files")

    if not label.get("fixing_commit") and meta.get("pr"):
        pr = gh("pr", "view", str(meta["pr"]), "--repo", REPO,
                "--json", "state,mergeCommit") or {}
        merge = (pr.get("mergeCommit") or {}).get("oid")
        notes.append(f"no fixing commit; PR state {pr.get('state')}"
                     + (f", merged as {merge[:9]}" if merge else ""))

    ready = (label.get("fixing_commit") and label.get("fix_files")
             and label.get("category") and label.get("cause")
             and not inp.get("pr_title", "").startswith("<"))
    if ready and c.get("status") != "ready":
        c["status"] = "ready"
        notes.append("status -> ready")
    return notes


def main():
    write = "--write" in sys.argv
    cases = load_jsonl(CASES)
    for c in cases:
        notes = fill(c)
        print(f"{c['id']}: " + ("; ".join(notes) if notes else "nothing to fill"))
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