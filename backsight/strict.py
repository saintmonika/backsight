"""Strict mode: catch code that sneaks around the world.

During a run, calls to real time, global randomness, uuid4, or the network
raise DeterminismLeak with the exact file, line, and fix. A leak costs one
edit, not a debugging session.
"""
from __future__ import annotations

import contextlib
import os
import random
import socket
import sys
import time
import uuid

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


class DeterminismLeak(RuntimeError):
    pass


def _caller() -> str:
    frame = sys._getframe(2)
    while frame is not None:
        filename = os.path.abspath(frame.f_code.co_filename)
        if not filename.startswith(_PACKAGE_DIR) and "contextlib" not in filename:
            return f"{os.path.relpath(filename)}:{frame.f_lineno}"
        frame = frame.f_back
    return "<unknown>"


def _blocker(name: str, fix: str):
    def blocked(*args, **kwargs):
        raise DeterminismLeak(f"{_caller()} calls {name}. Use {fix} so runs replay exactly.")

    return blocked


_PATCHES = [
    (time, "time", "time.time()", "world.now()"),
    (time, "monotonic", "time.monotonic()", "world.now()"),
    (time, "sleep", "time.sleep()", "world.sleep()"),
    (random, "random", "random.random()", "world.random('name').random()"),
    (random, "randint", "random.randint()", "world.random('name').randint()"),
    (random, "choice", "random.choice()", "world.random('name').choice()"),
    (random, "uniform", "random.uniform()", "world.random('name').uniform()"),
    (random, "shuffle", "random.shuffle()", "world.random('name').shuffle()"),
    (uuid, "uuid4", "uuid.uuid4()", "world.uuid()"),
    (socket, "create_connection", "the network", "a sim= fake on the tool"),
]


@contextlib.contextmanager
def guard(enabled: bool = True):
    if not enabled:
        yield
        return
    saved = [(mod, attr, getattr(mod, attr)) for mod, attr, _, _ in _PATCHES]
    saved_connect = socket.socket.connect
    try:
        for mod, attr, label, fix in _PATCHES:
            setattr(mod, attr, _blocker(label, fix))
        socket.socket.connect = _blocker("the network", "a sim= fake on the tool")  # type: ignore[method-assign]
        yield
    finally:
        for mod, attr, original in saved:
            setattr(mod, attr, original)
        socket.socket.connect = saved_connect  # type: ignore[method-assign]
