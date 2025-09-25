"""Session state helpers for the Streamlit dashboard."""
from __future__ import annotations

from typing import Any, Dict, Optional

import streamlit as st

from conductor.config import GlobalConfig

from dashboard.services.runtime import OrchestratorRuntime


_RUNTIME_KEY = "dashboard_runtime"
_GLOBAL_CONFIG_KEY = "dashboard_global_config"


def get_runtime() -> OrchestratorRuntime:
    if _RUNTIME_KEY not in st.session_state:
        st.session_state[_RUNTIME_KEY] = OrchestratorRuntime()
    return st.session_state[_RUNTIME_KEY]


def get_global_config_state() -> Dict[str, Any]:
    state = st.session_state.setdefault(
        _GLOBAL_CONFIG_KEY,
        {
            "config": GlobalConfig.from_mapping({}),
            "path": None,
            "dirty": False,
        },
    )
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
