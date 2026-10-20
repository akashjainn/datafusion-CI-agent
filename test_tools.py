"""Smoke and leakage checks for tools.py, over every ready case.

For each case it calls every tool the way the agent would (from `input` only)
and checks that:
  - every tool returns text and none returns a harness error;
  - no output contains the case's fixing commit SHA (the answer);
  - the PR diff is not empty (an empty diff means the base commit is wrong).

Usage:
    python3 test_tools.py
    DATAFUSION_REPO=/path/to/datafusion python3 test_tools.py
"""
import os
import sys

from build_cases import CASES, load_jsonl
from tools import (get_log, get_pr_diff, make_context, read_file, run_tool,
                   search_code, tool_schemas)

REPO_DIR = os.environ.get("DATAFUSION_REPO",
                          "/Users/akashjain/Documents/ai/oss/datafusion")
HARNESS_ERRORS = ("not a git repository", "could not find the PR's base",
                  "timed out", "bad arguments", "unknown tool")


def check_case(case):
    problems = []
    ctx = make_context(case, REPO_DIR)
    fix_sha = case["label"].get("fixing_commit") or ""
    calls = {
        "get_log search": get_log(ctx, pattern=r"error|panicked|FAILED|##\[error\]"),
        "get_log page": get_log(ctx, start=1, limit=50),
        "get_pr_diff files": get_pr_diff(ctx),
        "search_code": search_code(ctx, "fn main", path_glob="*.rs"),
    }
    for f in case["label"].get("fix_files") or []:
        calls[f"read_file {f}"] = read_file(ctx, f, start=1, limit=20)
        calls[f"get_pr_diff {f}"] = get_pr_diff(ctx, path=f)

    for name, out in calls.items():
        if not isinstance(out, str):
            problems.append(f"{name}: returned {type(out).__name__}, not str")
            continue
        for e in HARNESS_ERRORS:
            if e in out:
                problems.append(f"{name}: {out[:150]}")
        if fix_sha and (fix_sha in out or fix_sha[:9] in out):
            problems.append(f"{name}: LEAK - output contains fixing commit {fix_sha[:9]}")
    if "Files changed by the PR" in calls["get_pr_diff files"] and \
            calls["get_pr_diff files"].count("\n") < 2:
        problems.append("get_pr_diff files: empty diff - base commit is probably wrong")
    return problems


def main():
    names = [s["name"] for s in tool_schemas()]
    assert len(names) == len(set(names)), f"duplicate tool names: {names}"
    assert run_tool(None, "nope", {}).startswith("unknown tool")

    cases = [c for c in load_jsonl(CASES) if c.get("status") == "ready"]
    failed = 0
    for c in cases:
        problems = check_case(c)
        if problems:
            failed += 1
            print(f"FAIL {c['id']}")
            for p in problems:
                print(f"     {p}")
        else:
            print(f"ok   {c['id']}")
    print(f"\n{len(cases) - failed}/{len(cases)} cases passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
