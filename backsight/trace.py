"""The trace: an ordered log of everything that happened in one run.

Two views matter:
  - effects: what actually happened in the (fake) world
  - seen_by_agent: what the agent was told

Rules check effects. The gap between the two is where expensive bugs live.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass
class Event:
    t_ms: int
    kind: str  # call | seen | effect | sleep | reply
    tool: str | None = None
    call: int | None = None
    args: dict[str, Any] | None = None
    detail: dict[str, Any] | None = None


@dataclass
class Trace:
    events: list[Event] = field(default_factory=list)
    _listeners: list[Callable[[Event], None]] = field(default_factory=list, repr=False)

    def add(self, t_ms: int, kind: str, tool=None, call=None, args=None, detail=None) -> Event:
        event = Event(t_ms, kind, tool, call, args, detail)
        self.events.append(event)
        for listener in self._listeners:
            listener(event)
        return event

    def on_add(self, listener: Callable[[Event], None]) -> None:
        self._listeners.append(listener)

    # --- queries used by rules -------------------------------------------
    def calls(self, tool: str) -> list[Event]:
        """Every call the agent made to a tool."""
        return [e for e in self.events if e.kind == "call" and e.tool == tool]

    def effects(self, kind: str) -> list[Event]:
        """Actions that really happened, including ones the agent was told had failed."""
        return [e for e in self.events if e.kind == "effect" and e.tool == kind]

    def seen_by_agent(self, tool: str) -> list[Event]:
        """What the agent got back from each call."""
        return [e for e in self.events if e.kind == "seen" and e.tool == tool]

    def replies(self) -> list[str]:
        """Everything the agent said to the user."""
        return [e.detail["text"] for e in self.events if e.kind == "reply"]

    # --- identity --------------------------------------------------------
    def to_jsonable(self) -> list[dict[str, Any]]:
        return [asdict(e) for e in self.events]

    def hash(self) -> str:
        blob = json.dumps(self.to_jsonable(), sort_keys=True, default=repr, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    # --- human view ------------------------------------------------------
    def timeline(self) -> list[str]:
        return [format_event(e) for e in self.events]


def _fmt_args(args: dict[str, Any] | None) -> str:
    if not args:
        return ""
    return ", ".join(f"{k}={v!r}" for k, v in args.items() if v is not None)


def format_event(e: Event) -> str:
    t = f"{e.t_ms / 1000:8.3f}s"
    if e.kind == "call":
        return f"{t}  agent -> {e.tool}({_fmt_args(e.args)})"
    if e.kind == "effect":
        return f"{t}  world    {e.tool} applied ({_fmt_args(e.args)})"
    if e.kind == "seen":
        d = e.detail or {}
        outcome = d.get("outcome", "ok")
        note = ""
        if outcome == "timeout" and d.get("effect_applied"):
            note = "  (it actually went through)"
        fault = f"  [fault: {d['fault']}]" if d.get("fault") else ""
        return f"{t}  agent <- {e.tool}: {outcome.upper() if outcome != 'ok' else 'ok'}{note}{fault}"
    if e.kind == "sleep":
        return f"{t}  agent    waits {e.detail['seconds']}s"
    if e.kind == "reply":
        return f"{t}  agent    says: {e.detail['text']!r}"
    return f"{t}  {e.kind} {e.tool or ''}"
