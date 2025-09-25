"""Scheduling management page."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Dict, Optional

import streamlit as st

from dashboard.services.runtime import OrchestratorRuntime


def render(runtime: OrchestratorRuntime) -> None:
    st.header("Scheduler")
    flows = runtime.list_flows()
    if not flows:
        st.info("Registra almeno un flow per definire una schedulazione.")
        return

    _schedule_form(runtime, flows)
    st.divider()
    _schedules_table(runtime)


def _schedule_form(runtime: OrchestratorRuntime, flows: list[str]) -> None:
    with st.form("schedule-form"):
        st.subheader("Nuova schedulazione")
        flow_name = st.selectbox("Flow", flows)
        mode = st.radio("Tipo", ["Intervallo (secondi)", "Cron"], horizontal=True)
        interval_value: Optional[float] = None
        cron_expression: Optional[str] = None
        if mode.startswith("Intervallo"):
            interval_value = st.number_input(
                "Ogni quanti secondi?",
                min_value=1,
                value=60,
                step=1,
            )
        else:
            cron_expression = st.text_input("Espressione cron", help="es. 0 */2 * * *")
        payload_text = st.text_area("Payload (JSON)", height=120)
        metadata_text = st.text_area("Metadata (JSON)", height=120)
        start_immediately = st.checkbox("Esegui subito la prima volta", value=False)
        submitted = st.form_submit_button("Crea schedulazione", type="primary")
        if submitted:
            payload, payload_error = _parse_optional_json(payload_text)
            metadata, metadata_error = _parse_optional_json(metadata_text)
            if payload_error or metadata_error:
                _show_errors(payload_error, metadata_error)
                st.stop()
            if mode.startswith("Intervallo") and interval_value:
                interval = float(interval_value)
                if interval <= 0:
                    st.error("L'intervallo deve essere maggiore di zero.")
                    st.stop()
                cron_expression = None
            elif cron_expression:
                interval = None
            else:
                st.error("Specificare un intervallo o una espressione cron valida.")
                st.stop()
            try:
                schedule = runtime.schedule_flow(
                    flow_name,
                    interval=interval,
                    cron=cron_expression,
                    payload=payload,
                    metadata=metadata,
                    start_immediately=start_immediately,
                )
            except Exception as exc:  # pragma: no cover - UI feedback
                st.error(f"Schedulazione fallita: {exc}")
            else:
                st.success(
                    f"Schedulazione creata (id {schedule.id}). Gestiscila dalla tabella sottostante."
                )


def _schedules_table(runtime: OrchestratorRuntime) -> None:
    schedules = runtime.list_schedules()
    st.subheader("Schedulazioni attive")
    if not schedules:
        st.info("Non ci sono schedulazioni attive.")
        return
    for schedule in schedules:
        with st.expander(f"{schedule.flow_name} — prossimo run {schedule.next_run.isoformat()}"):
            st.markdown(f"**ID:** `{schedule.id}`")
            st.markdown(f"**Timezone:** {schedule.timezone}")
            trigger = schedule.trigger
            trigger_name = type(trigger).__name__
            if hasattr(trigger, "interval"):
                interval: timedelta = getattr(trigger, "interval")
                st.markdown(f"**Intervallo:** {interval}")
            if hasattr(trigger, "expression"):
                st.markdown(f"**Cron:** `{getattr(trigger, 'expression')}`")
            if schedule.payload is not None:
                st.markdown("**Payload:**")
                st.write(schedule.payload)
            if schedule.metadata:
                st.markdown("**Metadata:**")
                st.write(schedule.metadata)
            cols = st.columns(2)
            if cols[0].button("Esegui ora", key=f"run-now-{schedule.id}"):
                runtime.run_flow(
                    schedule.flow_name,
                    payload=schedule.payload,
                    metadata=schedule.metadata,
                    background=True,
                    schedule_id=schedule.id,
                )
                st.info("Esecuzione avviata in background.")
            if cols[1].button("Annulla schedulazione", key=f"cancel-{schedule.id}"):
                runtime.cancel_schedule(schedule.id)
                st.success("Schedulazione annullata.")
                st.experimental_rerun()


def _parse_optional_json(value: str) -> tuple[Optional[Any], Optional[str]]:
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
            st.error(f"JSON non valido: {err}")


__all__ = ["render"]
