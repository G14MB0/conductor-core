"""Utility helpers used across conductor modules."""
from __future__ import annotations

import contextlib
import importlib
import os
from functools import lru_cache
from typing import Any, Callable, Dict, Iterator, Mapping


@lru_cache(maxsize=None)
def load_callable(path: str) -> Callable[..., Any]:
    """Load a callable from the dotted path ``module:attribute``."""

    if ":" not in path:
        raise ValueError(f"Callable path '{path}' must include a ':' separating module and attribute")
    module_name, attribute_name = path.split(":", 1)
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attribute_name)
    except AttributeError as exc:  # pragma: no cover - depends on user input
        raise AttributeError(f"Module '{module_name}' has no attribute '{attribute_name}'.") from exc


def merge_env(*mappings: Mapping[str, str]) -> Dict[str, str]:
    """Merge environment mappings preserving the order of precedence."""

    merged: Dict[str, str] = {}
    for mapping in mappings:
        merged.update({str(key): str(value) for key, value in mapping.items()})
    return merged


@contextlib.contextmanager
def scoped_env(env: Mapping[str, str]) -> Iterator[None]:
    """Temporarily update ``os.environ`` within a context."""

    original: Dict[str, str] = {}
    try:
        for key, value in env.items():
            key = str(key)
            if key in os.environ:
                original[key] = os.environ[key]
            os.environ[key] = str(value)
        yield
    finally:
        for key in env:
            key = str(key)
            if key in original:
                os.environ[key] = original[key]
            elif key in os.environ:
                del os.environ[key]


def ensure_dict(mapping: Mapping[str, Any] | None) -> Dict[str, Any]:
    return dict(mapping) if mapping else {}

