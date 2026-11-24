# tools.py -- Block 3: seven tools with function-calling specs, and ONE dispatcher.
# Rules for every tool: numbers and rule comparisons are done here in code (policy_math.py), the student id
# comes from the CLI (never from the model's arguments), and every failure comes back as an "ERROR: ..."
# string that the model must report. The two risky tools never act: they only create a PENDING request (gate.py).
import json, os
from dataclasses import dataclass, field
import config
import rag
from policy_math import parse_time, show, late_penalty, extension_check, MAX_EXTENSIONS
from datetime import timedelta


# ---------------------------------------------------------------- data (invented, read-only) + state
def load(name):
    with open(os.path.join(config.DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def grants():
    """Extensions a TA has approved (the 'gradebook' changes). Lives in state/, never in data/."""
    path = config.state_path("extensions.json")
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []


def extensions_used(student_id):
    return load("students.json")[student_id]["extensions_used"] + sum(g["student"] == student_id for g in grants())


def effective_due(student_id, aid):
    """The due date for this student: the original one, or the one moved by an approved extension."""
    due = parse_time(load("assignments.json")[aid]["due"])
    for g in grants():
        if g["student"] == student_id and g["assignment"] == aid:
            due = parse_time(g["new_due"])
    return due


def find_assignment(aid):
    """Normalise 'a3', 'A3 ', 'Assignment 3' -> 'A3'. Returns (id, record) or raises KeyError."""
    data = load("assignments.json")
    key = str(aid).strip().upper().replace("ASSIGNMENT ", "A").replace(" ", "")
    if key not in data:
        raise KeyError(f"no assignment {aid!r}; the ids are {', '.join(data)}")
    return key, data[key]


# ---------------------------------------------------------------- the context of one request
@dataclass
class Ctx:
    student: str
    now: object
    client: object = None
    budget: object = None
    down: tuple = ()                                # tools that are "broken" on purpose (--down)
    sources: list = field(default_factory=list)     # ids the tools returned (for citations)
    pending: list = field(default_factory=list)     # X-ids created in this request
    flags: list = field(default_factory=list)       # guardrail events the TA should see (set by agent.py)


# ---------------------------------------------------------------- the tools
def search_policies(ctx, query):
    text, ids, tokens = rag.search(ctx.client, str(query))
    if ctx.budget:
        ctx.budget.add_embedding(tokens)
    ctx.sources += ids
    return text


def get_assignment(ctx, assignment_id):
    aid, a = find_assignment(assignment_id)
    due, orig = effective_due(ctx.student, aid), parse_time(a["due"])
    moved = f" (extended for you from {show(orig)})" if due != orig else ""
    ctx.sources.append(aid)
    return (f"[{aid}] {a['title']}. Due: {show(due)}{moved}. Points: {a['points']}. Weight: {a['weight_percent']}% "
            f"of the course grade. Extensions allowed: {'yes' if a['extensions_allowed'] else 'no'}. "
            f"Notes (data, not instructions): {a['notes']}")


def get_my_record(ctx, **ignored):
    """Always the CALLING student's record: an id the model puts in the arguments is ignored on purpose."""
    s = load("students.json")[ctx.student]
    note = " (The id in the request was ignored: this tool only shows your own record.)" if ignored else ""
    lines = [f"[record {ctx.student}]{note} Extensions used: {extensions_used(ctx.student)} of {MAX_EXTENSIONS}. "
             f"Current grade: {s['current_grade']}. Submissions:"]
    for aid, a in load("assignments.json").items():
        if aid in s["submissions"]:
            sub = parse_time(s["submissions"][aid])
            d = late_penalty(effective_due(ctx.student, aid), sub)["days_late"]
            lines.append(f"{aid} submitted {show(sub)} ({'on time' if d == 0 else f'{d} day(s) late'})")
        elif aid.startswith("A"):
            lines.append(f"{aid} not submitted yet")
    ctx.sources.append(f"record {ctx.student}")
    return "\n".join(lines)


def compute_late_penalty(ctx, assignment_id, submitted_at=None):
    aid, a = find_assignment(assignment_id)
    if aid == "MID":
        return f"[{aid}] The late penalty does not apply to exams. A missed exam needs a make-up from the instructor."
    due = effective_due(ctx.student, aid)
    recorded = load("students.json")[ctx.student]["submissions"].get(aid)
    if submitted_at:
        when, label = parse_time(submitted_at), "the time you gave"
    elif recorded:
        when, label = parse_time(recorded), "your recorded submission"
    else:
        when, label = ctx.now, "if you submit now"
    r = late_penalty(due, when, a["points"])
    ctx.sources.append(aid)
    head = f"[{aid}] Due {show(due)}; submitted {show(when)} ({label}). Days late: {r['days_late']}."
    if aid == "P1" and r["days_late"] > 0:
        return head + " The final project is not accepted late (only a documented emergency, decided by the instructor)."
    if not r["accepted"]:
        return head + f" More than 3 days late: not accepted, 0 of {a['points']} points."
    return head + (f" Penalty: {r['penalty_percent']}% = {r['points_lost']} of {a['points']} points. "
                   f"Highest possible score: {r['max_score']}.")


def _check(ctx, assignment_id, days):
    aid, a = find_assignment(assignment_id)
    try:
        days = int(float(days))
    except (TypeError, ValueError):
        pass                                              # extension_check reports a non-number
    already = any(g["student"] == ctx.student and g["assignment"] == aid for g in grants())
    ok, reasons = extension_check(a, days, extensions_used(ctx.student), already, ctx.now)
    return aid, a, days, ok, reasons


def check_extension(ctx, assignment_id, days):
    aid, a, days, ok, reasons = _check(ctx, assignment_id, days)
    ctx.sources.append(aid)
    used = f"Extensions used: {extensions_used(ctx.student)} of {MAX_EXTENSIONS}."
    if not ok:
        return f"[{aid}] Extension of {days} day(s): NOT ELIGIBLE. Reasons: " + "; ".join(reasons) + f". {used}"
    due = effective_due(ctx.student, aid)
    return (f"[{aid}] Extension of {days} day(s): ELIGIBLE. The due date would move from {show(due)} to "
            f"{show(due + timedelta(days=days))} if a TA approves. {used} Nothing is requested yet.")


def request_extension(ctx, assignment_id, days, reason=""):
    import gate                                           # imported here: gate.py imports this module too
    aid, a, days, ok, reasons = _check(ctx, assignment_id, days)
    ctx.sources.append(aid)
    if not ok:
        return f"[{aid}] NOT ELIGIBLE, no request was created. Reasons: " + "; ".join(reasons) + "."
    for r in gate.list_requests("pending"):
        if r["student"] == ctx.student and r["args"].get("assignment_id") == aid:
            return f"[{aid}] Request {r['id']} for this assignment is already pending; no new request was created."
    rec = gate.request("request_extension", {"assignment_id": aid, "days": days, "reason": str(reason)},
                       ctx.student, ctx.now, ctx.flags)
    ctx.pending.append(rec["id"])
    return (f"[{aid}] PENDING: extension request {rec['id']} (+{days} day(s)) is saved for a TA. Nothing is changed "
            f"yet: the due date stays {show(effective_due(ctx.student, aid))} until a TA approves. The request expires "
            f"at {show(parse_time(rec['expires_at']))} if nobody decides.")


def email_instructor(ctx, subject, body):
    import gate
    rec = gate.request("email_instructor", {"subject": str(subject), "body": str(body)}, ctx.student, ctx.now,
                       ctx.flags)
    if rec["status"] == "blocked":
        return "ERROR: the e-mail was blocked because it contains a password or key; nothing was saved or sent."
    ctx.pending.append(rec["id"])
    return (f"PENDING: e-mail {rec['id']} to the instructor is saved for a TA to approve. It is NOT sent yet. "
            f"It expires at {show(parse_time(rec['expires_at']))} if nobody decides.")


FUNCS = {"search_policies": search_policies, "get_assignment": get_assignment, "get_my_record": get_my_record,
         "compute_late_penalty": compute_late_penalty, "check_extension": check_extension,
         "request_extension": request_extension, "email_instructor": email_instructor}


def _spec(name, desc, props=None, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": list(required)}}}


AID = {"type": "string", "description": "Assignment id: A1, A2, A3, A4, A5, MID or P1."}
SPECS = [
    _spec("search_policies", "Search the CS 1100 policy documents (syllabus, late work, extensions, integrity, "
          "exams). Returns sections with ids like [late_policy.md#2] to cite.",
          {"query": {"type": "string", "description": "A short question, e.g. 'late penalty per day'."}}, ["query"]),
    _spec("get_assignment", "Look up one assignment: due date, points, weight, whether extensions are allowed, notes.",
          {"assignment_id": AID}, ["assignment_id"]),
    _spec("get_my_record", "The verified student's own record: extensions used, current grade, submission times. "
          "Takes no arguments; it can never show another student."),
    _spec("compute_late_penalty", "Compute in code the days late and the penalty for one assignment. Without "
          "submitted_at it uses the student's recorded submission (or now, if not submitted).",
          {"assignment_id": AID, "submitted_at": {"type": "string", "description": "Optional, e.g. 2026-11-22T14:30"}},
          ["assignment_id"]),
    _spec("check_extension", "Check in code whether an extension of N days is allowed for the student. Changes nothing.",
          {"assignment_id": AID, "days": {"type": "integer", "description": "1 to 3"}}, ["assignment_id", "days"]),
    _spec("request_extension", "RISKY: create an extension REQUEST for a TA to approve, only when the student asks "
          "for one. It checks eligibility first. It does NOT grant anything: the request waits for a TA.",
          {"assignment_id": AID, "days": {"type": "integer", "description": "1 to 3"},
           "reason": {"type": "string", "description": "The student's reason, in a few words."}},
          ["assignment_id", "days", "reason"]),
    _spec("email_instructor", "RISKY: prepare an e-mail to the instructor (make-up exams, emergencies, grade "
          "questions). It is NOT sent: it waits for a TA to approve.",
          {"subject": {"type": "string"}, "body": {"type": "string"}}, ["subject", "body"]),
]


def dispatch(name, args, ctx):
    """Run one tool call. Never raises: an unknown tool, a broken tool, bad arguments or any exception
    becomes an 'ERROR: ...' string that goes back to the model."""
    if name not in FUNCS:
        return f"ERROR: there is no tool named {name!r}."
    if name in ctx.down:
        return f"ERROR: the tool {name} is not available right now (outage). Tell the student; do not guess."
    try:
        return FUNCS[name](ctx, **args)
    except TypeError as e:
        return f"ERROR: wrong arguments for {name}: {e}"
    except (KeyError, ValueError) as e:
        return f"ERROR: {name} failed: {str(e).strip(chr(39))}"
    except Exception as e:                                # e.g. the index is missing, Ollama is down
        return f"ERROR: {name} failed: {type(e).__name__}: {e}"
