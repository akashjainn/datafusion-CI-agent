"""Make every case's head_sha available in a local DataFusion clone, so tools
can read files at that commit with `git show <sha>:<path>`.

Most PRs come from forks, so their commits exist only under GitHub's
refs/pull/<N>/head. Each PR is fetched into refs/ci-cases/pr/<N> (a real ref,
so `git gc` will not prune the commits). A head_sha that was force-pushed out
of the PR is fetched by SHA (GitHub still serves it) into refs/ci-cases/sha/<sha>.
Every head_sha is then checked with `git cat-file -e`. Exits nonzero if any
case's commit is still missing.

Usage:
    python3 fetch_case_commits.py
    python3 fetch_case_commits.py --repo-dir ~/Documents/ai/oss/datafusion
"""
import argparse
import os
import subprocess
import sys

from build_cases import CASES, load_jsonl

DEFAULT_REPO_DIR = "~/Documents/ai/oss/datafusion"
REMOTE = "origin"


def git(repo_dir, *args):
    return subprocess.run(["git", "-C", repo_dir, *args],
                          capture_output=True, text=True)


def has_commit(repo_dir, sha):
    return git(repo_dir, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def fetch_pr(repo_dir, pr):
    ref = f"refs/ci-cases/pr/{pr}"
    out = git(repo_dir, "fetch", "--no-tags", REMOTE, f"+refs/pull/{pr}/head:{ref}")
    if out.returncode != 0:
        return f"fetch of PR #{pr} failed: {out.stderr.strip()}"
    return None


def fetch_sha(repo_dir, sha):
    out = git(repo_dir, "fetch", "--no-tags", REMOTE, f"{sha}:refs/ci-cases/sha/{sha}")
    if out.returncode != 0:
        return f"fetch of {sha[:9]} by SHA failed: {out.stderr.strip()}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default=DEFAULT_REPO_DIR)
    args = ap.parse_args()
    repo_dir = os.path.expanduser(args.repo_dir)
    if git(repo_dir, "rev-parse", "--git-dir").returncode != 0:
        sys.exit(f"{repo_dir} is not a git repository")

    cases = load_jsonl(CASES)
    problems = []
    fetched = set()
    for c in cases:
        cid, sha = c["id"], c["input"]["head_sha"]
        pr = c.get("meta", {}).get("pr")
        if has_commit(repo_dir, sha):
            print(f"{cid}: {sha[:9]} present")
            continue
        if not pr:
            problems.append(f"{cid}: {sha[:9]} missing and case has no meta.pr")
            continue
        if pr not in fetched:
            err = fetch_pr(repo_dir, pr)
            fetched.add(pr)
            if err:
                problems.append(f"{cid}: {err}")
                continue
        if has_commit(repo_dir, sha):
            print(f"{cid}: {sha[:9]} fetched (PR #{pr})")
            continue
        # Not reachable from the PR's current head: force-pushed away.
        err = fetch_sha(repo_dir, sha)
        if err:
            problems.append(f"{cid}: not in PR #{pr}'s head; {err}")
        elif has_commit(repo_dir, sha):
            print(f"{cid}: {sha[:9]} fetched by SHA (not in PR #{pr}'s current head)")
        else:
            problems.append(f"{cid}: {sha[:9]} still missing after fetch by SHA")

    print(f"\n{len(cases) - len(problems)}/{len(cases)} cases have head_sha locally.")
    if problems:
        print("Missing:")
        for p in problems:
            print(f"  {p}")
        sys.exit(1)


if __name__ == "__main__":
    main()
