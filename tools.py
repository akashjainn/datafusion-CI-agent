@dataclass
class CaseContext:
    log_path: str
    head_sha: str
    repo_dir: str

TOOLS = {
    "get_log":     (get_log, GET_LOG_SCHEMA),
    "read_file":   (read_file, READ_FILE_SCHEMA),
    # ...
}

def run_tool(ctx, name, args):
    fn, _ = TOOLS[name]
    return fn(ctx, **args)


def get_log(id: int):