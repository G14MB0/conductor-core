"""Operations page combining monitoring, scheduling, and manual runs."""
from __future__ import annotations

import json
from typing import Dict, List, Optional

import streamlit as st

from dashboard.services.runtime import OrchestratorRuntime, RunSummary


_AUTO_REFRESH_SCRIPT_KEY = "__conductor_refresh_script"


def render(runtime: OrchestratorRuntime) -> None:
    """Render the operations dashboard."""

    st.header("Operazioni e monitoraggio")
    flows = runtime.list_flows()

    col_refresh = st.columns([1, 1, 4])
    with col_refresh[0]:
        auto_refresh = st.checkbox("Aggiorna automaticamente", value=True, key="ops-auto-refresh")
    with col_refresh[1]:
        interval_seconds = st.number_input(
            "Intervallo (s)", min_value=1, max_value=60, value=1, step=1, key="ops-refresh-interval"
        )

    if auto_refresh:
        _inject_auto_refresh(interval_seconds)
    else:
        _inject_auto_refresh(None)

    st.caption("Monitora i flow registrati, programma le esecuzioni e configura i payload di run dedicati.")

    if not flows:
        st.info("Registra almeno un flow per utilizzare gli strumenti operativi.")
        return

    monitor_tab, schedules_tab, runs_tab, logs_tab = st.tabs(["Monitoraggio", "Schedulazione", "Esecuzione manuale", "Log runtime"])
    with monitor_tab:
        _render_monitor(runtime)
    with schedules_tab:
        _render_schedules(runtime, flows)
    with runs_tab:
        _render_manual_runs(runtime, flows)
    with logs_tab:
        _render_logs(runtime)


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

def _render_monitor(runtime: OrchestratorRuntime) -> None:
    snapshot = runtime.runs()
    active = snapshot["active"]
    history = snapshot["history"]

    st.subheader("Esecuzioni attive")
    if not active:
        st.success("Nessuna esecuzione in corso.")
    else:
        for entry in active:
            _render_active_run(runtime, entry)

    st.subheader("Storico recenti")
    if not history:
        st.info("Non sono ancora presenti esecuzioni completate.")
        return

    for entry in history[:20]:
        _render_history_entry(entry)


def _render_active_run(runtime: OrchestratorRuntime, summary: RunSummary) -> None:
    key_suffix = str(abs(hash(summary.id)))
    cols = st.columns([3, 2, 2, 2])
    cols[0].markdown(f"**{summary.flow_name}** - stato :orange[In esecuzione]")
    cols[1].markdown(f"Avviato: `{summary.started_at.isoformat()}`")
    if summary.duration is not None:
        cols[2].markdown(f"Durata: `{summary.duration:.1f}s`")
    cols[3].markdown(f"Run-id: `{summary.id}`")

    payload_col, metadata_col = st.columns(2)
    if summary.payload_preview is not None:
        payload_col.json(summary.payload_preview)
    if summary.metadata:
        metadata_col.json(summary.metadata)

    if st.button("Termina esecuzione", key=f"cancel-run-{key_suffix}", type="primary"):
        runtime.cancel_run(summary.id)
        st.info("Richiesta di stop inviata.")

    st.divider()


def _render_history_entry(summary: RunSummary) -> None:
    key_suffix = str(abs(hash(summary.id)))
    colour = {
        "completed": "green",
        "error": "red",
        "cancelled": "gray",
    }.get(summary.status, "blue")
    label = f"{summary.flow_name} - stato finale :{colour}[{summary.status.upper()}]"
    with st.expander(label):
        st.markdown(f"**Run-id:** `{summary.id}`")
        st.markdown(f"**Inizio:** `{summary.started_at.isoformat()}`")
        if summary.finished_at:
            st.markdown(f"**Fine:** `{summary.finished_at.isoformat()}`")
        if summary.duration is not None:
            st.markdown(f"**Durata:** `{summary.duration:.1f}s`")
        if summary.metadata:
            st.markdown("**Metadata:**")
            st.json(summary.metadata)
        if summary.payload_preview is not None:
            st.markdown("**Payload:**")
            st.json(summary.payload_preview)
        if summary.error:
            st.error(summary.error)


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def _render_schedules(runtime: OrchestratorRuntime, flows: List[str]) -> None:
    st.subheader("Nuova schedulazione")
    with st.form("schedule-form"):
        selected_flows = st.multiselect("Flow da schedulare", flows, key="schedule-flows")
        mode = st.radio("Tipo", ["Intervallo (s)", "Cron"], horizontal=True, key="schedule-mode")
        interval_value: Optional[float] = None
        cron_expression: Optional[str] = None
        if mode.startswith("Intervallo"):
            interval_value = st.number_input("Ogni quanti secondi?", min_value=1, value=60, step=1)
        else:
            cron_expression = st.text_input("Espressione cron", placeholder="es. 0 */2 * * *")
        payload_text = st.text_area("Payload JSON (opzionale)", key="schedule-payload")
        metadata_text = st.text_area("Metadata JSON (opzionale)", key="schedule-metadata")
        start_immediately = st.checkbox("Esegui subito la prima volta", value=False, key="schedule-start-now")
        submit = st.form_submit_button("Crea schedulazione", type="primary")

    if submit:
        if not selected_flows:
            st.error("Seleziona almeno un flow.")
        else:
            payload, payload_error = _parse_optional_json(payload_text)
            metadata, metadata_error = _parse_optional_json(metadata_text)
            if payload_error or metadata_error:
                _show_errors(payload_error, metadata_error)
            else:
                try:
                    schedules = []
                    for flow_name in selected_flows:
                        schedule = runtime.schedule_flow(
                            flow_name,
                            interval=float(interval_value) if interval_value else None,
                            cron=cron_expression,
                            payload=payload,
                            metadata=metadata,
                            start_immediately=start_immediately,
                        )
                        schedules.append(schedule)
                except Exception as exc:  # pragma: no cover - UI feedback
                    st.error(f"Schedulazione fallita: {exc}")
                else:
                    ids = ", ".join(s.id for s in schedules)
                    st.success(f"Schedulazioni create con successo (id: {ids}).")

    st.subheader("Schedulazioni attive")
    schedules = runtime.list_schedules()
    if not schedules:
        st.info("Non ci sono schedulazioni attive.")
        return

    for schedule in schedules:
        with st.expander(f"{schedule.flow_name} - prossimo run {schedule.next_run.isoformat()}"):
            st.markdown(f"**ID:** `{schedule.id}`")
            st.markdown(f"**Trigger:** `{type(schedule.trigger).__name__}`")
            if getattr(schedule.trigger, "interval", None):
                st.markdown(f"**Intervallo:** {getattr(schedule.trigger, 'interval')}")
            if getattr(schedule.trigger, "expression", None):
                st.markdown(f"**Cron:** `{getattr(schedule.trigger, 'expression')}`")
            if schedule.payload is not None:
                st.markdown("**Payload:**")
                st.json(schedule.payload)
            if schedule.metadata:
                st.markdown("**Metadata:**")
                st.json(schedule.metadata)
            cols = st.columns(2)
            if cols[0].button("Esegui ora", key=f"schedule-run-{schedule.id}"):
                runtime.run_flow(
                    schedule.flow_name,
                    payload=schedule.payload,
                    metadata=schedule.metadata,
                    background=True,
                    schedule_id=schedule.id,
                )
                st.info("Esecuzione avviata in background.")
            if cols[1].button("Annulla schedulazione", key=f"schedule-cancel-{schedule.id}"):
                runtime.cancel_schedule(schedule.id)
                st.success("Schedulazione annullata.")
                st.rerun()


# ---------------------------------------------------------------------------
# Manual runs
# ---------------------------------------------------------------------------

def _render_manual_runs(runtime: OrchestratorRuntime, flows: List[str]) -> None:
    st.subheader("Esecuzione manuale")
    with st.form("manual-run-form"):
        selected_flows = st.multiselect("Flow da eseguire", flows, key="manual-flows")
        payload_text = st.text_area("Payload JSON (opzionale)", key="manual-payload")
        metadata_text = st.text_area("Metadata JSON (opzionale)", key="manual-metadata")
        mode = st.radio("Modalita", ["Sincrona", "Background"], horizontal=True, key="manual-mode")
        submit = st.form_submit_button("Avvia esecuzione", type="primary")

    if not submit:
        return

    if not selected_flows:
        st.error("Seleziona almeno un flow.")
        return

    payload, payload_error = _parse_optional_json(payload_text)
    metadata, metadata_error = _parse_optional_json(metadata_text)
    if payload_error or metadata_error:
        _show_errors(payload_error, metadata_error)
        return

    background = mode == "Background"
    for flow_name in selected_flows:
        try:
            summary = runtime.run_flow(
                flow_name,
                payload=payload,
                metadata=metadata,
                background=background,
            )
        except Exception as exc:  # pragma: no cover - UI feedback
            st.error(f"Esecuzione di '{flow_name}' fallita: {exc}")
        else:
            if background:
                st.info(f"Flow '{flow_name}' avviato in background (run-id {summary.id}).")
            else:
                status = (summary.status or "").lower()
                if status == "error":
                    details = summary.error or summary.metadata.get("last_status", "error")
                    st.error(f"Flow '{flow_name}' terminato con errore: {details}.")
                else:
                    display_status = summary.metadata.get("last_status", summary.status)
                    st.success(f"Flow '{flow_name}' completato con stato {display_status}.")




# ---------------------------------------------------------------------------
# Log viewer
# ---------------------------------------------------------------------------

def _render_logs(runtime: OrchestratorRuntime) -> None:
    st.subheader("Log runtime")
    cols = st.columns([2, 1, 1])
    level_options = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    selected = cols[0].selectbox(
        "Livello minimo",
        level_options,
        index=2,
        key="logs-min-level",
    )
    tail_lines = cols[1].number_input(
        "Righe per container",
        min_value=10,
        max_value=2000,
        value=200,
        step=10,
        key="logs-tail-lines",
    )
    if cols[2].button("Svuota log", key="logs-clear"):
        runtime.clear_logs()
        st.success("Log eliminati.")

    filter_level = None if selected == "ALL" else selected
    entries = runtime.logs(minimum_level=filter_level)
    if not entries:
        st.info("Non ci sono log da mostrare.")
        return

    rows = [
        {
            "timestamp": entry.timestamp,
            "level": entry.level,
            "logger": entry.logger,
            "message": entry.message,
        }
        for entry in entries
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.subheader("Log container Docker")
    container_logs = runtime.container_logs(tail=int(tail_lines))
    if not container_logs:
        st.info("Nessun container configurato per la raccolta dei log.")
        return

    for snapshot in container_logs:
        label = f"{snapshot.name}"
        with st.expander(label, expanded=snapshot.error is not None):
            if snapshot.error:
                st.warning(f"Impossibile leggere i log: {snapshot.error}")
            if snapshot.content:
                st.code(snapshot.content, language="log")
            elif not snapshot.error:
                st.info("Nessun log disponibile per il container specificato.")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _inject_auto_refresh(interval_seconds: Optional[int]) -> None:
    if not interval_seconds:
        clear_script = "<script>if (window.__conductorRefresh) { clearTimeout(window.__conductorRefresh); }</script>"
        st.markdown(clear_script, unsafe_allow_html=True)
        return
    millis = max(1, int(interval_seconds)) * 1000
    script = (
        "<script>"
        "if (window.__conductorRefresh) { clearTimeout(window.__conductorRefresh); }"
        f"window.__conductorRefresh = setTimeout(() => window.location.reload(), {millis});"
        "</script>"
    )
    st.markdown(script, unsafe_allow_html=True)


def _parse_optional_json(value: str) -> tuple[Optional[object], Optional[str]]:
    text = value.strip()
    if not text:
        return None, None
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, str(exc)


def _show_errors(*errors: Optional[str]) -> None:
    for err in errors:
        if err:
            st.error(f"Formato JSON non valido: {err}")


__all__ = ["render"]


if __name__ == "__main__":  # pragma: no cover - module execution support
    from dashboard import state

    render(state.get_runtime())

