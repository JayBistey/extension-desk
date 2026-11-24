# context.py -- Blocks 1 and 2: what the model sees on every call, and how long tool text may be.
import os
import config
from policy_math import show

_SOUL = None


def load_soul():
    """SOUL.md, the constitution, read once and sent as the system message of EVERY call."""
    global _SOUL
    if _SOUL is None:
        _SOUL = open(os.path.join(config.HERE, "SOUL.md"), encoding="utf-8").read().strip()
    return _SOUL


def build_messages(request, student_id, now):
    """The start of every conversation: SOUL.md plus a short session line (date and the verified student),
    then the student's (already guarded) request."""
    session = (f"\n\n## Session\nToday is {show(now)}. The verified student is {student_id}; the tools use this id "
               f"automatically. Assignment ids: A1, A2, A3, A4, A5, MID (midterm), P1 (final project).")
    return [{"role": "system", "content": load_soul() + session},
            {"role": "user", "content": request}]


def clip(text, limit=None):
    """Long tool results are cut, so one tool cannot flood the context (and the budget)."""
    limit = limit or config.TOOL_RESULT_CHARS
    return text if len(text) <= limit else text[:limit] + " ...[clipped]"
