# main.py -- the command line of Extension Desk.
#   python main.py index                                   build the policy index (once)
#   python main.py ask "Can I get 2 more days for A4?"     one request (--student S-2001 --budget N --down tool --now T)
#   python main.py pending                                 what waits for a TA
#   python main.py approve X-xxxxxx --by ta                run the saved request exactly once
#   python main.py deny X-xxxxxx --by ta                   refuse it: nothing runs
#   python main.py reset                                   empty state/ (pending requests, gradebook changes, outbox, audit)
#   python main.py eval | redteam [--only e01,e02] [--label NAME]     run a suite -> reports/*.json
#   python main.py summary                                 headline numbers of the saved runs
import argparse, os, shutil, sys
import config


def cmd_index(a):
    import rag
    from agent import make_client
    n, files, skipped = rag.build_index(make_client())
    print(f"Indexed {n} chunks from {len(files)} files ({', '.join(files)}) -> {os.path.relpath(config.INDEX_PATH)}")
    print(f"Not indexed (staff only): {', '.join(skipped) or 'none'}")


def cmd_ask(a):
    import gate
    from agent import run_agent
    from budget import Budget, cloud_cost
    r = run_agent(a.request, a.student, budget=Budget(a.budget) if a.budget else None, down=a.down or ())
    for s in r.steps:
        print(f"  tool  {s['tool']}({', '.join(f'{k}={v!r}' for k, v in (s['args'] or {}).items())})")
        print(f"        -> {s['result'][:220]}{'...' if len(s['result']) > 220 else ''}")
    print(f"  events: {', '.join(r.events) or 'none'}")
    print(f"\n{r.answer}\n")
    for rid in r.pending:
        print(gate.summary(gate.load_request(rid)) + "\n")
    print(f"[stop={r.stop}  calls={r.calls}  tokens {r.tokens_in} in / {r.tokens_out} out  {r.seconds:.1f} s  "
          f"local $0; on {config.PRICE_MODEL}: ${cloud_cost(r.tokens_in, r.tokens_out):.5f}]")


def cmd_pending(a):
    import gate
    gate.expire_due()                       # the fallback runs first: expired requests are never shown as pending
    recs = gate.list_requests("pending")
    print("\n\n".join(gate.summary(r) for r in recs) if recs else "No pending requests.")


def cmd_decide(a):
    import gate
    out = gate.approve(a.id, a.by) if a.cmd == "approve" else gate.deny(a.id, a.by, a.note)
    print(out["message"])


def cmd_reset(a):
    shutil.rmtree(config.STATE_DIR, ignore_errors=True)
    print(f"Emptied {os.path.relpath(config.STATE_DIR)}/ (pending requests, extensions granted, outbox, audit log).")


def cmd_suite(a):
    from evals import harness
    harness.run_suite(a.cmd, only=a.only.split(",") if a.only else None, label=a.label)


def cmd_summary(a):
    from evals import harness
    harness.print_summary()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py", description="Extension Desk, the CS 1100 extension assistant")
    ap.add_argument("--now", help="the invented world's clock, e.g. 2026-11-25T10:00 (default config.WORLD_NOW)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index")
    p = sub.add_parser("ask")
    p.add_argument("request")
    p.add_argument("--student", default=config.DEFAULT_STUDENT)
    p.add_argument("--budget", type=int, help="a smaller token budget for this request")
    p.add_argument("--down", action="append", help="make a tool fail on purpose (repeatable)")
    sub.add_parser("pending")
    for name in ("approve", "deny"):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.add_argument("--by", default=config.DEFAULT_APPROVER)
        p.add_argument("--note", default="")
    sub.add_parser("reset")
    for name in ("eval", "redteam"):
        p = sub.add_parser(name)
        p.add_argument("--only")
        p.add_argument("--label")
    sub.add_parser("summary")
    a = ap.parse_args(argv)
    if a.now:
        os.environ["DESK_NOW"] = a.now
    {"index": cmd_index, "ask": cmd_ask, "pending": cmd_pending, "approve": cmd_decide, "deny": cmd_decide,
     "reset": cmd_reset, "eval": cmd_suite, "redteam": cmd_suite, "summary": cmd_summary}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
