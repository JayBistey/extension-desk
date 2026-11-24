# policy_math.py -- every rule and number of the course policy, computed in CODE (no model involved).
# Why: a 7B model compares limits and counts days wrongly, so the tools call these functions and the model
# only reports their results. The numbers here must match docs/late_policy.md and docs/extensions.md.
import math
from datetime import datetime, timedelta

GRACE_MINUTES = 15          # late_policy.md#1
PENALTY_PER_DAY = 10        # percent of the points, late_policy.md#2
MAX_LATE_DAYS = 3           # late_policy.md#3: more than 3 days late -> 0 points
MAX_EXTENSIONS = 2          # extensions.md#1: per student per term
MAX_EXTENSION_DAYS = 3      # extensions.md#1: 1 to 3 days per extension


def parse_time(text):
    """'2026-11-22T14:30', '2026-11-22 14:30' or '2026-11-22' (then 23:59). Raises ValueError otherwise."""
    text = str(text).strip().replace(" ", "T")
    if len(text) == 10:
        text += "T23:59"
    return datetime.fromisoformat(text[:16])


def show(dt):
    """How a time is shown to people, e.g. 'Wed 2026-11-25 23:59'."""
    return dt.strftime("%a %Y-%m-%d %H:%M")


def days_late(due, submitted):
    """0 if on time or inside the grace period; otherwise every started day after the due time counts."""
    if submitted <= due + timedelta(minutes=GRACE_MINUTES):
        return 0
    return math.ceil((submitted - due) / timedelta(days=1))


def late_penalty(due, submitted, points=100):
    """{'days_late', 'penalty_percent', 'points_lost', 'max_score', 'accepted'} for one submission."""
    d = days_late(due, submitted)
    if d > MAX_LATE_DAYS:
        return {"days_late": d, "penalty_percent": 100, "points_lost": points, "max_score": 0, "accepted": False}
    pct = d * PENALTY_PER_DAY
    lost = points * pct // 100
    return {"days_late": d, "penalty_percent": pct, "points_lost": lost, "max_score": points - lost, "accepted": True}


def extension_check(assignment, days, extensions_used, already_extended, now):
    """Is this extension allowed? Returns (eligible, reasons). Every failed rule is listed, not just the first."""
    reasons = []
    due = parse_time(assignment["due"])
    if not assignment.get("extensions_allowed", False):
        reasons.append("this assignment cannot be extended through the TA desk (exams and the final project)")
    if not isinstance(days, int) or days < 1 or days > MAX_EXTENSION_DAYS:
        reasons.append(f"an extension is 1 to {MAX_EXTENSION_DAYS} days; {days!r} days were asked")
    if extensions_used >= MAX_EXTENSIONS:
        reasons.append(f"extension limit reached: {extensions_used} of {MAX_EXTENSIONS} already used this term")
    if already_extended:
        reasons.append("this assignment was already extended once")
    if now >= due:
        reasons.append(f"the due time {show(due)} has passed; the late policy applies instead")
    return (not reasons), reasons
