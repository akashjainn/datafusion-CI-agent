"""Tools the CI-triage agent can call.

Each tool is a plain function `fn(ctx, **args) -> str`. The harness builds the
CaseContext; the model only supplies the arguments in each tool's schema, so it
can never choose which log to read or which commit to look at.

Line numbers are 1-based everywhere, matching compilers, panic messages and
`git grep -n`.
"""
from dataclasses import dataclass
import os
import re
import subprocess

ANSI = re.compile(r"\x1b\[[0-9;]*m")
GH_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ")

MAX_PAGE_LINES = 200
MAX_MATCHES = 20
MAX_CONTEXT = 20
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


# ---------- registry ----------

TOOLS = {
    "get_log": (get_log, GET_LOG_SCHEMA),
    "read_file": (read_file, READ_FILE_SCHEMA),
}


def run_tool(ctx, name, args):
    """Run a tool by name. Unknown tools and bad arguments come back as text."""
    if name not in TOOLS:
        return f"unknown tool {name!r}; available: {', '.join(TOOLS)}"
    fn, _ = TOOLS[name]
    try:
        return fn(ctx, **args)
    except TypeError as e:
        return f"bad arguments for {name}: {e}"


if __name__ == "__main__":
    REPO_DIR = "/Users/akashjain/Documents/ai/oss/datafusion"
    ctx = CaseContext(log_path="logs/38202271883.log",
                      head_sha="80fcd0f6bec36e1ea30dff2008abc2097f56a462",
                      repo_dir=REPO_DIR)

    def show(label, out):
        print(f"\n### {label}\n{out[:1200]}")

    show("log page 1-5", get_log(ctx, start=1, limit=5))
    show("log past end", get_log(ctx, start=10**9))
    show("log start 0", get_log(ctx, start=0))
    show("log limit 0", get_log(ctx, limit=0))
    print("\n### log limit cap: lines returned =", get_log(ctx, limit=100000).count("\n"))
    show("log search panicked", get_log(ctx, pattern="panicked", context=2))
    show("log search bad regex", get_log(ctx, pattern="[bad"))
    show("log search no match", get_log(ctx, pattern="zzz_not_there"))
    show("file around :73", read_file(ctx, "datafusion/core/src/optimizer_rule_reference.rs", start=63, limit=20))
    show("file missing", read_file(ctx, "does/not/exist.rs"))
    show("file ..", read_file(ctx, "../../etc/passwd"))
    show("file absolute", read_file(ctx, "/etc/passwd"))
    show("file leading dash", read_file(ctx, "--output=x"))
    show("bad repo_dir", read_file(CaseContext(ctx.log_path, ctx.head_sha, ""), "Cargo.toml"))
    show("run_tool unknown", run_tool(ctx, "rm_rf", {}))
    show("run_tool bad arg", run_tool(ctx, "read_file", {"pth": "x"}))
