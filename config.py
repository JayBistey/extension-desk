# config.py -- every setting of Extension Desk in one place.
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- the model (the graded setup: Ollama, qwen2.5:7b, temperature 0) ----
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
CHAT_MODEL = "qwen2.5:7b"
EMBED_MODEL = "nomic-embed-text"
DOC_PREFIX = "search_document: "          # nomic-embed-text wants these task prefixes
QUERY_PREFIX = "search_query: "
TEMPERATURE = 0
SEED = 42

# ---- Block 2: context control ----
TOKEN_BUDGET = 9000                       # input + output tokens for ONE request (all its model calls)
MAX_TURNS = 5                             # model calls per request; the last one gets no tools
MAX_TOKENS = 300                          # cap on every model reply
TOOL_RESULT_CHARS = 1200                  # a tool result longer than this is clipped before the model reads it
MAX_INPUT_CHARS = 1500                    # a longer request is refused by the input guard

# ---- Block 4: RAG ----
DOCS_DIR = os.path.join(HERE, "docs")
INDEX_PATH = os.path.join(HERE, "index", "policies.json")
RAG_TOP_K = 3
RAG_MIN_SIM = 0.45

# ---- data and the invented world ----
DATA_DIR = os.path.join(HERE, "data")
COURSE = "CS 1100 Introduction to Programming, Brightwater College (invented), Fall 2026"
DEFAULT_STUDENT = "S-2001"
# The invented term has its own clock, so every run (and the instructor's re-run) sees the same "today".
# Override with the environment variable DESK_NOW or `--now` on the command line.
WORLD_NOW = "2026-11-24T09:00"
STAFF_EMAILS = {"rowan.hale@example.edu", "cs1100-tas@example.edu"}    # allowed in answers (from the syllabus)

# ---- Block 7: the human gate ----
RISKY_TOOLS = {"request_extension", "email_instructor"}   # never run from the loop: they only create a pending request
EXPIRY_HOURS = 24                         # a pending request expires after 24 h or at the original deadline, whichever is first
DEFAULT_APPROVER = "ta"

# ---- runtime state (gitignored); the eval harness points STATE_DIR at a fresh folder for every case ----
STATE_DIR = os.path.join(HERE, "state")
REPORTS_DIR = os.path.join(HERE, "reports")

# ---- cost: one cloud model to compare with (the local bill is $0). Checked on the run day, see the report 3.4 ----
PRICE_MODEL = "TO CHECK"
PRICE_IN_PER_M = 0.0
PRICE_OUT_PER_M = 0.0
PRICE_URL = ""
PRICE_CHECKED = ""


def now():
    """The current time of the invented world (injectable: DESK_NOW, `--now`, or tests set WORLD_NOW)."""
    return datetime.fromisoformat(os.environ.get("DESK_NOW") or WORLD_NOW)


def state_path(*parts):
    """A path inside the current state folder (read at call time, so the harness can switch folders)."""
    return os.path.join(STATE_DIR, *parts)
