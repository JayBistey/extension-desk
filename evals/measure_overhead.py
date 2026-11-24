# evals/measure_overhead.py -- how many input tokens SOUL.md and the tool specs cost on EVERY model call.
# Measured with real calls: the same tiny message with and without each part; the difference in
# usage.prompt_tokens is that part's cost. Then the estimate for one saving: SOUL.md cut in half.
#   python evals/measure_overhead.py        -> reports/prompt_overhead.json
import json, os, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config, tools
from agent import make_client
from context import load_soul


def prompt_tokens(client, system=None, specs=None):
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": "Hi"}]
    r = client.chat.completions.create(model=config.CHAT_MODEL, messages=msgs, temperature=0, max_tokens=1,
                                       **({"tools": specs} if specs else {}))
    return r.usage.prompt_tokens


def main():
    client = make_client()
    base = prompt_tokens(client)
    soul = prompt_tokens(client, system=load_soul()) - base
    specs = prompt_tokens(client, specs=tools.SPECS) - base
    run = json.load(open(os.path.join(config.REPORTS_DIR, "eval_results.json"), encoding="utf-8"))["summary"]
    calls, tin = run["calls_mean"], run["tokens_in_mean"]
    saved_per_request = soul / 2 * calls                  # half of SOUL.md is not re-sent on each call
    out = {"measured_at": datetime.now().isoformat(timespec="seconds"), "model": config.CHAT_MODEL,
           "baseline_prompt_tokens": base, "soul_md_tokens": soul, "tool_specs_tokens": specs,
           "overhead_per_call": soul + specs, "calls_per_request_mean": calls,
           "tokens_in_per_request_mean": tin, "halve_soul_saves_tokens_per_request": round(saved_per_request, 1),
           "halve_soul_saves_percent_of_input": round(100 * saved_per_request / tin, 1),
           "halve_soul_saves_usd_per_1000_requests": round(1000 * saved_per_request * config.PRICE_IN_PER_M / 1e6, 4),
           "price": f"{config.PRICE_MODEL}, ${config.PRICE_IN_PER_M}/1M input (checked {config.PRICE_CHECKED})",
           "source_run": "reports/eval_results.json"}
    path = os.path.join(config.REPORTS_DIR, "prompt_overhead.json")
    json.dump(out, open(path, "w", encoding="utf-8"), indent=2)
    for k, v in out.items():
        print(f"{k:<42} {v}")
    print(f"Saved {os.path.relpath(path, config.HERE)}")


if __name__ == "__main__":
    main()
