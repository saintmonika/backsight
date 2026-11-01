"""Faults, fault profiles, and fault plans.

Every fault decision is derived from (seed, tool, call index), never from call
order or wall-clock time. That keeps old seeds reproducing old bugs even when
other parts of the agent change, and lets a plan be pinned for minimizing.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

TIMEOUT_BEFORE = "timeout_before_effect"  # agent gets TimeoutError; nothing happened
TIMEOUT_AFTER = "timeout_after_effect"    # action happened; agent gets TimeoutError
ERROR = "error"                           # tool raises a 503
LATENCY_SPIKE = "latency_spike"           # call takes much longer than usual

FAULT_KINDS = (TIMEOUT_BEFORE, TIMEOUT_AFTER, ERROR, LATENCY_SPIKE)


def derive_seed(seed: int, *parts: object) -> int:
    """Stable across processes and Python versions (unlike hash())."""
    text = "|".join([str(seed), *(str(p) for p in parts)])
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


@dataclass(frozen=True)
class Profile:
    name: str
    rates: dict[str, float] = field(default_factory=dict)  # chance per call
    base_latency_ms: tuple[int, int] = (40, 400)
    spike_ms: tuple[int, int] = (2500, 4500)
    budget: int = 3  # max faults per run, so failures stay explainable

    def __hash__(self) -> int:
        return hash(self.name)


CALM = Profile("calm")
FLAKY_NETWORK = Profile(
    "flaky_network",
    rates={TIMEOUT_AFTER: 0.08, TIMEOUT_BEFORE: 0.05, LATENCY_SPIKE: 0.05},
)
DEGRADED_API = Profile("degraded_api", rates={ERROR: 0.15, LATENCY_SPIKE: 0.05})
CHAOS = Profile(
    "chaos",
    rates={TIMEOUT_AFTER: 0.15, TIMEOUT_BEFORE: 0.10, ERROR: 0.10, LATENCY_SPIKE: 0.10},
    budget=5,
)

PROFILES = {p.name: p for p in (CALM, FLAKY_NETWORK, DEGRADED_API, CHAOS)}


def get_profile(name_or_profile: str | Profile) -> Profile:
    if isinstance(name_or_profile, Profile):
        return name_or_profile
    try:
        return PROFILES[name_or_profile.replace("-", "_")]
    except KeyError:
        raise ValueError(f"unknown profile {name_or_profile!r}; choose from {sorted(PROFILES)}") from None


@dataclass(frozen=True, order=True)
class Fault:
    tool: str
    call: int  # 0-based index of the call to that tool
    kind: str

    def label(self) -> str:
        return f"{self.kind} on {self.tool} call #{self.call + 1}"

    def to_json(self) -> dict:
        return {"tool": self.tool, "call": self.call, "kind": self.kind}

    @classmethod
    def from_json(cls, d: dict) -> "Fault":
        return cls(d["tool"], int(d["call"]), d["kind"])


class FaultPlan:
    """Decides which fault (if any) hits each tool call.

    pinned=None  -> faults come from the seed and the profile's rates.
    pinned=set   -> only these exact faults fire; everything else runs clean.
    """

    def __init__(self, seed: int, profile: Profile, pinned: frozenset[Fault] | None = None):
        self.seed = seed
        self.profile = profile
        self.pinned = pinned

    def decide(self, tool: str, call: int, fired_so_far: int, rates: dict[str, float] | None = None) -> str | None:
        if self.pinned is not None:
            for f in self.pinned:
                if f.tool == tool and f.call == call:
                    return f.kind
            return None
        if fired_so_far >= self.profile.budget:
            return None
        rates = rates if rates is not None else self.profile.rates
        roll = random.Random(derive_seed(self.seed, "fault", tool, call)).random()
        cumulative = 0.0
        for kind in FAULT_KINDS:  # fixed order, so decisions never depend on dict order
            cumulative += rates.get(kind, 0.0)
            if roll < cumulative:
                return kind
        return None

    def latency_ms(self, tool: str, call: int, kind: str | None) -> int:
        rng = random.Random(derive_seed(self.seed, "latency", tool, call))
        lo, hi = self.profile.base_latency_ms
        ms = rng.randint(lo, hi)
        if kind == LATENCY_SPIKE:
            ms += rng.randint(*self.profile.spike_ms)
        return ms
