"""The @tool decorator.

Outside a Backsight run, a decorated tool is just the original function.
Inside a run, every call goes through the world, which can delay it, fail it,
or report a timeout after the action already happened.
"""
from __future__ import annotations

import functools
import inspect
import os
from typing import Any, Callable

from .world import ToolSpec, current_world


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    timeout_s: float | None = None,
    sim: Any = None,
    cassette: str | None = None,
    effect: str | None = None,
    faults: dict[str, float] | None = None,
    name: str | None = None,
):
    """Wrap an agent tool.

    timeout_s: how long the agent's client waits before giving up.
    sim:       a fake class (one fresh instance per run, method named like the
               tool) or a function sim(world, **args).
    cassette:  a JSON file of real responses, recorded once with
               `backsight record` and replayed in every run after. Relative
               paths are resolved next to the file that defines the tool.
    effect:    for cassette tools that change something (send, charge, update),
               the effect kind to record, so rules can see it happened.
    faults:    per-tool fault rates that override the profile's.

    A tool with neither sim nor cassette refuses to call the real system
    during a run unless allow_real=True.
    """
    if sim is not None and cassette is not None:
        raise ValueError("give a tool either sim= or cassette=, not both")

    def wrap(f: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or f.__name__
        signature = inspect.signature(f)
        tape = cassette
        if tape is not None and not os.path.isabs(tape):
            tape = os.path.join(os.path.dirname(os.path.abspath(inspect.getfile(f))), tape)
        spec = ToolSpec(tool_name, f, timeout_s, sim, faults, tape, effect)

        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            world = current_world()
            if world is None:
                return f(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            return world.call_tool(spec, dict(bound.arguments))

        wrapper.__backsight_tool__ = spec  # type: ignore[attr-defined]
        return wrapper

    return wrap(fn) if fn is not None else wrap
