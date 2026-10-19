"""Find failed DataFusion PR runs that have a fixing commit, collect the
evidence, and propose a category. Writes proposals; it does NOT write labels.
Confirm each proposal with review_cases.py before it goes into cases.jsonl.

Usage:
    python3 build_cases.py --limit 50
    python3 build_cases.py --limit 50 --workflow Rust
    python3 build_cases.py --limit 50 --before 2026-10-15T14:51:36Z

`--limit` always takes the newest runs. To go further back, pass `--before`
the "oldest run" time printed at the end of the previous batch.

Any failing gh call stops the run with gh's error, rather than silently
skipping the candidate. Rerunning resumes: runs already proposed are skipped.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

REPO = "apache/datafusion"
LOG_DIR = "logs"
PROPOSED = "cases_proposed.jsonl"
CASES = "cases.jsonl"
# Most recent PRs fetched per head branch (fork branches named `main` are
# common).
PR_LOOKUP_LIMIT = 30
PRS_ON_BRANCH_QUERY = """
query($owner: String!, $name: String!, $head: String!, $limit: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequests(headRefName: $head, first: $limit,
                 orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes { number commits(first: 100) { nodes { commit { oid messageHeadline } } } }
    }
  }
}"""

# Ordered: first match wins. Each rule: (category, subtype, regex).
# Built from the cases labeled by hand so far. Extend as new patterns show up.
RULES = [
    ("upstream_dependency", "cargo_audit", r"error: \d+ vulnerabilit(y|ies) found"),
    ("lint", "clippy_allow", r"error: #\[allow\] attribute found"),
    ("lint", "clippy", r"(warning|error): .*\n.*= help: for further information visit https://rust-lang\.github\.io/rust-clippy"),
    ("lint", "clippy", r"clippy::"),
    ("lint", "spelling", r"error: `[^`]+` should be `[^`]+`"),
    ("lint", "fmt", r"Diff in .*\.rs"),
    ("lint", "prettier", r"\[warn\].*Code style issues found|prettier --check"),
    ("lint", "license_header", r"(?i)missing (apache )?license header"),
    ("compile_error", "rustc", r"error\[E\d{4}\]"),
    ("compile_error", "rustc", r"error: could not compile"),
    ("test_expectation", "sqllogictest", r"External error: \d+ errors? in file .*\.slt"),
    ("test_expectation", "sqllogictest", r"\.slt:\d+"),
    # Only insta's failure output. Not a bare `insta` (matches `install-action`
    # in job setup and `Downloaded insta v1.x` in every Rust build) and not
    # `cargo insta` (also in a legacy-format warning on passing tests).
    ("test_expectation", "snapshot", r"(?i)snapshot assertion|Snapshot Summary|cargo insta review"),
    ("test_failure", "rust_panic", r"panicked at .*\.rs:\d+:\d+"),
    ("flaky_infra", "infra", r"(?i)no space left on device|connection reset|timed out after|The runner has received a shutdown signal|exit code 137"),
]

CATEGORIES = ["lint", "compile_error", "test_expectation", "test_logic",
              "code_bug", "upstream_dependency", "flaky_infra", "other"]


def gh(*args, as_json=True, required=False, expected_errors=()):
    """Run gh. On failure, return None, or exit with gh's error if required.

    A required call whose error contains one of expected_errors returns None
    instead of exiting.
    """
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if out.returncode != 0:
        if required and not any(e in out.stderr for e in expected_errors):
            sys.exit(f"gh {' '.join(args)} failed:\n  {out.stderr.strip()}")
        return None
    if not as_json:
        return out.stdout
    return json.loads(out.stdout) if out.stdout.strip() else None


def load_jsonl(path):
    """Read a JSONL file, stopping with the exact bad line instead of a traceback."""
    rows = []
    if not os.path.exists(path):
        return rows
    for n, line in enumerate(open(path), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            sys.exit(f"{path} line {n} is not valid JSON ({e.msg}):\n  {line.rstrip()[:120]}\n"
                     f"Each case must be one complete JSON object on a single line.")
    return rows


def seen_run_ids_and_prs():
    runs, prs = set(), set()
    for path in (PROPOSED, CASES):
        for c in load_jsonl(path):
            run_id = c.get("input", {}).get("run_id")
            if run_id is not None:
                runs.add(str(run_id))
            if c.get("meta", {}).get("pr"):
                prs.add(c["meta"]["pr"])
    return runs, prs


def classify(log):
    for category, subtype, pattern in RULES:
        m = re.search(pattern, log)
        if m:
            start = max(0, log.rfind("\n", 0, max(0, m.start() - 600)))
            snippet = log[start:m.end() + 1200]
            return category, subtype, snippet
    # No rule matched: show the lead-up to the step GitHub marked as failed.
    # The log's tail is usually post-job cleanup, after the error.
    err = log.find("##[error]")
    if err >= 0:
        return "other", "unmatched", log[max(0, err - 1800):err + 200]
    return "other", "unmatched", log[-2000:]


def strip_log_prefix(raw):
    # `gh run view --log-failed` prefixes each line with "job\tstep\ttimestamp ".
    lines = []
    for line in raw.splitlines():
        parts = line.split("\t", 2)
        text = parts[2] if len(parts) == 3 else line
        text = re.sub(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ", "", text)
        lines.append(text)
    return "\n".join(lines)


def prs_on_branch(branch):
    """Newest PRs (any state) with this head branch, with commit oids and
    headlines, as [{"number", "commits": [{"oid", "messageHeadline"}]}].

    A direct GraphQL query costs 1 point. `gh pr list --json commits` also
    fetches every commit's authors and costs ~30, which used up the 5,000
    points/hour in a few batches. Only a PR's first 100 commits are fetched.
    """
    owner, name = REPO.split("/")
    # -f sends strings as-is; -F would turn a branch named e.g. `123` into a number.
    data = gh("api", "graphql", "-f", f"owner={owner}", "-f", f"name={name}",
              "-f", f"head={branch}", "-F", f"limit={PR_LOOKUP_LIMIT}",
              "-f", "query=" + PRS_ON_BRANCH_QUERY, required=True)
    nodes = data["data"]["repository"]["pullRequests"]["nodes"]
    return [{"number": p["number"],
             "commits": [c["commit"] for c in p["commits"]["nodes"]]}
            for p in nodes]


def find_pr(sha, branch, prs_by_branch):
    """Return (number, commits) of the PR containing sha, or None.

    Looks up PRs by head branch, which returns their commits in the same call
    (one call per branch instead of a search plus `pr view` per commit). Fork
    PRs often share a branch name such as `main`, so the match is by commit,
    not by branch alone. None usually means the commit was force-pushed away.
    """
    if branch not in prs_by_branch:
        prs_by_branch[branch] = prs_on_branch(branch)
    for p in prs_by_branch[branch]:
        if any(c["oid"] == sha for c in p["commits"]):
            return p["number"], p["commits"]
    return None


def commit_runs(oid, cache):
    """All workflow runs for a commit, fetched once per commit."""
    if oid not in cache:
        cache[oid] = gh("run", "list", "--repo", REPO, "--commit", oid,
                        "--limit", "100", "--json", "workflowName,conclusion",
                        required=True) or []
    return cache[oid]


def workflow_status(runs, workflow):
    """'failure', 'success', or None if the workflow was cancelled, skipped,
    or never ran on the commit (so its result there is unknown)."""
    conclusions = {x["conclusion"] for x in runs if x["workflowName"] == workflow}
    if "failure" in conclusions:
        return "failure"
    if "success" in conclusions:
        return "success"
    return None


def find_breaking(commits, i, workflow, runs_for):
    """Earliest commit of the failing streak that ends at commits[i].

    Walks back past commits where the workflow's result is unknown and stops
    at the last commit where it succeeded. runs_for(oid) returns a commit's
    runs. The error can change within a streak (e.g. a merge from main brings
    a new failure), which this does not detect.
    """
    breaking = commits[i]["oid"]
    for c in reversed(commits[:i]):
        status = workflow_status(runs_for(c["oid"]), workflow)
        if status == "success":
            break
        if status == "failure":
            breaking = c["oid"]
    return breaking


def find_fixing(commits, i, workflow, runs_for):
    """First later commit where the workflow failing at commits[i] passes.

    Walks forward past commits where the workflow's result is unknown
    (cancelled, skipped, never ran) and returns the first one where it
    succeeded, or None if it fails again first, a merge comes first (the fix
    may have come from main), or no later commit passes. The fixing commit
    must also have no failing workflow at all. runs_for(oid) returns a
    commit's runs.
    """
    for c in commits[i + 1:]:
        if c["messageHeadline"].lower().startswith("merge"):
            return None
        runs = runs_for(c["oid"])
        status = workflow_status(runs, workflow)
        if status == "failure":
            return None
        if status == "success":
            if any(x["conclusion"] == "failure" for x in runs):
                return None
            return c["oid"]
    return None


def pr_for_commit(sha, branch, prs_by_branch, seen_prs):
    """Return (pr, commits) for a failing commit, or None to skip it."""
    found = find_pr(sha, branch, prs_by_branch)
    if not found:
        print(f"  no PR on branch {branch!r} contains {sha[:9]} "
              f"(force-pushed?); skipped", file=sys.stderr)
        return None
    if found[0] in seen_prs:
        return None  # one case per PR
    return found


def created_before(value):
    """argparse type for --before. gh silently returns no runs for a bad date."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            datetime.strptime(value, fmt)
            return value
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        f"{value!r} is not YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--workflow", default=None)
    ap.add_argument("--before", type=created_before, default=None,
                    help="only runs created at or before this time (UTC)")
    args = ap.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)
    seen_runs, seen_prs = seen_run_ids_and_prs()

    cmd = ["run", "list", "--repo", REPO, "--status", "failure",
           "--event", "pull_request", "--limit", str(args.limit),
           "--json",
           "databaseId,headSha,headBranch,workflowName,displayTitle,createdAt"]
    if args.workflow:
        cmd += ["--workflow", args.workflow]
    if args.before:
        # Inclusive: several runs often share a second, and a batch can end
        # partway through them. Runs already proposed are skipped anyway.
        cmd += ["--created", f"<={args.before}"]
    runs = gh(*cmd, required=True) or []
    print(f"{len(runs)} failed runs to check", file=sys.stderr)

    # Several workflows fail on the same commit. The PR checks depend only on
    # the commit, so they run once per commit; the log is fetched per run, so
    # a run without a log falls through to the next run on that commit.
    pr_by_sha = {}
    prs_by_branch = {}
    runs_cache = {}

    def runs_for(oid):
        return commit_runs(oid, runs_cache)

    added = 0
    for r in runs:
        run_id, sha = str(r["databaseId"]), r["headSha"]
        workflow = r["workflowName"]
        if run_id in seen_runs:
            continue
        if sha not in pr_by_sha:
            pr_by_sha[sha] = pr_for_commit(sha, r["headBranch"], prs_by_branch,
                                           seen_prs)
        if pr_by_sha[sha] is None:
            continue
        pr, commits = pr_by_sha[sha]
        if pr in seen_prs:
            continue  # one case per PR
        i = [c["oid"] for c in commits].index(sha)
        fixing = find_fixing(commits, i, workflow, runs_for)
        if fixing is None:
            continue
        nxt = next(c for c in commits if c["oid"] == fixing)

        # A run that failed before any job started (e.g. a workflow error)
        # has no log, so there is nothing to build a case from.
        raw = gh("run", "view", run_id, "--repo", REPO, "--log-failed",
                 as_json=False, required=True,
                 expected_errors=("log not found",))
        if raw is None:
            print(f"  run {run_id} [{r['workflowName']}] has no log; skipped",
                  file=sys.stderr)
            continue
        if not raw:
            sys.exit(f"run {run_id}: --log-failed returned an empty log")
        log = strip_log_prefix(raw)
        with open(os.path.join(LOG_DIR, f"{run_id}.log"), "w") as f:
            f.write(log)

        cmp = gh("api", f"repos/{REPO}/compare/{sha}...{nxt['oid']}",
                 required=True) or {}
        fix_files = [f["filename"] for f in cmp.get("files", [])]

        breaking = find_breaking(commits, i, workflow, runs_for)

        category, subtype, snippet = classify(log)
        if any(p.endswith(("Cargo.toml", "Cargo.lock")) for p in fix_files):
            subtype += "+cargo_change"

        case = {
            "id": f"df-{run_id}",
            "input": {"run_id": int(run_id), "pr_title": r["displayTitle"],
                      "head_sha": sha, "workflow": r["workflowName"]},
            "label": {"category": None, "breaking_commit": breaking,
                      "fixing_commit": nxt["oid"], "fix_files": fix_files,
                      "cause": None},
            "proposal": {"category": category, "subtype": subtype,
                         "evidence": snippet[-3000:],
                         "fix_message": nxt["messageHeadline"]},
            "meta": {"pr": pr},
            "status": "proposed",
        }
        with open(PROPOSED, "a") as f:
            f.write(json.dumps(case) + "\n")
        seen_prs.add(pr)
        added += 1
        print(f"+ PR #{pr} run {run_id} -> {category}/{subtype} "
              f"({len(fix_files)} fix files)", file=sys.stderr)

    print(f"done: {added} new proposals in {PROPOSED}", file=sys.stderr)
    if runs:
        print(f"oldest run: {runs[-1]['createdAt']} "
              f"(next batch: --before {runs[-1]['createdAt']})", file=sys.stderr)


if __name__ == "__main__":
    main()