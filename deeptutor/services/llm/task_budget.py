"""Per-turn admission budgets shared by concurrent model calls.

Uses the existing transport measurements. Prompt estimates are conservative
admission hints, not billing values; actual provider usage reconciles them.
"""

from contextvars import ContextVar
from dataclasses import dataclass
import time


class TaskBudgetExceeded(RuntimeError):
    error_code = "task_budget_exhausted"
    retryable = False


@dataclass(frozen=True)
class BudgetLimits:
    calls: int = 24
    tokens: int = 240_000
    seconds: int = 600
    concurrency: int = 4


LIMITS = {
    "chat": BudgetLimits(18, 200_000, 300, 4),
    "deep_solve": BudgetLimits(40, 400_000, 600, 4),
    "deep_question": BudgetLimits(40, 400_000, 900, 4),
    "deep_research": BudgetLimits(80, 900_000, 1800, 4),
}


class TaskBudget:
    def __init__(self, category="chat", limits=None):
        self.category = category
        self.limits = limits or LIMITS.get(category, BudgetLimits())
        self.started = time.monotonic()
        self.calls = self.used = self.reserved = self.active = 0

    @property
    def remaining_seconds(self):
        remaining = self.limits.seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise TaskBudgetExceeded(
                "Task time budget reached. Progress is saved; narrow the next request."
            )
        return remaining

    def admit(self, prompt_tokens, requested_output):
        self.remaining_seconds
        if self.calls >= self.limits.calls:
            raise TaskBudgetExceeded(
                "Task model-call budget reached. Progress is saved; continue with a narrower request."
            )
        if self.active >= self.limits.concurrency:
            raise TaskBudgetExceeded(
                "Task concurrency budget reached. Retry this step after the active calls finish."
            )
        available = self.limits.tokens - self.used - self.reserved - prompt_tokens
        output = min(max(1, requested_output or 4096), available)
        if output < 128:
            raise TaskBudgetExceeded(
                "Task token budget reached. Reduce the source scope or continue in a new turn."
            )
        reservation = prompt_tokens + output
        self.calls += 1
        self.active += 1
        self.reserved += reservation
        return reservation, output

    def finish(self, reservation, actual_tokens):
        self.active -= 1
        self.reserved -= reservation
        self.used += actual_tokens


current_budget: ContextVar[TaskBudget | None] = ContextVar("llm_task_budget", default=None)
