"""The world: the fake environment one run happens in.

The agent's tools, clock, and randomness all come from here, so one seed
decides everything that can change between runs.
"""
from __future__ import annotations

import contextvars
import inspect
from dataclasses import dataclass, field
import random
import uuid as _uuid
from typing import Any, Callable

from .cassettes import Cassette, CassetteMiss
from .faults import ERROR, TIMEOUT_AFTER, TIMEOUT_BEFORE, Fault, FaultPlan, Profile, derive_seed
from .trace import Trace

_current: contextvars.ContextVar["World | None"] = contextvars.ContextVar("backsight_world", default=None)


def current_world() -> "World | None":
    return _current.get()


class ToolError(Exception):
    """A tool failed with an error status (simulated 5xx)."""

    def __init__(self, tool: str, status: int = 503):
        super().__init__(f"{tool} failed with status {status}")
        self.tool = tool
        self.status = status


class RealCallBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    real_fn: Callable[..., Any]
    timeout_s: float | None = None
    sim: Any = None
    rates: dict[str, float] | None = field(default=None, hash=False)
    cassette: str | None = None  # absolute path to recorded responses
    effect: str | None = None    # effect kind to record when a cassette call succeeds


class FakeClock:
    """Simulated time in whole milliseconds, so traces hash identically."""

    def __init__(self) -> None:
        self.ms = 0

    def advance_ms(self, ms: int) -> None:
        if ms < 0:
            raise ValueError("time only moves forward")
        self.ms += int(ms)


class World:
    def __init__(self, seed: int, profile: Profile, plan: FaultPlan, allow_real: bool = False, record: bool = False):
        self.seed = seed
        self.profile = profile
        self.plan = plan
        self.allow_real = allow_real
        self.record = record  # True only during `backsight record`
        self.clock = FakeClock()
        self.trace = Trace()
        self.faults_fired: list[Fault] = []
        self.cassette_miss: CassetteMiss | None = None
        self._streams: dict[str, random.Random] = {}
        self._fakes: dict[type, Any] = {}
        self._cassettes: dict[str, Cassette] = {}
        self._call_counts: dict[str, int] = {}
        self._token: contextvars.Token | None = None

    # --- context ---------------------------------------------------------
    def __enter__(self) -> "World":
        self._token = _current.set(self)
        return self

    def __exit__(self, *exc) -> None:
        if self._token is not None:
            _current.reset(self._token)
            self._token = None

    # --- deterministic replacements for time and randomness --------------
    def now(self) -> float:
        """Simulated seconds since the run started."""
        return self.clock.ms / 1000

    def sleep(self, seconds: float) -> None:
        self.trace.add(self.clock.ms, "sleep", detail={"seconds": seconds})
        self.clock.advance_ms(round(seconds * 1000))

    def random(self, name: str = "default") -> random.Random:
        """A named random stream. Adding a new stream never shifts the others."""
        if name not in self._streams:
            self._streams[name] = random.Random(derive_seed(self.seed, "stream", name))
        return self._streams[name]

    def uuid(self) -> str:
        return str(_uuid.UUID(int=self.random("uuid").getrandbits(128), version=4))

    def reply(self, text: str) -> None:
        """Record something the agent said to the user."""
        self.trace.add(self.clock.ms, "reply", detail={"text": text})

    # --- ground truth ----------------------------------------------------
    def effect(self, kind: str, **data: Any) -> None:
        """Called by fakes when something really happens (a refund, an email)."""
        self.trace.add(self.clock.ms, "effect", tool=kind, args=data)

    def fake(self, cls: type) -> Any:
        """One fresh fake instance per run, so state never leaks between seeds."""
        if cls not in self._fakes:
            self._fakes[cls] = cls(self)
        return self._fakes[cls]

    def cassette(self, path: str, tool: str) -> Cassette:
        if path not in self._cassettes:
            self._cassettes[path] = Cassette(path, tool)
        return self._cassettes[path]

    def save_cassettes(self) -> list[Cassette]:
        saved = [c for c in self._cassettes.values() if c.dirty]
        for c in saved:
            c.save()
        return saved

    # --- the tool proxy --------------------------------------------------
    def call_tool(self, spec: "ToolSpec", args: dict[str, Any]) -> Any:
        name = spec.name
        call = self._call_counts.get(name, 0)
        self._call_counts[name] = call + 1

        kind = self.plan.decide(name, call, len(self.faults_fired), spec.rates)
        if kind:
            self.faults_fired.append(Fault(name, call, kind))
        latency = self.plan.latency_ms(name, call, kind)
        timeout_ms = round(spec.timeout_s * 1000) if spec.timeout_s else None

        self.trace.add(self.clock.ms, "call", tool=name, call=call, args=dict(args))

        if kind == TIMEOUT_BEFORE:
            self.clock.advance_ms(timeout_ms or latency)
            self._seen(name, call, "timeout", kind, effect_applied=False)
            raise TimeoutError(f"{name} timed out")

        if kind == ERROR:
            self.clock.advance_ms(latency)
            self._seen(name, call, "error", kind)
            raise ToolError(name, 503)

        result = self._run(spec, args)

        if kind == TIMEOUT_AFTER or (timeout_ms is not None and latency > timeout_ms):
            self.clock.advance_ms(timeout_ms or latency)
            self._seen(name, call, "timeout", kind, effect_applied=True)
            raise TimeoutError(f"{name} timed out")

        self.clock.advance_ms(latency)
        self._seen(name, call, "ok", kind, result=result)
        return result

    def _run(self, spec: "ToolSpec", args: dict[str, Any]) -> Any:
        name = spec.name
        if spec.cassette:
            tape = self.cassette(spec.cassette, name)
            if self.record:
                result = spec.real_fn(**args)  # the one time the real API is called
                tape.put(args, result)
            else:
                try:
                    result = tape.get(args)
                except CassetteMiss as miss:
                    self.cassette_miss = miss  # remembered even if the agent swallows it
                    raise
            if spec.effect:
                self.effect(spec.effect, **args)
            return result
        if spec.sim is None:
            if not self.allow_real:
                raise RealCallBlocked(
                    f"tool {name!r} has no sim= fake or cassette=, so it would call the real system. "
                    f"Give it a fake (@backsight.tool(sim=fakes.Payments)), record its responses "
                    f"(@backsight.tool(cassette='cassettes/{name}.json')), or pass allow_real=True."
                )
            return spec.real_fn(**args)
        if inspect.isclass(spec.sim):
            return getattr(self.fake(spec.sim), name)(**args)
        return spec.sim(self, **args)

    def _seen(self, name: str, call: int, outcome: str, fault: str | None, effect_applied=None, result=None) -> None:
        detail: dict[str, Any] = {"outcome": outcome}
        if fault:
            detail["fault"] = fault
        if effect_applied is not None:
            detail["effect_applied"] = effect_applied
        if result is not None:
            detail["result"] = result
        self.trace.add(self.clock.ms, "seen", tool=name, call=call, detail=detail)
