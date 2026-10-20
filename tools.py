"""Tools the CI-triage agent can call.

Each tool is a plain function `fn(ctx, **args) -> str`. The harness builds the
CaseContext; the model only supplies the arguments in each tool's schema, so it
can never choose which log to read or which commit to look at.

Line numbers are 1-based everywhere, matching compilers, panic messages and
`git grep -n`.
"""
from dataclasses import dataclass
from functools import lru_cache
import os
import re
import subprocess

ANSI = re.compile(r"\x1b\[[0-9;]*m")
GH_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ")

MAX_PAGE_LINES = 200
MAX_MATCHES = 20
MAX_CONTEXT = 20
MAX_GREP_MATCHES = 50
GIT_TIMEOUT = 60
# Kept at 500 for the first baseline on purpose: see whether truncation hides
# evidence (e.g. df-38202271883's `right:` list) before raising it.
MAX_LINE_CHARS = 500


@dataclass(frozen=True)
class CaseContext:
    log_path: str
    head_sha: str
    repo_dir: str


# ---------- shared helpers ----------

def clip(s, n=MAX_LINE_CHARS):
    return s if len(s) <= n else s[:n] + f" ...[{len(s) - n} more chars]"


def numbered(lines, lo, hi):
    """lines[lo:hi] (0-based indexes) labeled with 1-based line numbers."""
    return "\n".join(f"{n + 1}: {clip(lines[n])}" for n in range(lo, hi))


def page(lines, start, limit, what):
    """One page of `lines`. `start` is a 1-based line number."""
    total = len(lines)
    if total == 0:
        return f"the {what} is empty"
    if start < 1 or start > total:
        return f"start must be between 1 and {total} (the {what} has {total} lines)"
    if limit < 1:
        return "limit must be at least 1"
    limit = min(limit, MAX_PAGE_LINES)
    lo = start - 1
    hi = min(total, lo + limit)
    more = "" if hi == total else f" (more below: next start={hi + 1})"
    return f"lines {start}-{hi} of {total}{more}\n" + numbered(lines, lo, hi)


# ---------- get_log ----------

def _read_log(ctx):
    with open(ctx.log_path, encoding="utf-8-sig", errors="replace") as f:
        lines = ANSI.sub("", f.read()).splitlines()
    if lines:
        lines[0] = GH_TIMESTAMP.sub("", lines[0])
    return lines


def get_log(ctx, pattern=None, context=3, start=1, limit=MAX_PAGE_LINES):
    lines = _read_log(ctx)

    if pattern:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"invalid regex {pattern!r}: {e}"
        context = max(0, min(context, MAX_CONTEXT))
        matches = [i for i, line in enumerate(lines) if rx.search(line)]
        shown = matches[:MAX_MATCHES]
        blocks = []
        for i in shown:
            lo = max(0, i - context)
            hi = min(len(lines), i + context + 1)
            blocks.append(f"--- match at line {i + 1}\n" + numbered(lines, lo, hi))
        header = (f"{len(matches)} matches for {pattern!r} in {len(lines)} lines"
                  f" (showing {len(shown)})")
        return header + ("\n" + "\n".join(blocks) if blocks else "")

    return page(lines, start, limit, "log")


GET_LOG_SCHEMA = {
    "name": "get_log",
    "description": (
        "Read the log of the failed CI run. Two modes. Search: set `pattern` "
        "(a Python regex, so escape ( [ . etc.) to get every matching line with "
        "`context` lines around it; at most 20 matches are shown, and the header "
        "gives the total. Page: leave `pattern` unset and use `start`/`limit` to "
        "read consecutive lines (max 200 per call). Line numbers are 1-based. "
        "Lines over 500 characters are cut and end with '...[N more chars]'. "
        r"Start by searching for failure markers such as "
        r"`error|panicked|FAILED|##\[error\]`, then page around the line numbers "
        "the search returned. The last lines of a log are usually a summary, "
        "not the cause."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string",
                        "description": "Regex to search for. When set, start/limit are ignored."},
            "context": {"type": "integer",
                        "description": "Lines shown before and after each match (0-20, default 3)."},
            "start": {"type": "integer",
                      "description": "First line to show when paging (1-based, default 1)."},
            "limit": {"type": "integer",
                      "description": "Number of lines to show when paging (1-200, default 200)."},
        },
    },
}


# ---------- read_file ----------

def _check_path(path):
    """Return an error string for an unsafe or malformed path, else None."""
    if not path or not path.strip():
        return "path is empty"
    if path.startswith("-"):
        return f"invalid path {path!r}: must not start with '-'"
    if path.startswith("/") or os.path.isabs(path):
        return f"invalid path {path!r}: use a path relative to the repo root, e.g. datafusion/core/src/lib.rs"
    if ".." in path.replace("\\", "/").split("/"):
        return f"invalid path {path!r}: '..' is not allowed"
    return None


def _check_repo(ctx):
    if not ctx.repo_dir or not os.path.isdir(os.path.join(ctx.repo_dir, ".git")):
        return f"repo_dir {ctx.repo_dir!r} is not a git repository (harness misconfigured)"
    return None


def read_file(ctx, path, start=1, limit=MAX_PAGE_LINES):
    err = _check_path(path) or _check_repo(ctx)
    if err:
        return err
    result = subprocess.run(
        ["git", "-C", ctx.repo_dir, "show", f"{ctx.head_sha}:{path}"],
        capture_output=True, text=True, errors="replace",
    )
    if result.returncode != 0:
        return f"could not read {path!r} at {ctx.head_sha[:9]}: {result.stderr.strip()}"
    return page(result.stdout.splitlines(), start, limit, "file")


READ_FILE_SCHEMA = {
    "name": "read_file",
    "description": (
        "Read a file from the repository exactly as it was at the failing commit "
        "(not the current version). `path` is relative to the repo root, e.g. "
        "datafusion/core/src/optimizer_rule_reference.rs. Returns up to 200 "
        "numbered lines per call; line numbers are 1-based and match compiler "
        "and panic messages (file.rs:73 is line 73), so to look around an error "
        "at line N use start=N-10. The header says where the next page starts. "
        "Lines over 500 characters are cut. If the path is wrong the error says "
        "so; find the right path in the log or the PR diff."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "File path relative to the repo root."},
            "start": {"type": "integer",
                      "description": "First line to show (1-based, default 1)."},
            "limit": {"type": "integer",
                      "description": "Number of lines to show (1-200, default 200)."},
        },
        "required": ["path"],
    },
}


# ---------- git helper ----------

def _git(ctx, *args):
    """Run git in the case's repo. Returns (ok, stdout, stderr)."""
    try:
        r = subprocess.run(["git", "-C", ctx.repo_dir, *args],
                           capture_output=True, text=True, errors="replace",
                           timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "", f"git {args[0]} timed out after {GIT_TIMEOUT}s"
    return r.returncode == 0, r.stdout, r.stderr.strip()


# ---------- search_code ----------

def _check_glob(glob):
    if glob is None:
        return None
    if glob.startswith("-") or glob.startswith("/") or ".." in glob.split("/"):
        return f"invalid path_glob {glob!r}: use a repo-relative glob like 'datafusion/core/**/*.rs'"
    return None


def search_code(ctx, pattern, path_glob=None):
    if not pattern:
        return "pattern is empty"
    err = _check_glob(path_glob) or _check_repo(ctx)
    if err:
        return err
    args = ["grep", "-n", "-I", "--no-color", "-E", "-e", pattern, ctx.head_sha]
    if path_glob:
        args += ["--", path_glob]
    ok, out, errtxt = _git(ctx, *args)
    if not ok and not out:
        if not errtxt:                       # git grep exits 1 when nothing matched
            where = f" in {path_glob!r}" if path_glob else ""
            return f"0 matches for {pattern!r}{where}"
        return f"search failed: {errtxt}"
    prefix = ctx.head_sha + ":"
    hits = [l[len(prefix):] if l.startswith(prefix) else l for l in out.splitlines()]
    shown = hits[:MAX_GREP_MATCHES]
    files = len({h.split(":", 1)[0] for h in hits})
    header = (f"{len(hits)} matches in {files} files for {pattern!r}"
              + (f" in {path_glob!r}" if path_glob else "")
              + f" (showing {len(shown)}; format path:line:text)")
    if len(hits) > len(shown):
        header += " - narrow the pattern or add path_glob to see the rest"
    return header + "\n" + "\n".join(clip(h) for h in shown)


SEARCH_CODE_SCHEMA = {
    "name": "search_code",
    "description": (
        "Search the repository's files as they were at the failing commit, like "
        "`git grep`. `pattern` is a POSIX extended regex (escape ( [ . etc.); use "
        "it to find where a function, test, config option or error message is "
        "defined, e.g. 'fn logical_rules_match_documented_order' or "
        "'enable_semi_join_pushdown'. `path_glob` limits the search to matching "
        "files, e.g. 'datafusion/core/**/*.rs' or '*.slt'. Returns up to 50 "
        "'path:line:text' results with 1-based line numbers; follow up with "
        "read_file(path, start=line-10) to see the surrounding code. Binary files "
        "are skipped."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Extended regex to search for."},
            "path_glob": {"type": "string",
                          "description": "Optional repo-relative glob limiting which files are searched."},
        },
        "required": ["pattern"],
    },
}


# ---------- get_pr_diff ----------

@lru_cache(maxsize=None)
def _pr_base(repo_dir, head_sha):
    """The commit the PR's changes are measured against.

    `merge-base origin/main <head>` is wrong for PRs that were merged later:
    head is then an ancestor of today's main and the diff comes out empty.
    So use main as it was when the failing commit was made, then merge-base.
    """
    ctx = CaseContext("", head_sha, repo_dir)
    ok, ts, err = _git(ctx, "show", "-s", "--format=%cI", head_sha)
    if not ok:
        return None, err
    ok, main_then, err = _git(ctx, "rev-list", "-1", "--first-parent",
                              f"--before={ts.strip()}", "origin/main")
    if not ok or not main_then.strip():
        return None, err or "no origin/main commit before the failing commit"
    ok, base, err = _git(ctx, "merge-base", main_then.strip(), head_sha)
    if not ok:
        return None, err
    return base.strip(), None


def get_pr_diff(ctx, path=None, start=1, limit=MAX_PAGE_LINES):
    err = (_check_path(path) if path is not None else None) or _check_repo(ctx)
    if err:
        return err
    base, err = _pr_base(ctx.repo_dir, ctx.head_sha)
    if not base:
        return f"could not find the PR's base commit: {err}"
    rng = f"{base}..{ctx.head_sha}"
    if path is None:
        ok, out, errtxt = _git(ctx, "diff", "--stat=200", "--no-color", rng)
        if not ok:
            return f"diff failed: {errtxt}"
        lines = out.splitlines()
        return ("Files changed by the PR up to the failing commit "
                "(call again with `path` for one file's diff):\n"
                + "\n".join(clip(l) for l in lines[:MAX_PAGE_LINES]))
    ok, out, errtxt = _git(ctx, "diff", "--no-color", rng, "--", path)
    if not ok:
        return f"diff failed: {errtxt}"
    if not out.strip():
        return f"the PR did not change {path!r} (up to the failing commit)"
    return page(out.splitlines(), start, limit, "diff")


GET_PR_DIFF_SCHEMA = {
    "name": "get_pr_diff",
    "description": (
        "Show what the pull request changed, up to and including the failing "
        "commit (later commits, including any fix, are not visible). Without "
        "`path` it returns the list of changed files with +/- line counts; call "
        "it that way first. With `path` it returns that file's unified diff, "
        "paged 200 lines at a time with 1-based line numbers of the diff output "
        "(not of the file); use read_file to see the whole file. Use this to "
        "connect a failure to the change that caused it, or to see that the PR "
        "did not touch the failing area."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "Optional repo-relative file path; omit to list changed files."},
            "start": {"type": "integer", "description": "First diff line to show (1-based, default 1)."},
            "limit": {"type": "integer", "description": "Diff lines to show (1-200, default 200)."},
        },
    },
}


# ---------- submit_diagnosis (ends the run; not executed) ----------

CATEGORIES = ["lint", "compile_error", "test_expectation", "test_logic",
              "code_bug", "upstream_dependency", "flaky_infra", "other"]

SUBMIT_TOOL = "submit_diagnosis"

SUBMIT_DIAGNOSIS_SCHEMA = {
    "name": SUBMIT_TOOL,
    "description": (
        "Submit the final diagnosis. Call this exactly once, when you are done "
        "investigating; it ends the run. Categories: lint = style/policy checks "
        "that do not run code (fmt, clippy, typos, license, dependency-cycle and "
        "lock-file checks); compile_error = rustc failed to build; "
        "test_expectation = the code change is intended and a test's expected "
        "output is now stale; test_logic = the test itself is wrong or "
        "nondeterministic; code_bug = the PR's code is wrong and a check caught "
        "it; upstream_dependency = a change outside this repo (a dependency or "
        "advisory) broke the build; flaky_infra = CI infrastructure failed and no "
        "code is at fault (rerun); other = none of these. fix_files lists the "
        "repo-relative files that must change to fix the failure (empty if none, "
        "e.g. for flaky_infra)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": CATEGORIES},
            "fix_files": {"type": "array", "items": {"type": "string"},
                          "description": "Repo-relative paths that need to change; [] if none."},
            "caused_by_pr": {"type": "boolean",
                             "description": "True if the PR's own changes caused the failure."},
            "explanation": {"type": "string",
                            "description": "One paragraph: what failed, why, and what fixes it, citing the evidence (log lines, code)."},
        },
        "required": ["category", "fix_files", "caused_by_pr", "explanation"],
    },
}


# ---------- registry ----------

TOOLS = {
    "get_log": (get_log, GET_LOG_SCHEMA),
    "read_file": (read_file, READ_FILE_SCHEMA),
    "search_code": (search_code, SEARCH_CODE_SCHEMA),
    "get_pr_diff": (get_pr_diff, GET_PR_DIFF_SCHEMA),
}


def tool_schemas():
    """All schemas to pass as `tools=` to the API, including the submit tool."""
    return [schema for _, schema in TOOLS.values()] + [SUBMIT_DIAGNOSIS_SCHEMA]


def run_tool(ctx, name, args):
    """Run a tool by name. Unknown tools and bad arguments come back as text."""
    if name == SUBMIT_TOOL:
        return "submit_diagnosis ends the run and is handled by the agent loop"
    if name not in TOOLS:
        return f"unknown tool {name!r}; available: {', '.join(TOOLS)}, {SUBMIT_TOOL}"
    fn, _ = TOOLS[name]
    try:
        return fn(ctx, **args)
    except TypeError as e:
        return f"bad arguments for {name}: {e}"


def make_context(case, repo_dir, log_dir="logs"):
    """Build a CaseContext from a case's `input` only (never its label)."""
    inp = case["input"]
    return CaseContext(log_path=os.path.join(log_dir, f"{inp['run_id']}.log"),
                       head_sha=inp["head_sha"], repo_dir=repo_dir)


if __name__ == "__main__":
    REPO_DIR = os.environ.get("DATAFUSION_REPO",
                              "/Users/akashjain/Documents/ai/oss/datafusion")
    ctx = CaseContext(log_path="logs/38202271883.log",
                      head_sha="80fcd0f6bec36e1ea30dff2008abc2097f56a462",
                      repo_dir=REPO_DIR)

    def show(label, out):
        print(f"\n### {label}\n{out[:1200]}")

    show("log page 1-5", get_log(ctx, start=1, limit=5))
    show("log past end", get_log(ctx, start=10**9))
    show("log limit 0", get_log(ctx, limit=0))
    print("\n### log limit cap: lines returned =", get_log(ctx, limit=100000).count("\n"))
    show("log search panicked", get_log(ctx, pattern="panicked", context=2))
    show("log search bad regex", get_log(ctx, pattern="[bad"))
    show("file around :73", read_file(ctx, "datafusion/core/src/optimizer_rule_reference.rs", start=63, limit=20))
    show("file missing", read_file(ctx, "does/not/exist.rs"))
    show("file ..", read_file(ctx, "../../etc/passwd"))
    show("search fn", search_code(ctx, r"fn logical_rules_match_documented_order"))
    show("search glob", search_code(ctx, "push_down_semi_join", path_glob="*.md"))
    show("search none", search_code(ctx, "zzz_not_there_anywhere"))
    show("search bad glob", search_code(ctx, "x", path_glob="../x"))
    show("diff files", get_pr_diff(ctx))
    show("diff one file", get_pr_diff(ctx, path="datafusion/optimizer/src/optimizer.rs", limit=40))
    show("diff untouched", get_pr_diff(ctx, path="Cargo.toml"))
    show("run_tool unknown", run_tool(ctx, "rm_rf", {}))
    show("run_tool bad arg", run_tool(ctx, "read_file", {"pth": "x"}))
    print("\n### schemas:", [s["name"] for s in tool_schemas()])
