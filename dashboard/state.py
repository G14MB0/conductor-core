"""Session state helpers for the Streamlit dashboard."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import streamlit as st

from conductor.config import GlobalConfig, load_global_config

from dashboard.services.deployments import load_saved_deployments
from dashboard.services.runtime import OrchestratorRuntime


_RUNTIME_KEY = "dashboard_runtime"
_GLOBAL_CONFIG_KEY = "dashboard_global_config"


def get_runtime() -> OrchestratorRuntime:
    if _RUNTIME_KEY not in st.session_state:
        runtime = OrchestratorRuntime()
        failures = []
        for name, deployment in load_saved_deployments().items():
            try:
                runtime.register_flow(deployment, replace=True)
            except Exception as exc:  # pragma: no cover - restoration feedback
                failures.append((name, str(exc)))
        st.session_state[_RUNTIME_KEY] = runtime
        if failures:
            st.session_state["_runtime_restore_failures"] = failures
    runtime = st.session_state[_RUNTIME_KEY]
    failures = st.session_state.pop("_runtime_restore_failures", None)
    if failures:
        for name, message in failures:
            st.warning(f"Impossibile ripristinare il flow '{name}': {message}")
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
    if _RUNTIME_KEY in st.session_state:
        runtime: OrchestratorRuntime = st.session_state[_RUNTIME_KEY]
        runtime.shutdown()
        st.session_state.pop(_RUNTIME_KEY, None)
    st.session_state.pop(_GLOBAL_CONFIG_KEY, None)


__all__ = [
    "get_runtime",
    "get_global_config_state",
    "set_global_config",
    "mark_global_config_dirty",
    "mark_global_config_clean",
    "reset_state",
]
