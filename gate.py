# gate.py -- Block 7: the human gate. Adapted from my Lab 9 hitl.py (pending JSON, resolve once, audit log).
# A risky tool never acts from the agent loop: it only saves state/pending/X-xxxxxx.json. A TA then runs
#   python main.py approve X-xxxxxx --by ta     -> the SAVED call runs exactly once
#   python main.py deny X-xxxxxx --by ta        -> nothing runs
# New in this project: EXPIRY. A request expires after 24 h or at the original due time, whichever is first
# (my Lab 9 Part H Q5 answer). Approving an expired request runs nothing. The clock is passed in (`now`),
# so tests and an eval case can prove it.
import hashlib, json, os
from datetime import timedelta
import config
import tools
from guardrails import SECRET_PATTERNS, redact
from policy_math import parse_time, show, extension_check, MAX_EXTENSIONS


def fingerprint(action, args):
    """A hash of exactly what the TA is shown; if the saved call is changed later, the approval is void."""
    return hashlib.sha256(json.dumps({"action": action, "args": args}, sort_keys=True).encode()).hexdigest()[:16]


def _path(rid):
    return config.state_path("pending", f"{rid}.json")


def _write(rec):
    os.makedirs(config.state_path("pending"), exist_ok=True)
    with open(_path(rec["id"]), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)


def load_request(rid):
    if not os.path.exists(_path(rid)):
        raise ValueError(f"no request {rid!r}")
    return json.load(open(_path(rid), encoding="utf-8"))


def list_requests(status=None):
    folder = config.state_path("pending")
    if not os.path.isdir(folder):
        return []
    recs = [json.load(open(os.path.join(folder, n), encoding="utf-8")) for n in sorted(os.listdir(folder))]
    return sorted([r for r in recs if status is None or r["status"] == status], key=lambda r: r["created_at"])


def audit(event, **fields):
    """Append one JSON line per step to state/audit.jsonl; secrets are scrubbed first. Append only."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    line = json.loads(redact(json.dumps({"at": config.now().isoformat(), "event": event, **fields}), SECRET_PATTERNS))
    with open(config.state_path("audit.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def facts_and_flags(action, args, student, now):
    """What the TA needs to decide in five seconds, looked up in CODE; warnings come from observable signals."""
    facts, flags = {}, []
    if action == "request_extension":
        aid = args["assignment_id"]
        a = tools.load("assignments.json")[aid]
        due = tools.effective_due(student, aid)
        used = tools.extensions_used(student)
        facts = {"assignment": f"{aid} {a['title']}",
                 "due_change": f"{show(due)} -> {show(due + timedelta(days=args['days']))} (+{args['days']} day(s))",
                 "extensions": f"{used} of {MAX_EXTENSIONS} used (approving makes it {used + 1} of {MAX_EXTENSIONS})"}
        if due - now < timedelta(hours=24):
            flags.append(f"the due time is less than 24 h away ({show(due)})")
        if args["days"] == 3:
            flags.append("the maximum of 3 days is asked")
    else:
        facts = {"to": "rowan.hale@example.edu (the instructor)", "subject": args["subject"],
                 "body": f"{len(args['body'])} characters: {args['body'][:200]}"}
    if not str(args.get("reason") if action == "request_extension" else args.get("body")).strip():
        flags.append("no reason given")
    return facts, flags


def request(action, args, student, now, context_flags=()):
    """Save one pending request (never run it). Returns the record; status 'blocked' if the args carry a secret.
    context_flags: guardrail events of this request (e.g. an injection found in a tool result) the TA should see."""
    if any(p.search(json.dumps(args)) for p in SECRET_PATTERNS.values()):
        audit("blocked", action=action, student=student, why="secret_in_arguments")
        return {"id": None, "status": "blocked"}
    expires = now + timedelta(hours=config.EXPIRY_HOURS)
    if action == "request_extension":                    # ... or at the original due time, whichever is first
        expires = min(expires, tools.effective_due(student, args["assignment_id"]))
    rid = "X-" + os.urandom(3).hex()
    while os.path.exists(_path(rid)):
        rid = "X-" + os.urandom(3).hex()
    facts, flags = facts_and_flags(action, args, student, now)
    flags += [f"guardrail event during this request: {e}" for e in context_flags]
    rec = {"id": rid, "action": action, "args": args, "student": student, "facts": facts, "flags": flags,
           "fingerprint": fingerprint(action, args), "status": "pending",
           "created_at": now.isoformat(timespec="minutes"), "expires_at": expires.isoformat(timespec="minutes")}
    _write(rec)
    audit("requested", id=rid, action=action, student=student, args=args, expires_at=rec["expires_at"])
    return rec


def summary(rec):
    """The text the TA sees for one request."""
    s = tools.load("students.json").get(rec["student"], {})
    lines = [f"=== TA APPROVAL NEEDED  [{rec['id']}]  status: {rec['status']} ===",
             f"Action:     {rec['action']}",
             f"Student:    {rec['student']} {s.get('name', '?')}"]
    lines += [f"{k.replace('_', ' ').capitalize() + ':':<11} {v}" for k, v in rec["facts"].items()]
    if rec["action"] == "request_extension":
        lines.append(f"Reason:     {rec['args']['reason'] or '(none)'}")
    lines += [f"Warnings:   {'; '.join('! ' + f for f in rec['flags']) or 'none'}",
              f"Expires:    {show(parse_time(rec['expires_at']))} (24 h or the original due time, whichever is first)",
              "Reversible? NO: approving changes the gradebook or sends an e-mail.",
              f"Approve:    python main.py approve {rec['id']} --by {config.DEFAULT_APPROVER}",
              f"Deny:       python main.py deny {rec['id']} --by {config.DEFAULT_APPROVER}"]
    return "\n".join(lines)


def _finish(rec, status, message, **extra):
    rec.update(status=status, message=message, **extra)
    _write(rec)
    audit(status, id=rec["id"], action=rec["action"], by=rec.get("decided_by"), message=message)
    return {"id": rec["id"], "status": status, "message": message}


def approve(rid, by, now=None):
    """Run the saved call EXACTLY once. It is marked approved before it runs, so a second approve (or a crash
    in the middle) can never run it twice. Expired or changed requests run nothing."""
    now = now or config.now()
    rec = load_request(rid)
    if rec["status"] != "pending":
        audit("approve_refused", id=rid, by=by, status=rec["status"])
        return {"id": rid, "status": rec["status"],
                "message": f"Request {rid} is already '{rec['status']}': nothing was run again."}
    rec.update(decided_by=by, decided_at=now.isoformat(timespec="minutes"))
    if now >= parse_time(rec["expires_at"]):
        return _finish(rec, "expired", f"EXPIRED at {show(parse_time(rec['expires_at']))}: request {rid} was not "
                                        f"run and nothing changed. The student may ask again if it is still allowed.")
    if fingerprint(rec["action"], rec["args"]) != rec["fingerprint"]:
        return _finish(rec, "void", f"VOID: request {rid} was changed after it was saved; nothing was run.")
    rec["status"] = "approved"                       # mark FIRST, then execute
    _write(rec)
    audit("approved", id=rid, action=rec["action"], by=by)
    if rec["action"] == "request_extension":
        return _grant_extension(rec, now)
    return _send_email(rec, now)


def deny(rid, by, note="", now=None):
    rec = load_request(rid)
    if rec["status"] != "pending":
        return {"id": rid, "status": rec["status"], "message": f"Request {rid} is already '{rec['status']}'."}
    rec.update(decided_by=by, decided_at=(now or config.now()).isoformat(timespec="minutes"), note=note)
    return _finish(rec, "denied", f"DENIED by {by}: request {rid} was not run; nothing changed.")


def expire_due(now=None):
    """The fallback when nobody decides: every pending request past its expiry becomes 'expired' (never run)."""
    now = now or config.now()
    out = []
    for rec in list_requests("pending"):
        if now >= parse_time(rec["expires_at"]):
            rec["decided_by"] = "timeout"
            _finish(rec, "expired", f"EXPIRED at {show(parse_time(rec['expires_at']))}: not run.")
            out.append(rec["id"])
    return out


# ---------------------------------------------------------------- what an approval actually does
def _grant_extension(rec, now):
    """Re-check the rules at approval time (two pending requests must not pass the limit together), then
    write the new due date to state/extensions.json, the gradebook."""
    a = rec["args"]
    aid = a["assignment_id"]
    already = any(g["student"] == rec["student"] and g["assignment"] == aid for g in tools.grants())
    ok, reasons = extension_check(tools.load("assignments.json")[aid], a["days"],
                                  tools.extensions_used(rec["student"]), already, now)
    if not ok:
        return _finish(rec, "refused", f"Not run: the rules no longer allow it ({'; '.join(reasons)}).")
    old = tools.effective_due(rec["student"], aid)
    new = old + timedelta(days=a["days"])
    grants = tools.grants() + [{"request": rec["id"], "student": rec["student"], "assignment": aid, "days": a["days"],
                                "old_due": old.isoformat(timespec="minutes"), "new_due": new.isoformat(timespec="minutes"),
                                "approved_by": rec["decided_by"], "at": now.isoformat(timespec="minutes")}]
    with open(config.state_path("extensions.json"), "w", encoding="utf-8") as f:
        json.dump(grants, f, indent=2)
    return _finish(rec, "done", f"APPROVED by {rec['decided_by']} and run once: {aid} for {rec['student']} is now due "
                                f"{show(new)} (was {show(old)}).")


def _send_email(rec, now):
    """'Sending' writes the e-mail to state/outbox/ (no real mail server in this project)."""
    os.makedirs(config.state_path("outbox"), exist_ok=True)
    path = config.state_path("outbox", f"{rec['id']}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"To: rowan.hale@example.edu\nFrom: {rec['student']} via the CS 1100 extension desk\n"
                f"Date: {now.isoformat(timespec='minutes')}\nSubject: {rec['args']['subject']}\n\n{rec['args']['body']}\n")
    return _finish(rec, "done", f"APPROVED by {rec['decided_by']} and run once: the e-mail was sent "
                                f"(state/outbox/{rec['id']}.txt).")
