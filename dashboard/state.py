"""Session state helpers for the Streamlit dashboard."""
from __future__ import annotations

import os
from functools import wraps
from typing import Any, Dict, Optional

import streamlit as st

from conductor.config import GlobalConfig, load_global_config

from dashboard.services.deployments import load_saved_deployments
from dashboard.services.runtime import OrchestratorRuntime


_RUNTIME_KEY = "dashboard_runtime"
_GLOBAL_CONFIG_KEY = "dashboard_global_config"


if hasattr(st, "cache_resource"):
    _runtime_cache = st.cache_resource
elif hasattr(st, "experimental_singleton"):
    _runtime_cache = st.experimental_singleton  # type: ignore[attr-defined]
else:  # pragma: no cover - compatibility with very old Streamlit releases

    def _runtime_cache(func):  # type: ignore[no-redef]
        cache: Dict[str, OrchestratorRuntime] = {}

        @wraps(func)
        def wrapper(*args, **kwargs):
            if "value" not in cache:
                cache["value"] = func(*args, **kwargs)
            return cache["value"]

        def clear() -> None:
            cache.pop("value", None)

        wrapper.clear = clear  # type: ignore[attr-defined]
        return wrapper


@_runtime_cache
def _get_shared_runtime() -> OrchestratorRuntime:
    """Create or return the shared orchestrator runtime for the dashboard."""

    return OrchestratorRuntime()


def _initialise_runtime(runtime: OrchestratorRuntime) -> None:
    if getattr(runtime, "_dashboard_initialised", False):
        return

    failures = []
    for name, deployment in load_saved_deployments().items():
        try:
            runtime.register_flow(deployment, replace=True)
        except Exception as exc:  # pragma: no cover - restoration feedback
            failures.append((name, str(exc)))
    runtime._dashboard_initialised = True  # type: ignore[attr-defined]
    runtime._dashboard_restore_failures = failures  # type: ignore[attr-defined]


def get_runtime() -> OrchestratorRuntime:
    runtime = _get_shared_runtime()
    st.session_state[_RUNTIME_KEY] = runtime
    _initialise_runtime(runtime)

    failures = getattr(runtime, "_dashboard_restore_failures", [])  # type: ignore[attr-defined]
    if failures and not st.session_state.get("_runtime_restore_reported"):
        for name, message in failures:
            st.warning(f"Impossibile ripristinare il flow '{name}': {message}")
        st.session_state["_runtime_restore_reported"] = True
    return runtime


def get_global_config_state() -> Dict[str, Any]:
    state = st.session_state.setdefault(
        _GLOBAL_CONFIG_KEY,
        {
            "config": GlobalConfig.from_mapping({}),
            "path": None,
            "dirty": False,
            "_initialised": False,
        },
    )
    if not state.get("_initialised"):
        state["_initialised"] = True
        env_path = os.environ.get("CONDUCTOR_GLOBAL_CONFIG")
        if env_path:
            try:
                config = load_global_config(env_path)
            except Exception as exc:  # pragma: no cover - configuration feedback
                state["_load_error"] = str(exc)
                state["_load_source"] = env_path
            else:
                state["config"] = config
                state["path"] = env_path
                state["dirty"] = False
    if state.get("_load_error") and not state.get("_load_error_reported"):
        source = state.get("_load_source", "CONDUCTOR_GLOBAL_CONFIG")
        st.warning(f"Impossibile caricare la global config '{source}': {state['_load_error']}")
        state["_load_error_reported"] = True
    return state


def set_global_config(config: GlobalConfig, *, path: Optional[str] = None, dirty: bool = True) -> None:
    state = get_global_config_state()
    state["config"] = config
    state["path"] = path
    state["dirty"] = dirty


def mark_global_config_dirty() -> None:
    state = get_global_config_state()
    state["dirty"] = True


def mark_global_config_clean() -> None:
    state = get_global_config_state()
    state["dirty"] = False


def reset_state() -> None:
    runtime: Optional[OrchestratorRuntime] = st.session_state.pop(_RUNTIME_KEY, None)
    if runtime is not None:
        runtime.shutdown()
    try:
        _get_shared_runtime.clear()  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - cache cleanup best effort
        pass
    st.session_state.pop("_runtime_restore_reported", None)
    st.session_state.pop(_GLOBAL_CONFIG_KEY, None)


__all__ = [
    "get_runtime",
    "get_global_config_state",
    "set_global_config",
    "mark_global_config_dirty",
    "mark_global_config_clean",
    "reset_state",
]
