"""Checks for build_cases.py: classifier, fix check, breaking-commit walk.

Usage:
    python3 test_build_cases.py          # offline checks
    python3 test_build_cases.py --live   # also PR #26266 on GitHub (~10 gh calls)

The classifier check reads logs/38646208310.log (Security audit, PR #26266).
"""
import os
import sys

import build_cases as b

HERE = os.path.dirname(os.path.abspath(__file__))
FAILED = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILED.append(name)


def run(wf, conclusion):
    return {"workflowName": wf, "conclusion": conclusion}


def test_classifier():
    log = open(os.path.join(HERE, "logs", "38646208310.log")).read()
    check("security audit log -> upstream_dependency",
          b.classify(log)[:2], ("upstream_dependency", "cargo_audit"))
    check("install-action alone is not a snapshot failure",
          b.classify("Download action repository 'taiki-e/install-action@861a07'")[0],
          "other")
    check("cargo downloading the insta crate is not a snapshot failure",
          b.classify("  Downloaded icu_properties_data v2.2.0\n  Downloaded insta v1.49.0\n")[0],
          "other")
    check("insta legacy-format warning on a passing test is not a failure",
          b.classify("Snapshot test passes but the existing value is in a legacy format. "
                     "Please run `cargo insta test --force-update-snapshots` to update")[0],
          "other")
    log = open(os.path.join(HERE, "logs", "38569803182.log")).read()  # PR #26212
    cat, sub, evidence = b.classify(log)
    check("unmatched log: evidence shows the failing step, not post-job cleanup",
          (cat, ".profraw" in evidence and "##[error]Process completed" in evidence),
          ("other", True))
    check("insta snapshot still detected",
          b.classify("error: snapshot assertion for 'plan' failed")[:2],
          ("test_expectation", "snapshot"))
    check("cargo insta still detected",
          b.classify("run `cargo insta review` to accept")[:2],
          ("test_expectation", "snapshot"))


def test_workflow_status():
    status = getattr(b, "workflow_status", None)
    if status is None:
        check("workflow_status exists", False, True)
        return
    check("cancelled is not success",
          status([run("Rust", "cancelled"), run("Dev", "success")], "Rust"), None)
    check("missing workflow is not success",
          status([run("Dev", "success")], "Rust"), None)
    check("success", status([run("Rust", "success")], "Rust"), "success")
    check("any failure wins",
          status([run("Rust", "success"), run("Rust", "failure")], "Rust"), "failure")


def test_breaking_walk():
    walk = getattr(b, "find_breaking", None)
    if walk is None:
        check("find_breaking exists", False, True)
        return
    commits = [{"oid": x} for x in "ABCDEF"]
    ci = {"A": [run("W", "success")], "B": [run("W", "cancelled")],
          "C": [run("W", "failure")], "D": [],  # never ran
          "E": [run("W", "failure")], "F": [run("W", "failure")]}
    check("walks past cancelled/missing, stops at last success",
          walk(commits, 5, "W", ci.get), "C")
    check("head is breaking when previous commit passed",
          walk(commits, 2, "W", ci.get), "C")
    check("no earlier success: earliest failure",
          walk(commits[2:], 3, "W", ci.get), "C")


def test_fixing_walk():
    walk = getattr(b, "find_fixing", None)
    if walk is None:
        check("find_fixing exists", False, True)
        return

    def commit(oid, headline="change"):
        return {"oid": oid, "messageHeadline": headline}

    ci = {"H": [run("W", "failure")], "X": [run("W", "cancelled")],
          "N": [],  # never ran
          "S": [run("W", "success"), run("Dev", "success")],
          "F": [run("W", "failure")],
          "O": [run("W", "success"), run("Dev", "failure")]}
    check("immediate next commit passes",
          walk([commit("H"), commit("S")], 0, "W", ci.get), "S")
    check("walks past cancelled and missing to the first success",
          walk([commit("H"), commit("X"), commit("N"), commit("S")], 0, "W", ci.get), "S")
    check("a later failure ends the search",
          walk([commit("H"), commit("X"), commit("F"), commit("S")], 0, "W", ci.get), None)
    check("a merge from main before the fix ends the search",
          walk([commit("H"), commit("X", "Merge branch 'main' into x"), commit("S")],
               0, "W", ci.get), None)
    check("no later passing commit",
          walk([commit("H"), commit("X")], 0, "W", ci.get), None)
    check("another workflow failing on the fix rejects it",
          walk([commit("H"), commit("O")], 0, "W", ci.get), None)


def test_prs_on_branch_shape():
    calls = []
    fake = {"data": {"repository": {"pullRequests": {"nodes": [
        {"number": 7, "commits": {"nodes": [
            {"commit": {"oid": "aaa", "messageHeadline": "first"}},
            {"commit": {"oid": "bbb", "messageHeadline": "second"}}]}}]}}}}
    real = b.gh
    b.gh = lambda *a, **k: calls.append(a) or fake
    try:
        check("prs_on_branch maps GraphQL nodes",
              b.prs_on_branch("123"),
              [{"number": 7, "commits": [{"oid": "aaa", "messageHeadline": "first"},
                                         {"oid": "bbb", "messageHeadline": "second"}]}])
        check("find_pr finds the commit", b.find_pr("bbb", "123", {})[0], 7)
        check("branch sent as a string (-f)",
              calls[0][calls[0].index("head=123") - 1], "-f")
    finally:
        b.gh = real


def test_live_pr_26266():
    found = b.find_pr("3991012d58698a1c3f8f09566a521a3a165c1450",
                      "spark-stack", {})
    pr, commits = found
    i = [c["oid"] for c in commits].index("3991012d58698a1c3f8f09566a521a3a165c1450")
    cache = {}
    got = b.find_breaking(commits, i, "Security audit",
                          lambda oid: b.commit_runs(oid, cache))
    check("PR #26266 breaking commit", got[:9], "72372a473")

    # PR #26315: Rust was cancelled on the next commit and passed on the one after.
    # Not `gh pr view --json commits`: it fetches every commit's authors and
    # costs ~30 GraphQL points. The run's branch comes from REST.
    run_id = "38595518292"  # the failing Rust run at a18cef27a
    head = b.gh("run", "view", run_id, "--repo", b.REPO,
                "--json", "headSha,headBranch", required=True)
    pr, commits = b.find_pr(head["headSha"], head["headBranch"], {})
    i = [c["oid"] for c in commits].index(head["headSha"])
    got = b.find_fixing(commits, i, "Rust", lambda oid: b.commit_runs(oid, cache))
    check("PR #26315 fixing commit", (got or "")[:9], "62154dc1b")


if __name__ == "__main__":
    test_classifier()
    test_workflow_status()
    test_breaking_walk()
    test_fixing_walk()
    test_prs_on_branch_shape()
    if "--live" in sys.argv and hasattr(b, "find_fixing"):
        test_live_pr_26266()
    print(f"\n{len(FAILED)} failed" if FAILED else "\nall passed")
    sys.exit(1 if FAILED else 0)
