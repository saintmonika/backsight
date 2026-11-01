"""The @tool decorator.

Outside a Backsight run, a decorated tool is just the original function.
Inside a run, every call goes through the world, which can delay it, fail it,
or report a timeout after the action already happened.
"""
from __future__ import annotations

import functools
import inspect
from typing import Any, Callable

from .world import current_world


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    timeout_s: float | None = None,
    sim: Any = None,
    faults: dict[str, float] | None = None,
    name: str | None = None,
):
    """Wrap an agent tool.

    timeout_s: how long the agent's client waits before giving up.
    sim:       a fake class (one fresh instance per run, method named like the
               tool) or a function sim(world, **args). Without it, Backsight
               refuses to call the real system unless allow_real=True.
    faults:    per-tool fault rates that override the profile's.
    """

    def wrap(f: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or f.__name__
        signature = inspect.signature(f)

        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            world = current_world()
            if world is None:
                return f(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            return world.call_tool(tool_name, dict(bound.arguments), sim, timeout_s, faults, f)

        wrapper.__backsight_tool__ = tool_name  # type: ignore[attr-defined]
        return wrapper

    return wrap(fn) if fn is not None else wrap
