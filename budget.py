# budget.py -- Block 2: a hard token budget for ONE request. From my Lab 12 budget.py (TODO 9-10):
# reserve() BEFORE a model call, charge() the real usage after it, release() if the call failed.
# Lab 12 needed a lock because sub-agents ran on threads; Extension Desk is one agent in one loop, so no lock.
import config


class BudgetExceeded(Exception):
    """Raised by reserve(): the next call could pass the ceiling, so it is never sent."""


def estimate_tokens(messages, max_tokens, specs=None):
    """Deliberately pessimistic: 1 token per 3 characters of everything sent (tool specs included),
    plus the full reply cap. The real usage is almost always lower."""
    chars = sum(len(str(m.get("content") or "")) + len(str(m.get("tool_calls") or "")) for m in messages)
    chars += len(str(specs or ""))
    return chars // 3 + max_tokens


def cloud_cost(tokens_in, tokens_out):
    """What the same tokens would cost on the cloud model in config.py (the local bill is $0)."""
    return tokens_in * config.PRICE_IN_PER_M / 1e6 + tokens_out * config.PRICE_OUT_PER_M / 1e6


class Budget:
    def __init__(self, ceiling=None):
        self.ceiling = ceiling or config.TOKEN_BUDGET
        self.spent = 0                  # real tokens reported by Ollama (input + output)
        self.reserved = 0               # the estimate of the call in flight
        self.tokens_in = self.tokens_out = self.calls = 0

    def left(self):
        return self.ceiling - self.spent - self.reserved

    def reserve(self, estimate):
        if self.spent + self.reserved + estimate > self.ceiling:
            raise BudgetExceeded(f"next call needs up to {estimate} tokens, only {self.left()} of "
                                 f"{self.ceiling} left")
        self.reserved += estimate

    def charge(self, estimate, usage):
        """The call came back: drop its reservation, add the REAL usage. Returns (in, out)."""
        tin, tout = (usage.prompt_tokens or 0, usage.completion_tokens or 0) if usage else (0, 0)
        self.reserved -= estimate
        self.spent += tin + tout
        self.tokens_in += tin
        self.tokens_out += tout
        self.calls += 1
        return tin, tout

    def release(self, estimate):
        """The call failed: give its reservation back."""
        self.reserved -= estimate

    def add_embedding(self, tokens):
        """Policy search embeds the query: a few tokens, counted as input (not reserved)."""
        self.spent += tokens
        self.tokens_in += tokens
