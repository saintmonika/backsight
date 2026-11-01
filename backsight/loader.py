"""Load a scenario file: it defines scenario(world), RULES, and optionally PROFILE."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from types import ModuleType


def load_scenario(path: str) -> ModuleType:
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"no scenario file at {path}")
    name = "backsight_scenario_" + hashlib.sha256(path.encode()).hexdigest()[:10]
    if name in sys.modules:
        return sys.modules[name]
    folder = os.path.dirname(path)
    if folder not in sys.path:
        sys.path.insert(0, folder)  # so the scenario can import its own agent module
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    for required in ("scenario", "RULES"):
        if not hasattr(module, required):
            raise AttributeError(f"{path} must define `{required}`")
    return module
