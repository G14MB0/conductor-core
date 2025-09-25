"""Interactive flow designer built with streamlit-flow."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import streamlit as st
import streamlit_flow as sf

from conductor.config import FlowConfig
from conductor.diagram import render_mermaid_diagram

from dashboard.components.mermaid import mermaid_diagram
from dashboard.services.runtime import OrchestratorRuntime
from dashboard.services.serialization import flow_config_to_dict
from dashboard.state import get_global_config_state


_METADATA_PAGE = "metadata"
_NODES_PAGE = "nodes"
_REVIEW_PAGE = "review"


def render(runtime: OrchestratorRuntime) -> None:
    st.header("Flow Designer")
    builder_state = sf.initialize("flow_builder_state", _default_state)
    _apply_import_if_present(builder_state)

    app = sf.App()

    if "flow_builder_current_page" not in st.session_state:
        st.session_state["flow_builder_current_page"] = _METADATA_PAGE
        st.session_state["flow_builder_page_args"] = ()
        st.session_state["flow_builder_page_kwargs"] = {}

    def _remember_page(page: str, *args: Any, **kwargs: Any) -> None:
        st.session_state["flow_builder_current_page"] = page
        st.session_state["flow_builder_page_args"] = tuple(args)
        st.session_state["flow_builder_page_kwargs"] = dict(kwargs)

    def _goto(page: str, *args: Any, **kwargs: Any) -> None:
        _remember_page(page, *args, **kwargs)
        app.goto(page, *args, **kwargs)

    @app.route(_METADATA_PAGE)
    def metadata_page() -> None:
        st.subheader("Metadati del flow")
        builder_state["name"] = st.text_input("Nome flow", value=builder_state.get("name", ""))
        builder_state["description"] = st.text_area(
            "Descrizione",
            value=builder_state.get("description", ""),
            height=80,
        )
        start_text = st.text_input(
            "Nodi di start (separati da virgola)",
            value=", ".join(builder_state.get("start", [])),
        )
        builder_state["start"] = [item.strip() for item in start_text.split(",") if item.strip()]
        metadata_text = st.text_area(
            "Metadata opzionali (JSON)",
            value=json.dumps(builder_state.get("metadata", {}), ensure_ascii=False, indent=2),
            height=160,
        )
        metadata, error = _parse_json_dict(metadata_text, "metadata", builder_state.get("metadata", {}))
        if error:
            st.error(error)
        else:
            builder_state["metadata"] = metadata
        if st.button("Avanti ➡️", type="primary"):
            if not builder_state["name"].strip():
                st.error("Il flow deve avere un nome.")
            elif not builder_state["start"]:
                st.error("Indicare almeno un nodo di start.")
            else:
                _goto(_NODES_PAGE)

    @app.route(_NODES_PAGE)
    def nodes_page() -> None:
        st.subheader("Nodi")
        nodes: Dict[str, Dict[str, Any]] = builder_state.setdefault("nodes", {})
        if nodes:
            st.dataframe(
                _nodes_overview(nodes),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Nessun nodo definito. Usa il form per aggiungerne uno.")

        editing = builder_state.get("editing")
        options = ["<nuovo>"] + sorted(nodes.keys())
        selected = st.selectbox(
            "Seleziona un nodo da modificare",
            options,
            index=options.index(editing) if editing and editing in options else 0,
        )
        builder_state["editing"] = selected if selected != "<nuovo>" else None
        current = nodes.get(builder_state["editing"], _empty_node())

        with st.form("node-form"):
            node_id = st.text_input("ID", value=current["id"])
            node_name = st.text_input("Nome visualizzato", value=current["name"])
            executor = st.selectbox(
                "Executor",
                ["inline", "process", "docker"],
                index={"inline": 0, "process": 1, "docker": 2}.get(current["executor"], 0),
            )
            callable_value = st.text_input(
                "Callable (modulo:function)", value=current.get("callable", "")
            )
            image_value = st.text_input("Docker image", value=current.get("image", ""))
            command_text = st.text_area(
                "Command (uno per riga)",
                value="\n".join(current.get("command", [])),
                height=80,
            )
            args_text = st.text_area(
                "Args (uno per riga)",
                value="\n".join(current.get("args", [])),
                height=80,
            )
            env_text = st.text_area(
                "Env (JSON)",
                value=json.dumps(current.get("env", {}), ensure_ascii=False, indent=2),
                height=120,
            )
            transitions_text = st.text_area(
                "Transizioni (JSON, es. {\"success\": [\"next\"]})",
                value=json.dumps(current.get("transitions", {}), ensure_ascii=False, indent=2),
                height=160,
            )
            timeout_value = st.text_input(
                "Timeout (secondi, lascia vuoto per nessuno)",
                value=str(current.get("timeout", "") or ""),
            )
            with_global_state = st.checkbox(
                "Condividi global state",
                value=current.get("with_global_state", True),
            )
            workdir_value = st.text_input("Workdir", value=current.get("workdir", ""))
            description_value = st.text_area(
                "Descrizione",
                value=current.get("description", ""),
                height=80,
            )
            saved = st.form_submit_button("Salva nodo", type="primary")
            if saved:
                if not node_id.strip():
                    st.error("L'ID del nodo è obbligatorio.")
                else:
                    env_data, env_error = _parse_json_dict(env_text, "env", current.get("env", {}))
                    transitions_data, transitions_error = _parse_json_dict(
                        transitions_text,
                        "transitions",
                        current.get("transitions", {}),
                    )
                    errors = [msg for msg in [env_error, transitions_error] if msg]
                    if errors:
                        for err in errors:
                            st.error(err)
                    else:
                        timeout_parsed: Optional[float] = None
                        if timeout_value.strip():
                            try:
                                timeout_parsed = float(timeout_value)
                            except ValueError:
                                st.error("Timeout non numerico.")
                                st.stop()
                        node_payload = {
                            "id": node_id.strip(),
                            "name": node_name.strip(),
                            "executor": executor,
                            "callable": callable_value.strip() or None,
                            "image": image_value.strip() or None,
                            "command": _split_lines(command_text),
                            "args": _split_lines(args_text),
                            "env": env_data,
                            "transitions": transitions_data,
                            "timeout": timeout_parsed,
                            "with_global_state": with_global_state,
                            "workdir": workdir_value.strip() or None,
                            "description": description_value.strip() or None,
                        }
                        previous_id = builder_state.get("editing")
                        if previous_id and previous_id != node_payload["id"]:
                            nodes.pop(previous_id, None)
                            builder_state["start"] = [
                                node_payload["id"] if item == previous_id else item
                                for item in builder_state.get("start", [])
                            ]
                        nodes[node_payload["id"]] = node_payload
                        builder_state["editing"] = node_payload["id"]
                        st.success("Nodo salvato.")
        if builder_state.get("editing"):
            if st.button("Elimina nodo", type="secondary"):
                node_id = builder_state.pop("editing")
                if node_id and node_id in nodes:
                    nodes.pop(node_id, None)
                    builder_state["start"] = [
                        item for item in builder_state.get("start", []) if item != node_id
                    ]
                    st.success("Nodo eliminato.")
        nav_cols = st.columns(2)
        if nav_cols[0].button("⬅️ Indietro"):
            _goto(_METADATA_PAGE)
        if nav_cols[1].button("Avanti ➡️", type="primary"):
            if not nodes:
                st.error("Definire almeno un nodo.")
            else:
                missing = [node_id for node_id in builder_state.get("start", []) if node_id not in nodes]
                if missing:
                    st.error(
                        "I seguenti start node non esistono più: " + ", ".join(missing)
                    )
                else:
                    _goto(_REVIEW_PAGE)

    @app.route(_REVIEW_PAGE)
    def review_page() -> None:
        st.subheader("Revisione finale")
        flow_config = _build_flow(builder_state)
        if flow_config is None:
            st.error("Completare i passaggi precedenti per generare una configurazione valida.")
            if st.button("⬅️ Torna ai nodi"):
                _goto(_NODES_PAGE)
            return
        st.success("Configurazione valida.")
        st.markdown(f"**Nome:** {flow_config.name}")
        st.markdown(f"**Start:** {', '.join(flow_config.start)}")
        if flow_config.description:
            st.markdown(flow_config.description)
        diagram = render_mermaid_diagram(flow_config, include_metadata=False, title=flow_config.name)
        mermaid_diagram(diagram, key="designer-preview", height=600)

        flow_dict = flow_config_to_dict(flow_config)
        json_payload = json.dumps(flow_dict, ensure_ascii=False, indent=2)
        st.download_button(
            "Scarica flow (JSON)",
            data=json_payload,
            file_name=f"{flow_config.name or 'flow'}.json",
            mime="application/json",
        )
        register_col, reset_col = st.columns(2)
        if register_col.button("Registra flow", type="primary"):
            global_state = get_global_config_state()
            global_config = global_state["config"]
            try:
                runtime.register_flow(flow_config, replace=True, global_config=global_config)
            except Exception as exc:  # pragma: no cover - UI feedback
                st.error(f"Registrazione fallita: {exc}")
            else:
                st.success("Flow registrato nel runtime.")
        if reset_col.button("Reset designer"):
            builder_state.clear()
            builder_state.update(_default_state())
            _goto(_METADATA_PAGE)

        if st.button("⬅️ Torna ai nodi"):
            _goto(_NODES_PAGE)

    current_page: str = st.session_state.get("flow_builder_current_page", _METADATA_PAGE)
    current_args = st.session_state.get("flow_builder_page_args", ())
    current_kwargs = st.session_state.get("flow_builder_page_kwargs", {})
    if current_page not in app._pages:
        current_page = _METADATA_PAGE
        current_args = ()
        current_kwargs = {}
        _remember_page(current_page)
    app.next(current_page, *current_args, **current_kwargs)

    app.show()


def _default_state() -> Dict[str, Any]:
    return {
        "name": "",
        "description": "",
        "metadata": {},
        "start": [],
        "nodes": {},
        "editing": None,
    }


def _apply_import_if_present(builder_state: Dict[str, Any]) -> None:
    imported = st.session_state.pop("flow_builder_import", None)
    if not imported:
        return
    try:
        nodes = {
            node["id"]: _normalize_node(node)
            for node in imported.get("nodes", [])
            if node.get("id")
        }
        builder_state.clear()
        builder_state.update(
            {
                "name": imported.get("name", ""),
                "description": imported.get("description", ""),
                "metadata": imported.get("metadata", {}),
                "start": list(imported.get("start", [])),
                "nodes": nodes,
                "editing": None,
            }
        )
        st.success("Flow importato nel designer.")
        st.session_state["flow_builder_current_page"] = _METADATA_PAGE
        st.session_state["flow_builder_page_args"] = ()
        st.session_state["flow_builder_page_kwargs"] = {}
    except Exception as exc:  # pragma: no cover - UI feedback
        st.error(f"Impossibile importare il flow: {exc}")


def _normalize_node(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(node.get("id", "")),
        "name": node.get("name") or "",
        "executor": (node.get("executor") or "inline").lower(),
        "callable": node.get("callable"),
        "image": node.get("image"),
        "command": list(node.get("command", [])),
        "args": list(node.get("args", [])),
        "env": dict(node.get("env", {})),
        "transitions": dict(node.get("transitions", {})),
        "timeout": node.get("timeout"),
        "with_global_state": bool(node.get("with_global_state", True)),
        "workdir": node.get("workdir"),
        "description": node.get("description"),
    }


def _nodes_overview(nodes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for node in nodes.values():
        rows.append(
            {
                "id": node["id"],
                "executor": node["executor"],
                "callable/image": node.get("callable") or node.get("image") or "",
                "successori": ", ".join(
                    f"{status}:{'/'.join(successors)}"
                    for status, successors in node.get("transitions", {}).items()
                ),
            }
        )
    return rows


def _empty_node() -> Dict[str, Any]:
    return {
        "id": "",
        "name": "",
        "executor": "inline",
        "callable": "",
        "image": "",
        "command": [],
        "args": [],
        "env": {},
        "transitions": {},
        "timeout": None,
        "with_global_state": True,
        "workdir": "",
        "description": "",
    }


def _split_lines(value: str) -> List[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _parse_json_dict(
    value: str,
    field: str,
    fallback: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], Optional[str]]:
    fallback = dict(fallback or {})
    text = value.strip()
    if not text:
        return fallback, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return fallback, f"Campo {field} non è un JSON valido: {exc}"
    if not isinstance(parsed, dict):
        return fallback, f"Campo {field} deve essere un oggetto JSON."
    return parsed, None


def _build_flow(state: Dict[str, Any]) -> Optional[FlowConfig]:
    try:
        nodes_payload: List[Dict[str, Any]] = []
        for node in state.get("nodes", {}).values():
            node_map = {
                "id": node["id"],
                "name": node.get("name") or None,
                "executor": node.get("executor", "inline"),
                "callable": node.get("callable"),
                "image": node.get("image"),
                "command": node.get("command", []),
                "args": node.get("args", []),
                "env": node.get("env", {}),
                "transitions": node.get("transitions", {}),
                "timeout": node.get("timeout"),
                "with_global_state": node.get("with_global_state", True),
                "workdir": node.get("workdir"),
                "description": node.get("description"),
            }
            nodes_payload.append(node_map)
        flow_mapping = {
            "name": state.get("name") or "flow",
            "description": state.get("description"),
            "start": state.get("start", []),
            "metadata": state.get("metadata", {}),
            "nodes": nodes_payload,
        }
        return FlowConfig.from_mapping(flow_mapping)
    except Exception:
        return None


__all__ = ["render"]


if __name__ == "__main__":  # pragma: no cover - streamlit multipage support
    from dashboard import state

    render(state.get_runtime())
