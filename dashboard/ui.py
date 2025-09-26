"""Shared UI helpers for Streamlit pages."""
from __future__ import annotations

import streamlit as st

_LAYOUT_FLAG = "_conductor_layout_set"


def ensure_wide_layout(*, title: str | None = None, page_icon: str | None = None) -> None:
    """Ensure the Streamlit app uses the wide layout exactly once per session."""

    if st.session_state.get(_LAYOUT_FLAG):
        return
    st.set_page_config(
        page_title=title or "Conductor Dashboard",
        page_icon=page_icon,
        layout="wide",
    )
    st.session_state[_LAYOUT_FLAG] = True
