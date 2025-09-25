"""Global settings management page."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

from conductor.config import GlobalConfig, load_global_config

from dashboard.services.git import test_git_connection
from dashboard.services.serialization import (
    global_config_to_dict,
    repository_location_to_dict,
    repository_locations_to_rows,
    rows_to_repository_locations,
)
from dashboard.state import (
    get_global_config_state,
    mark_global_config_clean,
    set_global_config,
)


def render() -> None:
    state = get_global_config_state()
    config: GlobalConfig = state["config"]
    st.header("Global config")

    _loaders()
    _editor(config)
    _save_controls(config)


def _loaders() -> None:
    st.subheader("Caricamento configurazione")
    cols = st.columns([3, 1])
    current_path = get_global_config_state().get("path") or ""
    with cols[0]:
        path_input = st.text_input(
            "Percorso file config",
            value=current_path,
            placeholder="es. examples/global.json",
        )
    with cols[1]:
        if st.button("Carica da file"):
            if not path_input:
                st.error("Indicare un percorso valido.")
            else:
                try:
                    loaded = load_global_config(path_input)
                except Exception as exc:  # pragma: no cover - UI feedback
                    st.error(f"Impossibile caricare la configurazione: {exc}")
                else:
                    set_global_config(loaded, path=path_input, dirty=False)
                    st.experimental_rerun()
    uploaded = st.file_uploader(
        "Oppure carica un file dal tuo computer",
        type=["json", "yaml", "yml", "toml"],
    )
    if uploaded is not None:
        text = uploaded.getvalue()
        try:
            decoded = text.decode("utf-8")
        except UnicodeDecodeError as exc:  # pragma: no cover - UI feedback
            st.error(f"Encoding non supportato: {exc}")
            return
        mapping: Optional[Dict[str, Any]] = None
        try:
            mapping = json.loads(decoded)
        except json.JSONDecodeError:
            try:
                import yaml

                mapping = yaml.safe_load(decoded)
            except Exception as exc:  # pragma: no cover - optional dependency
                st.error(f"Impossibile interpretare il file: {exc}")
        if mapping is not None:
            try:
                loaded = GlobalConfig.from_mapping(mapping)
            except Exception as exc:  # pragma: no cover
                st.error(f"Configurazione non valida: {exc}")
            else:
                set_global_config(loaded, path=None, dirty=True)
                st.success("Configurazione caricata in memoria. Ricordati di salvarla su file.")
                st.experimental_rerun()


def _editor(config: GlobalConfig) -> None:
    st.subheader("Modifica impostazioni")
    with st.form("global-config-form"):
        general_tab, resources_tab, code_tab, logging_tab = st.tabs(
            ["Generale", "Resource locations", "Code locations", "Remote logging"]
        )
        mapping: Dict[str, Any] = {}
        errors: List[str] = []

        general_data, general_errors = _general_section(config)
        mapping.update(general_data)
        errors.extend(general_errors)

        resources_data, resources_errors = _locations_section(
            config.resource_locations,
            table_key="resource-locations",
            empty_label="Nessuna resource location definita.",
        )
        mapping["resource_locations"] = resources_data
        errors.extend(resources_errors)

        code_data, code_errors = _locations_section(
            config.code_locations,
            table_key="code-locations",
            empty_label="Nessuna code location definita.",
        )
        mapping["code_locations"] = code_data
        errors.extend(code_errors)

        logging_data, logging_errors = _logging_section(config)
        if logging_data is not None:
            mapping["remote_logging"] = logging_data
        errors.extend(logging_errors)

        submitted = st.form_submit_button("Applica modifiche", type="primary")
        if submitted:
            if errors:
                for err in errors:
                    st.error(err)
                st.stop()
            try:
                new_config = GlobalConfig.from_mapping(mapping)
            except Exception as exc:  # pragma: no cover - UI feedback
                st.error(f"Configurazione non valida: {exc}")
            else:
                state = get_global_config_state()
                set_global_config(
                    new_config,
                    path=state.get("path"),
                    dirty=True,
                )
                st.success("Impostazioni aggiornate. Ricordati di salvare su file.")


def _general_section(config: GlobalConfig) -> Tuple[Dict[str, Any], List[str]]:
    errors: List[str] = []
    max_concurrency = st.number_input(
        "Max concurrency (0 = auto)", value=config.max_concurrency or 0, min_value=0, step=1
    )
    process_pool = st.number_input(
        "Process pool size (0 = auto)", value=config.process_pool_size or 0, min_value=0, step=1
    )
    registries_text = st.text_area(
        "Container registries (uno per riga)",
        value="\n".join(config.container_registries),
        height=80,
    )
    dependencies_text = st.text_area(
        "Dipendenze Python extra (uno per riga)",
        value="\n".join(config.dependencies),
        height=80,
    )
    env_text = st.text_area(
        "Variabili d'ambiente (JSON)",
        value=json.dumps(config.env, ensure_ascii=False, indent=2),
        height=140,
    )
    shared_state_text = st.text_area(
        "Shared state iniziale (JSON)",
        value=json.dumps(config.shared_state, ensure_ascii=False, indent=2),
        height=140,
    )

    env, env_error = _safe_json_dict(env_text, "env", config.env)
    if env_error:
        errors.append(env_error)
    shared_state, state_error = _safe_json_dict(shared_state_text, "shared_state", config.shared_state)
    if state_error:
        errors.append(state_error)

    mapping: Dict[str, Any] = {
        "container_registries": _split_lines(registries_text),
        "dependencies": _split_lines(dependencies_text),
        "env": env,
        "shared_state": shared_state,
    }
    if max_concurrency > 0:
        mapping["max_concurrency"] = max_concurrency
    if process_pool > 0:
        mapping["process_pool_size"] = process_pool
    return mapping, errors


def _locations_section(
    locations,
    *,
    table_key: str,
    empty_label: str,
) -> Tuple[Dict[str, Any], List[str]]:
    errors: List[str] = []
    rows = repository_locations_to_rows(locations)
    st.caption("Modifica o aggiungi righe per aggiornare le location.")
    edited = st.data_editor(
        rows,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        key=table_key,
    )
    normalized = _normalize_editor_rows(edited)
    if not normalized:
        st.info(empty_label)
        return {}, errors

    git_rows = [row for row in normalized if str(row.get("kind", "")).lower() == "git"]
    if git_rows:
        st.markdown("**Test connessione Git**")
        for row in git_rows:
            cols = st.columns([3, 1])
            cols[0].markdown(f"`{row['name']}` → {row['location']}")
            if cols[1].button("Test", key=f"git-test-{table_key}-{row['name']}"):
                ok, message = test_git_connection(row["location"], row.get("reference"))
                if ok:
                    st.success(message)
                else:
                    st.error(message)
    try:
        parsed = rows_to_repository_locations(normalized)
    except ValueError as exc:
        errors.append(str(exc))
        parsed = locations
    mapping = {
        name: repository_location_to_dict(location)
        for name, location in parsed.items()
    }
    return mapping, errors


def _logging_section(config: GlobalConfig) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    errors: List[str] = []
    enabled = st.checkbox(
        "Abilita remote logging",
        value=config.remote_logging is not None,
    )
    if not enabled:
        return None, errors
    current = config.remote_logging
    target = st.text_input("Endpoint", value=current.target if current else "")
    method = st.selectbox(
        "HTTP method",
        options=["POST", "PUT", "PATCH"],
        index={"POST": 0, "PUT": 1, "PATCH": 2}.get((current.method if current else "POST").upper(), 0),
    )
    headers_text = st.text_area(
        "Headers (JSON)",
        value=json.dumps(current.headers, ensure_ascii=False, indent=2) if current else "",
        height=120,
    )
    verify = st.checkbox(
        "Verifica certificato SSL",
        value=current.verify if current else True,
    )
    enabled_flag = st.checkbox(
        "Attivo",
        value=current.enabled if current else True,
    )
    headers, headers_error = _safe_json_dict(headers_text, "remote_logging.headers", current.headers if current else {})
    if headers_error:
        errors.append(headers_error)
    payload = {
        "target": target,
        "method": method,
        "headers": headers,
        "enabled": enabled_flag,
        "verify": verify,
    }
    return payload, errors


def _save_controls(config: GlobalConfig) -> None:
    state = get_global_config_state()
    dirty = state.get("dirty", False)
    if dirty:
        st.warning("Ci sono modifiche non salvate.")
    else:
        st.info("Configurazione sincronizzata con il file.")

    col1, col2 = st.columns([3, 1])
    suggested = state.get("path") or "global.generated.json"
    with col1:
        path = st.text_input("Salva su percorso", value=suggested, key="config-save-path")
    with col2:
        if st.button("Salva", type="primary"):
            try:
                _write_config_to_path(path, config)
            except Exception as exc:  # pragma: no cover - UI feedback
                st.error(f"Salvataggio fallito: {exc}")
            else:
                mark_global_config_clean()
                st.success(f"Configurazione salvata in {path}")


def _write_config_to_path(path: str, config: GlobalConfig) -> None:
    if not path:
        raise ValueError("Percorso non specificato.")
    data = global_config_to_dict(config)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = output_path.suffix.lower() or ".json"
    if suffix in {".json", ""}:
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Installare PyYAML per esportare in YAML.") from exc
        with output_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)
    elif suffix == ".toml":
        try:
            import tomli_w
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Installare tomli-w per esportare in TOML.") from exc
        with output_path.open("wb") as handle:
            tomli_w.dump(data, handle)
    else:
        raise ValueError(f"Formato non supportato: {suffix}")
    set_global_config(config, path=str(output_path), dirty=False)


def _split_lines(value: str) -> List[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _parse_json_dict(value: str, field: str) -> Dict[str, Any]:
    text = value.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Campo {field} non è un JSON valido: {exc}")
    if not isinstance(data, dict):
        raise ValueError(f"Campo {field} deve essere un oggetto JSON.")
    return data


def _safe_json_dict(value: str, field: str, fallback: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    try:
        parsed = _parse_json_dict(value, field)
    except ValueError as exc:
        return dict(fallback), str(exc)
    return parsed, None


def _normalize_editor_rows(data) -> List[Dict[str, Any]]:
    if hasattr(data, "to_dict"):
        df = data.fillna("")
        return [dict(row) for row in df.to_dict("records")]
    return [dict(row) for row in (data or [])]


__all__ = ["render"]
