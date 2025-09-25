"""Overview dashboard showing executions status."""
from __future__ import annotations

from typing import List

import streamlit as st

from conductor.diagram import render_mermaid_diagram

from dashboard.components.mermaid import mermaid_diagram
from dashboard.services.runtime import OrchestratorRuntime, RunSummary


def render(runtime: OrchestratorRuntime) -> None:
    st.header("Monitoraggio esecuzioni")
    runs = runtime.runs()
    active = runs["active"]
    history = runs["history"]

    st.subheader("Esecuzioni attive")
    if not active:
        st.success("Nessuna esecuzione in corso.")
    else:
        _render_active_runs(runtime, active)

    st.subheader("Storico esecuzioni")
    if not history:
        st.info("Non sono ancora presenti esecuzioni concluse.")
    else:
        for summary in history:
            _render_history_entry(runtime, summary)


def _render_active_runs(runtime: OrchestratorRuntime, runs: List[RunSummary]) -> None:
    for idx, summary in enumerate(runs):
        with st.container():
            cols = st.columns([2, 1, 1])
            cols[0].markdown(
                f"**{summary.flow_name}** — stato: :orange[In esecuzione]"
            )
            cols[1].markdown(
                f"Avviato: `{summary.started_at.isoformat()}`"
            )
            if summary.duration is not None:
                cols[2].markdown(f"Durata: `{summary.duration:.2f}s`")
            if st.button(
                "Interrompi esecuzione",
                key=f"cancel-run-{summary.id}",
                type="primary",
            ):
                runtime.cancel_run(summary.id)
                st.rerun()
            if summary.metadata:
                with st.expander("Metadata"):
                    st.json(summary.metadata)
            if summary.payload_preview is not None:
                with st.expander("Payload"):
                    st.write(summary.payload_preview)
        if idx < len(runs) - 1:
            st.divider()


def _render_history_entry(runtime: OrchestratorRuntime, summary: RunSummary) -> None:
    status_colour = {
        "completed": "green",
        "error": "red",
        "cancelled": "gray",
    }.get(summary.status, "blue")
    label = f"{summary.flow_name} — {summary.status.upper()}"
    with st.expander(label):
        st.markdown(
            f"**Stato finale:** :{status_colour}[{summary.status}]"
        )
        st.markdown(f"**Inizio:** `{summary.started_at.isoformat()}`")
        if summary.finished_at:
            st.markdown(f"**Fine:** `{summary.finished_at.isoformat()}`")
        if summary.duration is not None:
            st.markdown(f"**Durata:** `{summary.duration:.2f}s`")
        if summary.metadata:
            st.markdown("**Metadata:**")
            st.json(summary.metadata)
        if summary.payload_preview is not None:
            st.markdown("**Payload:**")
            st.write(summary.payload_preview)
        if summary.error:
            st.error(summary.error)
        result = summary.result
        if result and result.trace:
            show = st.toggle(
                "Mostra diagramma (trace)",
                key=f"diagram-toggle-{summary.id}",
            )
            if show:
                try:
                    flow_config = runtime.get_flow_config(summary.flow_name)
                except KeyError:
                    st.warning(
                        "Il flow non è più registrato, impossibile generare il diagramma."
                    )
                else:
                    mermaid = render_mermaid_diagram(
                        flow_config,
                        trace=result.trace,
                        include_metadata=True,
                        title=f"{summary.flow_name} (esecuzione)",
                    )
                    mermaid_diagram(
                        mermaid,
                        key=f"mermaid-{summary.id}",
                        height=650,
                    )


__all__ = ["render"]
