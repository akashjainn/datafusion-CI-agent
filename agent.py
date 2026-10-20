import json, os, sys, time
from anthropic import Anthropic
from build_cases import load_jsonl, CASES
from tools import make_context, run_tool, tool_schemas, get_log, read_file, SUBMIT_TOOL

REPO_DIR = "/Users/akashjain/Documents/ai/oss/datafusion"
MODEL = os.environ.get("MODEL", "claude-sonnet-5-5")   # iterate on Sonnet; compare models later
MAX_TURNS = 25

SYSTEM = """You are diagnosing a failed CI run on a pull request to apache/datafusion.
Use the tools to find the actual failure and its cause, then call submit_diagnosis exactly once."""

client = Anthropic(max_retries=3, timeout=120.0)

def first_message(case):
    inp = case["input"]
    return (f"CI run {inp['run_id']} ({inp['workflow']} workflow) failed on PR "
            f"\"{inp['pr_title']}\" at commit {inp['head_sha'][:9]}. Diagnose the failure.")

def run_agent(case):
    ctx = make_context(case, REPO_DIR)
    messages = [{"role": "user", "content": first_message(case)}]
    transcript, usage = [], {"input_tokens": 0, "output_tokens": 0}

    for turn in range(MAX_TURNS + 1):
        last = turn == MAX_TURNS
        resp = client.messages.create(
            model=MODEL, max_tokens=4096, system=SYSTEM,
            tools=tool_schemas(), messages=messages,
            # on the final turn, force an answer instead of more investigation
            tool_choice={"type": "tool", "name": SUBMIT_TOOL} if last else {"type": "auto"},
        )
        usage["input_tokens"] += resp.usage.input_tokens
        usage["output_tokens"] += resp.usage.output_tokens
        messages.append({"role": "assistant", "content": resp.content})
        transcript.append({"turn": turn, "role": "assistant",
                           "stop_reason": resp.stop_reason,
                           "content": [b.model_dump() for b in resp.content]})

        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            if block.name == SUBMIT_TOOL:
                return block.input, transcript, usage           # done
            output = run_tool(ctx, block.name, block.input)
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

        if results:
            messages.append({"role": "user", "content": results})
            transcript.append({"turn": turn, "role": "tool", "content": results})
        else:
            if resp.stop_reason == "max_tokens":
                nudge = "Your last response was cut off by the length limit. Continue, and keep each response shorter."
            else:
                nudge = ("You have not called submit_diagnosis. Keep investigating with the tools, "
                         "or call submit_diagnosis now if you have enough evidence.")
            messages.append({"role": "user", "content": nudge})
            transcript.append({"turn": turn, "role": "nudge", "content": nudge})

            
    return None, transcript, usage                               # should not get here