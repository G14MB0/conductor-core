"""Streamlit entrypoint for the Conductor operations dashboard."""
from __future__ import annotations

import streamlit as st

from dashboard import state
from dashboard.pages import deployment, operations, settings


def main() -> None:
    st.set_page_config(page_title="Conductor Dashboard", layout="wide")
    runtime = state.get_runtime()

    pages = {
        "Deployment": lambda: deployment.render(runtime),
        "Operazioni": lambda: operations.render(runtime),
        "Impostazioni": settings.render,
    }

    with st.sidebar:
        st.title("Conductor")
        flows_registered = runtime.list_flows()
        runs_snapshot = runtime.runs()
        st.metric("Flow registrati", len(flows_registered))
        st.metric("Esecuzioni attive", len(runs_snapshot["active"]))
        if st.button("Aggiorna dati"):
            st.rerun()
        st.divider()
        selection = st.radio("Navigazione", list(pages.keys()))

    pages[selection]()


if __name__ == "__main__":  # pragma: no cover - Streamlit entrypoint
    main()
