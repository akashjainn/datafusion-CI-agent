# DataFusion CI triage agent — eval dataset

## What this project is
A CI failure-triage agent for apache/datafusion. Given a failed GitHub Actions
run on a PR, the agent will diagnose it and return a structured answer:
failure category, the files the fix touched, and a one-paragraph explanation.

This folder currently holds the **eval dataset and the tooling that builds it**.
The agent itself does not exist yet.

## Hard rules
1. **Never write or change `label.category` or `label.cause`.** Akash decides
   every category and writes every cause by hand. Scripts may *propose* a
   category (in `proposal`), never set the label. If a script you write would
   set either field, stop and ask.
2. **Do not build the agent** (the model loop, tools, prompts, or eval
   harness). Akash is writing that himself. Data tooling only.
3. **Do not run `review_cases.py`.** It is interactive and is where Akash labels.
4. **Ask before writing to `cases.jsonl`.** Run any script that modifies it as
   a dry run first, show the diff, and wait for a yes. Scripts must back up
   the file before writing.
5. Target **Python 3.9** (the system python3). No `match`, no `X | Y` type
   unions, no 3.10+ stdlib features. Standard library only, plus the `gh` CLI.

## Files
- `cases.jsonl` — confirmed eval cases. One JSON object per line, nothing else
  (no notes, no code fences, no blank-line comments).
- `cases_proposed.jsonl` — candidates from `build_cases.py`, awaiting review.
- `logs/<run_id>.log` — failed-job logs, prefixes stripped. Later the agent's input.
- `build_cases.py` — finds failed PR runs that have a fixing commit, saves the
  log, gets fix files, proposes a category from regex rules (`RULES`).
- `review_cases.py` — interactive labeling (human only, see rule 3).
- `fill_cases.py` — fills `meta.pr`, `pr_title`, `fix_files`, and `status` on
  existing cases from GitHub. Dry run by default; `--write` applies.
- `test_build_cases.py` — checks for `build_cases.py` (classifier, fix check,
  breaking-commit walk). `python3 test_build_cases.py` runs offline;
  `--live` adds checks against real PRs (~10 `gh` calls). Run it after any
  change to `build_cases.py`. The classifier check needs
  `logs/38646208310.log`.
- `notes.md` — free-text notes (duplicates, oddities). Create it if missing.

`review_cases.py` and `fill_cases.py` import from `build_cases.py`; keep the
shared helpers (`gh`, `load_jsonl`, `CATEGORIES`, constants) there.

## Case schema
```json
{"id": "df-004",
 "input":  {"run_id": 38690227577, "pr_title": "...", "head_sha": "...", "workflow": "Rust"},
 "label":  {"category": "test_expectation", "breaking_commit": "...", "fixing_commit": "...",
            "fix_files": ["path/one.rs"], "cause": "one sentence"},
 "difficulty": "easy|medium|hard",
 "meta":   {"pr": 26090},
 "status": "ready|needs_fix|needs_fix_files|proposed|labeled|rejected"}
```
- `input` is what the agent will see. Everything in `label` is the answer and
  must never be passed to the agent.
- Only `status: "ready"` cases are used.

## Data rules
- `head_sha` = the commit the failing run tested. Always take it from
  `gh run view <run_id> --json headSha`; never type it by hand.
- `breaking_commit` = the earliest commit of the failing streak that ends at
  `head_sha`, for the same workflow. Walk back through the PR's commits, pass
  over commits where that workflow was cancelled, skipped, or never ran, and
  stop at the last commit where it succeeded. It is often not `head_sha`
  (PR #26266: head was a merge from main; the streak began at `72372a473`).
  The error can change within a streak (a merge from main can bring a new
  failure); the walk does not detect that, so check it when labeling.
- A workflow counts as passed on a commit only if it has a `success` run
  there. Cancelled, skipped, or missing is unknown, not passed.
- `fixing_commit` = the first later commit on the same PR where the failing
  workflow passed and no workflow failed. Walk forward past commits where
  that workflow was cancelled, skipped, or never ran (a new push often
  cancels the previous commit's run). Give up if the workflow fails again or
  a merge from main comes first. PR #26315: Rust was cancelled on
  `d176c35fe` and passed on `62154dc1b`, so the fix is `62154dc1b`.
- `fix_files` = files changed between `head_sha` and `fixing_commit`
  (`gh api repos/apache/datafusion/compare/<head_sha>...<fixing_commit>`).
  Compare from `head_sha`, not `breaking_commit`. When the walk passed over
  commits, the diff covers all of them.
- Skip: fixes that are merges from main, force-pushed commits, PRs with no
  later green commit.
- **One case per PR, and one case per root cause.** PR #26384 has the same
  cause as df-003 (pinned arrow-rs `interleave` change) — record it in
  `notes.md`, do not add it.

## Categories (fixed list)
`lint`, `compile_error`, `test_expectation`, `test_logic`, `code_bug`,
`upstream_dependency`, `flaky_infra`, `other`.
`test_failure` is a proposal-only bucket for a bare Rust panic; it can never be
a final label.

## Current state (Oct 18, 2026)
- 4 cases in `cases.jsonl`. Only df-004 is `ready`.
- df-001: no fixing commit (last commit on PR #26090 was still failing). Check
  whether the PR merged; if not, it will be dropped.
- df-002, df-003: need `fix_files` and `meta.pr` (`fill_cases.py` does this).
- df-001's `input` had a pasted command overwrite the `pr_title` key;
  `fill_cases.py` repairs it.
- Target: ~30 ready cases with a category mix — do not let `test_expectation`
  dominate.
- `cases_proposed.jsonl`: 7 proposals, none reviewed. Rebuilt on Oct 18 with
  the walk rules and fixed classifier (previous version in
  `cases_proposed.jsonl.bak`). Changed: #26266 breaking -> `72372a473`,
  proposal `upstream_dependency`; #26212 breaking -> `e7a800311`, proposal
  `other` (tests passed; failed "Verify Working Directory Clean" on
  `.profraw` files); #26315 fixing -> `62154dc1b` with 3 fix files (two from
  a test-coverage commit; the Rust run on the first fix commit was
  cancelled); #26218 proposal `test_failure/rust_panic`.
- The old snapshot rule was a false positive on three proposals (`insta`
  matched `install-action`, `Downloaded insta v1.x`, and a legacy-format
  warning on passing tests). Treat any category proposed before Oct 18 as
  unchecked.
- #26266 (Security audit) is probably not a valid case: the advisories came
  from outside the PR (quinn-proto, then rustls after a merge from main) and
  the "fix" changed no Cargo files. Akash to decide when reviewing.
- Batches (Oct 18): unfiltered paged back to `--before 2026-10-09T00:16:23Z`
  (newest windows re-run with the current rules: nothing new before Oct 11);
  Rust-only to `--before 2026-10-11T10:08:30Z` (old rules).
- 14 proposals in `cases_proposed.jsonl` after the Oct 18 batches. The
  classifier has no rule yet for `Docker pull failed` (flaky_infra) or
  `cannot update the lock file ... --locked`; those come out `other/unmatched`.

## GitHub CLI
- `gh` is authenticated on this machine. Repo: `apache/datafusion`.
- Rate limits: use `--limit 50` per batch, not hundreds. Run one batch at a
  time; two scripts at once have hit GitHub's limits.
- GraphQL has its own 5,000 points/hour. Check it with
  `gh api graphql -f query='{rateLimit{remaining resetAt}}'`; the REST
  `gh api rate_limit` figure for GraphQL was wrong when checked on Oct 18.
  Avoid `gh pr list --json commits` and `gh pr view --json commits`: both
  fetch every commit's authors (~30 points per call). Use
  `build_cases.find_pr` (a direct GraphQL query, 1 point) instead; the branch
  comes from `gh run view <id> --json headBranch` (REST).
- Any failing `gh` call stops `build_cases.py` with gh's error rather than
  silently skipping a candidate. The exception is a run with no log (it
  failed before any job started), which is skipped with a message.

## Running batches
- `--limit` always takes the newest runs. Page back with `--before <time>`;
  each batch prints the value to use next.
- `--workflow Rust` gives ~50 distinct commits per 50 runs (vs ~27 for all
  workflows, since each push runs ~8 workflows). `Dev` likely has the
  prettier/license-header lint failures. Each workflow has its own run
  history, so track `--before` separately per workflow.
- Most failing commits are unusable: force-pushed out of the PR, or no later
  commit yet. Expect few proposals per batch.
