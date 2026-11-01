"""Rules: checks that must hold, written against what actually happened."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .trace import Trace


@dataclass(frozen=True)
class Rule:
    fn: Callable[[Trace], bool]
    name: str
    when: str  # "end" (once, after the run) or "step" (after every event)
    description: str

    def check(self, trace: Trace) -> bool:
        return bool(self.fn(trace))


class RuleBroken(Exception):
    def __init__(self, rule: Rule):
        super().__init__(f"rule broken: {rule.name}")
        self.rule = rule


def rule(fn: Callable[[Trace], bool] | None = None, *, when: str = "end", name: str | None = None):
    """Mark a function trace -> bool as a rule.

    when="end":  checked once at the end of the run.
    when="step": checked after every event; the run stops at the first break,
                 so the timeline shows exactly where it went wrong.
    """
    if when not in ("end", "step"):
        raise ValueError("when must be 'end' or 'step'")

    def wrap(f: Callable[[Trace], bool]) -> Rule:
        return Rule(f, name or f.__name__, when, (f.__doc__ or "").strip())

    return wrap(fn) if fn is not None else wrap
