# agent.py -- the loop: plan, act, observe, with every block wired in at a fixed point.
#   input guard (6) -> [budget check (2) -> model call with SOUL.md (1) -> tools (3, 4) -> tool-result guard (6)]
#   x at most MAX_TURNS -> output guard (6) -> answer. Risky tools only create pending requests (7).
# The structure follows my Lab 12 subagents.py:run_sub_agent: the last turn gets no tools, and trouble becomes a
# stop reason, never an exception the caller has to catch.
import json, time
from dataclasses import dataclass, field
import openai
import config
import tools
from budget import Budget, BudgetExceeded, estimate_tokens
from context import load_soul, build_messages, clip
from guardrails import check_input, check_tool_result, check_output, refusal

# A 7B model sometimes answers from memory. Once per request it is told to use a tool first (no way out offered:
# in the demo a "...or refuse" clause made the model always refuse).
NUDGE = ("Use a tool before you answer: search_policies for a rule, get_assignment for an assignment, get_my_record "
         "for your record, compute_late_penalty or check_extension for numbers. Then answer with the source in brackets.")


@dataclass
class RunResult:
    answer: str = ""
    stop: str = ""                # answer | budget | max_turns | refused | error
    steps: list = field(default_factory=list)     # [{"tool", "args", "result"}]: the evidence
    events: list = field(default_factory=list)    # guardrail and loop events
    pending: list = field(default_factory=list)   # X-ids created by risky tools
    sources: list = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0
    seconds: float = 0.0
    sent: list = field(default_factory=list)      # every message text sent to the model (to prove what it never saw)


def make_client():
    return openai.OpenAI(base_url=config.OLLAMA_URL, api_key="ollama", max_retries=0, timeout=120)


def run_agent(request, student_id=None, budget=None, down=(), client=None, now=None):
    student_id = student_id or config.DEFAULT_STUDENT
    budget = budget or Budget()
    client = client or make_client()
    now = now or config.now()
    res, t0 = RunResult(), time.monotonic()

    def done(stop, answer):
        res.stop, res.answer = stop, answer
        res.tokens_in, res.tokens_out, res.calls = budget.tokens_in, budget.tokens_out, budget.calls
        res.seconds = round(time.monotonic() - t0, 2)
        return res

    g = check_input(request, student_id)                        # Block 6, before any model call
    res.events += g.events
    if not g.allowed:
        return done("refused", refusal(g.events[0].split(":", 1)[1]))

    ctx = tools.Ctx(student=student_id, now=now, client=client, budget=budget, down=tuple(down))
    messages = build_messages(g.text, student_id, now)          # Block 1: SOUL.md is the system message
    evidence = [g.text]                                         # what the answer's numbers may come from
    nudged, stop, answer = False, "max_turns", ""
    for turn in range(1, config.MAX_TURNS + 1):
        last = turn == config.MAX_TURNS                         # the last turn gets no tools: it must answer
        specs = None if last else tools.SPECS
        est = estimate_tokens(messages, config.MAX_TOKENS, specs)
        try:
            budget.reserve(est)                                 # Block 2: never send a call that could pass the budget
        except BudgetExceeded as e:
            res.pending, res.sources = ctx.pending, ctx.sources
            return done("budget", f"Stopped: the token budget for this request is used up ({e}). Nothing else "
                                  f"was done. Please ask a shorter question or ask a TA.")
        res.sent.append(json.dumps(messages))
        try:
            resp = client.chat.completions.create(
                model=config.CHAT_MODEL, messages=messages, temperature=config.TEMPERATURE, seed=config.SEED,
                max_tokens=config.MAX_TOKENS, **({"tools": specs} if specs else {}))
        except Exception as e:                                  # Ollama down, timeout, bad response
            budget.release(est)
            res.pending, res.sources = ctx.pending, ctx.sources
            return done("error", f"Stopped: the model is not reachable right now ({type(e).__name__}). "
                                 f"Nothing was done. Please try again later or ask a TA.")
        budget.charge(est, resp.usage)
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        if not calls:
            text = (msg.content or "").strip()
            if not res.steps and not nudged and not last:       # answered without any tool: nudge once
                nudged = True
                res.events.append("tool_first_nudge")
                messages += [{"role": "assistant", "content": text}, {"role": "user", "content": NUDGE}]
                continue
            stop, answer = ("max_turns" if last else "answer"), text
            break
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in calls]})
        for c in calls:                                         # Block 3: act, then observe
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = None
            out = (tools.dispatch(c.function.name, args, ctx) if isinstance(args, dict)
                   else f"ERROR: the arguments for {c.function.name} were not valid JSON.")
            out, ev = check_tool_result(c.function.name, out)   # Block 6: tool text is data
            res.events += ev
            ctx.flags += [e for e in ev if e.startswith("injection")]
            out = clip(out)                                     # Block 2: one tool cannot flood the context
            evidence.append(out)
            res.steps.append({"tool": c.function.name, "args": args, "result": out})
            messages.append({"role": "tool", "tool_call_id": c.id, "content": out})
    res.pending, res.sources = ctx.pending, ctx.sources
    if not answer:
        answer = "I could not finish this request. Please ask a TA."
    final, ev = check_output(answer, student_id, load_soul(), "\n".join(evidence), ctx.pending)   # Block 6
    res.events += ev
    return done(stop, final)
