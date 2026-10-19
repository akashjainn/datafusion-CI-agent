from dataclasses import dataclass
import re
ANSI = re.compile(r"\x1b\[[0-9;]*m")
@dataclass
class CaseContext:
    log_path: str
    head_sha: str
    repo_dir: str

def get_log(ctx, pattern = None, context = 3, start=0, limit=200):
    if start < 0:
        return "Start must be at least 0"

    if start >= len(lines):
        return f"start {start} is past the end (log has {len(lines)} lines, 0-{len(lines) - 1})"
    
    limit = min(limit, 200)
    with open(ctx.log_path, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
        clean = ANSI.sub("", text)
        lines = clean.splitlines()
    if pattern:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"invalid regex {pattern!r}: {e}"

        matches = [i for i, line in enumerate(lines) if rx.search(line)]
        shown = matches[:20]

        blocks = []
        for i in shown:
            lo = max(0, i - context)           # context lines before i, but not below 0
            hi = min(len(lines), i + context + 1)      # context lines after i, but not past len(lines)
            block = "\n".join(f"{n}: {clip(lines[n])}" for n in range(lo, hi))        # each line in lo..hi as f"{n}: {lines[n]}", joined with "\n"
            blocks.append(f"--- match at line {i}\n{block}")

        header = f"{len(matches)} matches for {pattern!r} in {len(lines)} lines (showing {len(shown)})"
        return header + "\n" + "\n".join(blocks)


    body  = "\n".join(clip(line) for line in lines[start:start + limit])          
    return f"lines: {start}-{min(len(lines), start + limit) - 1} of {len(lines)}\n" + body

def clip(s, n=500):
    return s if len(s) <= n else s[:n] + f" ...[{len(s) - n} more chars]"

def run_tool(ctx, name, args):
    fn, _ = TOOLS[name]
    return fn(ctx, **args)

GET_LOG_SCHEMA = {
    "name": "get_log",
    "description": "Read the failed CI run's log. Use get_log(ctx, pattern, context, start, limit) to retrieve the logs results. Pattern is a keyword / phrase that will be searched for. Context determines how many lines above and below each found keyword / phrase will be returned. If no pattern is passed in, you can pass in a start and limit to simply return a section of the log.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex to search for"},
            "context": {"type": "integer", "description": "Lines of context around each match"},
            "start":   {"type": "integer"},
            "limit":   {"type": "integer"},
        },
    },
}

TOOLS = {
    "get_log":     (get_log, GET_LOG_SCHEMA),
}

if __name__ == "__main__":
    ctx = CaseContext(log_path="logs/38202271883.log", head_sha="80fcd0f6", repo_dir="")
    print(get_log(ctx, start=0, limit=5))
    print(get_log(ctx, start=10**9))
    print(get_log(ctx, start=-5))
    print(get_log(ctx, limit=100000).count("\n"))   # should be 200 (header line + 199 newlines)
    print(get_log(ctx, pattern="panicked", context=2))