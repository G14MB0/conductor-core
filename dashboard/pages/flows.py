"""Flow registry management page.""" 
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
from streamlit.runtime.uploaded_file_manager import UploadedFile

from conductor.config import FlowConfig, load_flow_config

from dashboard.services.runtime import OrchestratorRuntime
from dashboard.services.serialization import flow_config_to_dict


def render(runtime: OrchestratorRuntime) -> None:
    st.header("Gestione flow registrati")

    st.subheader("Registrazione di un nuovo flow")
    st.session_state.setdefault("flow_upload_preview", None)
    st.session_state.setdefault("flow_upload_filename", None)

    col_left, col_right = st.columns(2)
    with col_left:
        uploaded = st.file_uploader(
            "Carica configurazione flow (JSON, YAML, TOML)",
            type=["json", "yaml", "yml", "toml"],
            key="flow-config-upload",
        )
    with col_right:
        flow_name = st.text_input("Nome da registrare (opzionale)")
        replace = st.checkbox("Sostituisci se il flow esiste già", value=False)

    if uploaded is not None:
        try:
            parsed = _load_flow_from_upload(uploaded)
        except Exception as exc:  # pragma: no cover - UI feedback
            st.session_state["flow_upload_preview"] = None
            st.session_state["flow_upload_filename"] = None
            st.error(f"Impossibile leggere la configurazione: {exc}")
        else:
            st.session_state["flow_upload_preview"] = parsed
            st.session_state["flow_upload_filename"] = uploaded.name
            st.success(f"File caricato: {parsed.name}")

    pending_flow: Optional[FlowConfig] = st.session_state.get("flow_upload_preview")
    if pending_flow is not None:
        file_name = st.session_state.get("flow_upload_filename")
        st.info(
            f"Flow pronto alla registrazione: **{pending_flow.name}**"
            + (f" (origine `{file_name}`)" if file_name else "")
        )
        preview_cols = st.columns(2)
        if preview_cols[0].button("Registra flow caricato", type="primary"):
            _register_flow(runtime, pending_flow, flow_name, replace)
            st.session_state["flow_upload_preview"] = None
            st.session_state["flow_upload_filename"] = None
            st.session_state["flow-config-upload"] = None
        if preview_cols[1].button("Annulla caricamento"):
            st.session_state["flow_upload_preview"] = None
            st.session_state["flow_upload_filename"] = None
            st.session_state["flow-config-upload"] = None

    st.divider()

    st.subheader("Flow disponibili")
    flow_names = runtime.list_flows()
    if not flow_names:
        st.info("Nessun flow registrato al momento.")
        return

    for idx, name in enumerate(flow_names):
        flow_config = runtime.get_flow_config(name)
        with st.expander(f"{name} ({len(flow_config.nodes)} nodi)"):
            st.markdown(f"**Start:** {', '.join(flow_config.start)}")
            if flow_config.description:
                st.markdown(flow_config.description)
            _render_nodes_table(flow_config)
            _flow_actions(runtime, flow_config, idx)


def _register_flow(
    runtime: OrchestratorRuntime,
    flow: FlowConfig,
    name: Optional[str],
    replace: bool,
) -> None:
    try:
        registered_name = runtime.register_flow(flow, name=name or None, replace=replace)
    except Exception as exc:  # pragma: no cover - propagated to UI
        st.error(f"Registrazione fallita: {exc}")
        return
    st.success(f"Flow '{registered_name}' registrato correttamente.")


def _flow_actions(runtime: OrchestratorRuntime, flow: FlowConfig, index: int) -> None:
    key_suffix = f"{index}-{abs(hash(flow.name))}"
    payload_text = st.text_area(
        "Payload iniziale (JSON opzionale)",
        key=f"payload-{key_suffix}",
        height=120,
    )
    metadata_text = st.text_area(
        "Metadata aggiuntivi (JSON)",
        key=f"metadata-{key_suffix}",
        height=120,
    )

    payload, payload_error = _parse_optional_json(payload_text)
    metadata, metadata_error = _parse_optional_json(metadata_text)

    cols = st.columns(4)
    if cols[0].button("Esegui (sincrono)", key=f"run-sync-{key_suffix}"):
        if payload_error or metadata_error:
            _show_payload_errors(payload_error, metadata_error)
        else:
            summary = runtime.run_flow(
                flow.name,
                payload=payload,
                metadata=metadata,
                background=False,
            )
            st.success(
                f"Esecuzione completata con stato {summary.metadata.get('last_status', 'n/a')}"
            )
    if cols[1].button("Esegui in background", key=f"run-bg-{key_suffix}"):
        if payload_error or metadata_error:
            _show_payload_errors(payload_error, metadata_error)
        else:
            summary = runtime.run_flow(
                flow.name,
                payload=payload,
                metadata=metadata,
                background=True,
            )
            st.info(
                f"Esecuzione avviata (run-id {summary.id}). Monitora nella sezione Monitoraggio."
            )
    if cols[2].button("Apri nel Flow Designer", key=f"designer-{key_suffix}"):
        st.session_state["flow_builder_import"] = flow_config_to_dict(flow)
        st.success("Flow caricato nel designer. Apri la pagina 'Flow Designer'.")
    if cols[3].button("Deregistra", key=f"unregister-{key_suffix}"):
        try:
            runtime.unregister_flow(flow.name)
        except Exception as exc:  # pragma: no cover - UI feedback
            st.error(f"Impossibile deregistrare il flow: {exc}")
        else:
            st.success(f"Flow '{flow.name}' rimosso.")
            st.rerun()


def _render_nodes_table(flow: FlowConfig) -> None:
    rows: List[Dict[str, Any]] = []
    for node in flow.nodes.values():
        transitions = {
            status: ", ".join(successors)
            for status, successors in (node.transitions or {}).items()
        }
        rows.append(
            {
                "id": node.id,
                "executor": node.executor,
                "callable": node.callable or node.image or "",
                "timeout": node.timeout,
                "with_global_state": node.with_global_state,
                "transitions": json.dumps(transitions, ensure_ascii=False),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _load_flow_from_upload(uploaded: UploadedFile) -> FlowConfig:
    suffix = Path(uploaded.name).suffix or ".json"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded.getbuffer())
        tmp_path = Path(tmp.name)
    try:
        return load_flow_config(tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _parse_optional_json(value: str) -> tuple[Optional[Any], Optional[str]]:
    text = value.strip()
    if not text:
        return None, None
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, str(exc)


def _show_payload_errors(*errors: Optional[str]) -> None:
    for err in errors:
        if err:
            st.error(f"Formato JSON non valido: {err}")


__all__ = ["render"]


if __name__ == "__main__":  # pragma: no cover - streamlit multipage support
    from dashboard import state

    render(state.get_runtime())
