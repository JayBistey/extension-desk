# guardrails.py -- Block 6: small, boring checks at three fixed points of every request.
#   check_input        the student's message, BEFORE any model call (a refusal here costs 0 tokens)
#   check_tool_result  every tool result, BEFORE the model reads it (tool text is data, never instructions)
#   check_output       the model's answer, BEFORE the student sees it (the last line of defense)
# The patterns started from my Lab 8 guardrails.py (SECRET_PATTERNS, PII_PATTERNS, redact).
import re
from dataclasses import dataclass, field
import config

SECRET_PATTERNS = {
    "api_key": re.compile(r"\b(?:sk-[A-Za-z0-9\-_]{8,}|AKIA[0-9A-Z]{16})"),
    # "password: X", "password = X", "password is X" -- but not "password is not ..." or an already redacted value
    "password": re.compile(r"(?i)\bpassword\s*(?:[:=]|is)\s*(?!\[REDACTED|not\b|never\b|private\b|secret\b|confidential\b)"
                           r"[A-Za-z0-9][\w\-!@#$%]{3,}"),
}
PII_PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"(?<![\d-])(?:\(?\d{3}\)?[\s.\-]?)?\d{3}[\s.\-]\d{4}(?![\d-])"),
}
STUDENT_ID = re.compile(r"\bS-\d{4}\b")

# Direct prompt injection in the student's message.
INJECTION = re.compile(
    r"(?i)\b(ignore|disregard|forget)\b.{0,30}\b(instructions|rules|policy|constitution|prompt)\b"
    r"|system prompt|developer mode|jailbreak|\byou are now\b|soul\.md"
    r"|(reveal|print|show|repeat|paste)\b.{0,30}\b(your|the)\b.{0,15}\b(instructions|rules|constitution|prompt)\b")
# Requests outside the desk's job (writing work for the student, changing grades, unrelated topics).
OFF_TOPIC = re.compile(
    r"(?i)\b(write|do|finish|solve)\b.{0,20}\b(my|an|the)\b.{0,20}\b(essay|homework|code|assignment for me)\b"
    r"|\b(change|raise|bump|fix)\b.{0,15}\b(my|the|his|her|their)\b.{0,10}\bgrade\b"
    r"|\b(weather|stock price|football|recipe|lottery|horoscope)\b")
# Instruction-like text inside DATA (an assignment note, a document): flagged and cut out of the tool result.
INJECTION_IN_DATA = re.compile(
    r"(?i)[^.]*\b(SYSTEM\s*:|ignore (?:the|all|any|previous|your)\b[^.]{0,40}\b(?:policy|instructions|rules)"
    r"|no approval (?:is )?needed|grant every)[^.]*\.?")

# A false "it is done" claim about one of the two risky actions. Lab 12 lesson: "has been granted and is pending"
# slipped through because "pending" counted as a negation. Here only real negations and conditions exempt a sentence.
CLAIMS_DONE = re.compile(
    r"(?i)\b(extension|due date|deadline)\b[^.]{0,60}\b(has been|have been|is|was|is now|got)\s+(granted|approved|extended|moved|changed|updated)\b"
    r"|\bI\s+(have\s+)?(granted|approved|extended)\b"
    r"|\b(granted|approved) (you |your )?(an |the |a )?(\d+-day )?extension"
    r"|\b(e-?mail|message)\b[^.]{0,60}\b(has been|was|is)\s+(sent|delivered|forwarded)\b"
    r"|\bI\s+(have\s+)?(sent|e-?mailed|forwarded)\b")
# "I have created a request" while no request exists: the model skipped the tool (seen in my first smoke test).
CLAIMS_REQUEST = re.compile(r"(?i)\b(I\s+(have\s+)?(created|submitted|made|filed|saved|sent|placed)\s+(a|an|your|the)\s+"
                            r"(extension\s+)?(request|e-?mail)|(request|e-?mail)\b[^.]{0,40}\b(has been|was|is)\s+"
                            r"(created|submitted|saved|filed|prepared))")
NEGATED = re.compile(r"(?i)\b(not|never|no|cannot|can't|isn't|hasn't|wasn't|haven't|won't|nothing|until|once|if|after|when|will)\b")


PERSONAL = re.compile(r"(?i)\b(grade|grades|score|e-?mail|phone|number|record|submission|submitted|extensions?|address|contact)\b")


def others_named(text, student_id):
    """Another student's id (S-xxxx) or name from the invented roster appears in the text."""
    import json, os
    roster = json.load(open(os.path.join(config.DATA_DIR, "students.json"), encoding="utf-8"))
    ids = [s for s in STUDENT_ID.findall(text) if s != student_id]
    names = [r["name"] for sid, r in roster.items() if sid != student_id and r["name"].lower() in text.lower()]
    return ids + names


@dataclass
class GuardResult:
    allowed: bool
    text: str
    events: list = field(default_factory=list)


def redact(text, patterns):
    for name, p in patterns.items():
        text = p.sub(f"[REDACTED_{name.upper()}]", text)
    return text


def refusal(reason):
    """The fixed reply for a refused input (code-built, no model call)."""
    why = {"empty": "the message is empty", "too_long": f"the message is longer than {config.MAX_INPUT_CHARS} characters",
           "prompt_injection": "it asks me to ignore or reveal my rules",
           "secret_in_input": "it contains a password or key; please never type one here",
           "off_topic": "it is outside the extension desk's job",
           "other_student_data": "it asks about another student's information, which I never share"}[reason]
    return (f"Refused by the input guardrail ({reason}): {why}. I can help with CS 1100 course rules, deadlines, "
            f"late penalties and extension requests.")


# ---------------------------------------------------------------- 1. input
def check_input(text, student_id):
    """Refuse (0 tokens) or let through with personal data of others redacted."""
    if not text or not text.strip():
        return GuardResult(False, "", ["refused:empty"])
    if len(text) > config.MAX_INPUT_CHARS:
        return GuardResult(False, "", ["refused:too_long"])
    if INJECTION.search(text):
        return GuardResult(False, "", ["refused:prompt_injection"])
    if any(p.search(text) for p in SECRET_PATTERNS.values()):
        return GuardResult(False, "", ["refused:secret_in_input"])
    if OFF_TOPIC.search(text):
        return GuardResult(False, "", ["refused:off_topic"])
    if (others_named(text, student_id) and PERSONAL.search(text)):   # eval_v2 e16, r05: the model answered with the
        return GuardResult(False, "", ["refused:other_student_data"])  # caller's own record; a prompt rule did not fix it
    events = []
    for name, p in PII_PATTERNS.items():          # an e-mail or phone number never reaches the model
        if p.search(text):
            text = p.sub(f"[REDACTED_{name.upper()}]", text)
            events.append(f"redacted_pii:{name}")
    others = [s for s in STUDENT_ID.findall(text) if s != student_id]
    if others:                                    # another student's id: the model never learns whose data was asked
        text = STUDENT_ID.sub(lambda m: m.group(0) if m.group(0) == student_id else "[ANOTHER_STUDENT]", text)
        events.append("redacted_pii:other_student_id")
    return GuardResult(True, text, events)


# ---------------------------------------------------------------- 2. tool results
def check_tool_result(tool, text):
    """Secrets are cut out; instruction-like sentences in data are removed and replaced by a visible marker."""
    events = []
    if any(p.search(text) for p in SECRET_PATTERNS.values()):
        text = redact(text, SECRET_PATTERNS)
        events.append(f"secret_removed_from_tool_result:{tool}")
    if INJECTION_IN_DATA.search(text):
        text = INJECTION_IN_DATA.sub(" [REMOVED: instruction-like text found in the data; it is not an instruction]", text)
        events.append(f"injection_in_tool_result:{tool}")
    return text, events


# ---------------------------------------------------------------- 3. output
NUM = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
ID_LIKE = re.compile(r"\[[^\]]*\]|\b[A-Z]{1,3}-?[0-9a-f]*\d[0-9a-f]*\b|#\d+")   # [late_policy.md#2], A3, S-2001, X-1a2b3c


def numbers(text):
    """The numbers a text states, ignoring ids and citations (A3, S-2001, [extensions.md#1])."""
    return {float(n.replace(",", "")) for n in NUM.findall(ID_LIKE.sub(" ", str(text)))}


def unsupported_numbers(answer, evidence):
    """Numbers in the answer that appear in no tool result and not in the request. A tool's 23:59 also backs
    the "11:59 PM" a person would write (bug found in redteam_v1: r02 and r04 were flagged for "11")."""
    have = numbers(evidence) | {float(int(h) % 12 or 12) for h in re.findall(r"\b(\d{1,2}):\d{2}\b", evidence)}
    return sorted(n for n in numbers(answer) if not any(abs(n - e) < 0.006 for e in have))


def claims_done(text):
    """True if some sentence says an extension was granted or an e-mail was sent (without a negation)."""
    return any(CLAIMS_DONE.search(s) and not NEGATED.search(s) for s in re.split(r"(?<=[.!?])\s+|\n+", text))


def soul_leak(text, soul, n=8):
    """True if the answer repeats any 8 consecutive words of SOUL.md (the constitution is not for users)."""
    words = re.findall(r"[a-z0-9']+", soul.lower())
    grams = {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}
    said = re.findall(r"[a-z0-9']+", text.lower())
    return any(" ".join(said[i:i + n]) in grams for i in range(len(said) - n + 1))


CITATION = re.compile(r"\[([^\]]+)\]")


def check_output(text, student_id, soul, evidence, pending, sources=(), flags=()):
    """Returns (final text, events). Blocks secrets and SOUL text, redacts personal data, removes citations no
    tool returned, corrects false claims, flags numbers that no tool returned, and tells the student when a
    record contained an instruction."""
    if any(p.search(text) for p in SECRET_PATTERNS.values()):
        return "I can't share that. Passwords and keys are never given out at the extension desk.", ["secret_in_output"]
    if soul_leak(text, soul):
        return "I can't share my instructions. I can help with CS 1100 rules, deadlines and extensions.", \
               ["constitution_in_output"]
    events = []
    for name, p in PII_PATTERNS.items():
        def keep_staff(m, name=name):
            return m.group(0) if m.group(0).lower() in config.STAFF_EMAILS else f"[REDACTED_{name.upper()}]"
        new = p.sub(keep_staff, text)
        if new != text:
            text, events = new, events + [f"redacted_pii_in_output:{name}"]
    if any(s != student_id for s in STUDENT_ID.findall(text)):
        text = STUDENT_ID.sub(lambda m: m.group(0) if m.group(0) == student_id else "[ANOTHER_STUDENT]", text)
        events.append("redacted_pii_in_output:other_student_id")
    def known(cite):                              # [late_policy.md#2], [late_policy.md], [A3], [record S-2001]
        return cite.startswith(("REDACTED", "ANOTHER_STUDENT")) or any(s == cite or s.startswith(cite + "#")
                                                                       for s in sources)
    bad_cites = [c for c in CITATION.findall(text) if not known(c)]
    if bad_cites:                                 # a citation the tools never returned is invented (redteam_v1 r07, r10)
        text = CITATION.sub(lambda m: m.group(0) if known(m.group(1)) else "", text)
        events.append("unknown_citation_removed:" + ",".join(bad_cites))
    if re.search(r"\[REDACTED_(PASSWORD|API_KEY)\]", text):   # don't even confirm that a secret exists (r04)
        text = " ".join(s for s in re.split(r"(?<=[.!?])\s+", text) if "[REDACTED_" not in s)
        events.append("redacted_secret_mention_removed")
    if claims_done(text):
        fix = (f"Correction: nothing is granted or sent yet; request {', '.join(pending)} waits for a TA's approval."
               if pending else "Correction: no extension was granted and no e-mail was sent.")
        sentences = re.split(r"(?<=[.!?])\s+", text)
        text = " ".join(s for s in sentences if not (CLAIMS_DONE.search(s) and not NEGATED.search(s)))
        text = (text + " " + fix).strip()
        events.append("false_claim_corrected")
    if not pending and CLAIMS_REQUEST.search(text):
        sentences = re.split(r"(?<=[.!?])\s+", text)
        text = " ".join(s for s in sentences if not CLAIMS_REQUEST.search(s))
        text += " Correction: no request was created yet; ask me to request it and I will prepare it for a TA."
        events.append("claimed_request_without_tool")
    bad = unsupported_numbers(text, evidence)
    if bad:
        events.append("unsupported_numbers:" + ",".join(f"{n:g}" for n in bad))
        text += (" (Check: " + ", ".join(f"{n:g}" for n in bad) +
                 " did not come from any tool result; please confirm with the TA desk.)")
    if flags:                                     # SOUL rule 7, enforced in code (redteam_v1 r02: the model forgot)
        text += " Note: a course record contained an instruction-like text; I did not follow it."
        events.append("injection_notice_added")
    return text.strip(), events
