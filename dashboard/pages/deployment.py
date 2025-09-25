"""Flow deployment management page."""
from __future__ import annotations

from typing import Dict, Optional

import streamlit as st

from conductor.config import FlowDeployment, GlobalConfig

from dashboard import state
from dashboard.services import deployments
from dashboard.services.deployments import DeploymentResult, GitRepositorySnapshot
from dashboard.services.runtime import OrchestratorRuntime


_LOCAL_STATE_KEY = "deployment_local_state"
_GIT_STATE_KEY = "deployment_git_state"


def render(runtime: OrchestratorRuntime) -> None:
    """Render the deployment workflow page."""

    st.header("Deployment dei flow")
    st.caption(
        "Configura e registra nuovi flow scegliendo le sorgenti dei file di configurazione"
        " e delle risorse di codice per ciascun deployment."
    )

    base_config: GlobalConfig = state.get_global_config_state()["config"]

    local_tab, git_tab = st.tabs(["File locali", "Repository Git"])
    with local_tab:
        _render_local_panel(runtime, base_config)
    with git_tab:
        _render_git_panel(runtime, base_config)

    st.divider()
    _render_registered_flows(runtime)


# ---------------------------------------------------------------------------
# Local uploads
# ---------------------------------------------------------------------------

def _render_local_panel(runtime: OrchestratorRuntime, base_config: GlobalConfig) -> None:
    st.subheader("Configurazione da file locali")
    st.write(
        "Carica i file necessari (flow config obbligatorio, global config e codice opzionali)."
        " I file vengono salvati sul server per generare il deployment dedicato."
    )

    state_bucket = st.session_state.setdefault(_LOCAL_STATE_KEY, {})

    with st.form("local-deployment-form", clear_on_submit=False):
        flow_file = st.file_uploader(
            "Configurazione del flow", type=["json", "yaml", "yml", "toml"], key="local-flow-upload"
        )
        global_file = st.file_uploader(
            "Configurazione globale (opzionale)", type=["json", "yaml", "yml", "toml"], key="local-global-upload"
        )
        code_file = st.file_uploader(
            "Pacchetto codice/resources (ZIP opzionale)", type=["zip"], key="local-code-upload"
        )
        flow_name = st.text_input(
            "Nome flow (opzionale, sovrascrive quello definito nel file)",
            key="local-flow-name",
            value=state_bucket.get("flow_name", ""),
        )
        replace = st.checkbox(
            "Sostituisci se il flow esiste gi�",
            value=state_bucket.get("replace", False),
            key="local-replace",
        )
        submitted = st.form_submit_button("Registra flow locale", type="primary")

    state_bucket["flow_name"] = flow_name
    state_bucket["replace"] = replace

    if not submitted:
        return

    if flow_file is None:
        st.error("Carica almeno il file di configurazione del flow.")
        return

    flow_bytes = flow_file.getvalue()
    global_bytes = global_file.getvalue() if global_file is not None else None
    code_bytes = code_file.getvalue() if code_file is not None else None

    try:
        result = deployments.prepare_local_deployment(
            flow_payload=flow_bytes,
            flow_filename=flow_file.name,
            base_config=base_config,
            flow_name=flow_name.strip() or None,
            global_payload=global_bytes,
            global_filename=global_file.name if global_file is not None else None,
            code_archive=code_bytes,
            code_filename=code_file.name if code_file is not None else None,
        )
        registered = runtime.register_flow(result.deployment, replace=replace)
    except Exception as exc:  # pragma: no cover - UI feedback
        st.error(f"Registrazione fallita: {exc}")
        return

    st.success(f"Flow '{registered}' registrato correttamente (origine locale).")
    _render_metadata(result.metadata)


# ---------------------------------------------------------------------------
# Git integrations
# ---------------------------------------------------------------------------

def _render_git_panel(runtime: OrchestratorRuntime, base_config: GlobalConfig) -> None:
    st.subheader("Configurazione da repository Git")
    st.write(
        "Collega un repository per selezionare i file di configurazione e le cartelle di codice"
        " direttamente dalla sorgente controllata. � possibile utilizzare branch, tag o commit."
    )

    git_state: Dict[str, object] = st.session_state.setdefault(_GIT_STATE_KEY, {})

    with st.form("git-connect-form"):
        repo_url = st.text_input("URL repository", key="git-url")
        reference = st.text_input("Branch / tag / commit (opzionale)", key="git-reference")
        token = st.text_input(
            "Token/Password (opzionale, usato solo per il clone)",
            key="git-token",
            type="password",
        )
        connect = st.form_submit_button("Collega repository", type="primary")

    if connect:
        if not repo_url.strip():
            st.error("Indica l'URL del repository.")
        else:
            try:
                snapshot = deployments.prime_git_repository(
                    repo_url.strip(), reference=reference.strip() or None, token=token.strip() or None
                )
            except Exception as exc:  # pragma: no cover - UI feedback
                st.error(f"Impossibile collegare il repository: {exc}")
            else:
                git_state["snapshot"] = snapshot
                st.success(
                    f"Repository collegato. Commit attuale: `{snapshot.commit}`"
                    + (f" (richiesto {snapshot.requested_reference})" if snapshot.requested_reference else "")
                )

    snapshot: Optional[GitRepositorySnapshot] = git_state.get("snapshot")  # type: ignore[assignment]

    if snapshot is None:
        st.info("Collega un repository per continuare.")
        return

    st.markdown(
        f"**Repo:** `{snapshot.repo_url}`  �  **Commit:** `{snapshot.commit}`"
        + (
            f"  �  **Riferimento richiesto:** `{snapshot.requested_reference}`"
            if snapshot.requested_reference
            else ""
        )
    )

    if not snapshot.config_candidates:
        st.warning("Nel repository non sono stati trovati file JSON/YAML/TOML di configurazione.")
        return

    flow_path = st.selectbox(
        "Seleziona la configurazione del flow",
        snapshot.config_candidates,
        key="git-flow-path",
    )

    global_options = ["<Usa configurazione base>"] + snapshot.config_candidates
    global_choice = st.selectbox(
        "Configurazione globale",
        global_options,
        key="git-global-path",
    )
    global_path = None if global_choice.startswith("<") else global_choice

    code_paths = st.multiselect(
        "Cartelle di codice da associare",
        snapshot.directories,
        default=[],
        key="git-code-paths",
        help="Puoi selezionare pi� cartelle; saranno registrate come code locations nel deployment.",
    )

    flow_name = st.text_input(
        "Nome flow (opzionale)",
        value=git_state.get("flow_name", ""),
        key="git-flow-name",
    )
    replace = st.checkbox(
        "Sostituisci se il flow esiste gi�",
        value=git_state.get("replace", False),
        key="git-replace",
    )

    col_actions = st.columns(2)
    register = col_actions[0].button("Registra flow da Git", type="primary", key="git-register")
    if col_actions[1].button("Scollega repository", key="git-reset"):
        git_state.pop("snapshot", None)
        st.rerun()

    git_state["flow_name"] = flow_name
    git_state["replace"] = replace

    if not register:
        return

    try:
        result = deployments.build_deployment_from_git(
            snapshot,
            flow_path=flow_path,
            base_config=base_config,
            flow_name=flow_name.strip() or None,
            global_config_path=global_path,
            code_paths=code_paths,
        )
        registered = runtime.register_flow(result.deployment, replace=replace)
    except Exception as exc:  # pragma: no cover - UI feedback
        st.error(f"Registrazione fallita: {exc}")
        return

    st.success(
        f"Flow '{registered}' registrato correttamente dal repository `{snapshot.repo_url}`."  # noqa: E501
    )
    _render_metadata(result.metadata)


# ---------------------------------------------------------------------------
# Registered flows overview
# ---------------------------------------------------------------------------

def _render_registered_flows(runtime: OrchestratorRuntime) -> None:
    st.subheader("Flow registrati")
    flow_names = runtime.list_flows()
    if not flow_names:
        st.info("Nessun flow registrato al momento.")
        return

    for name in flow_names:
        flow_config = runtime.get_flow_config(name)
        global_config = runtime.get_global_config(name)
        try:
            deployment = runtime.get_deployment(name)
        except Exception:
            deployment = None
        key_suffix = str(abs(hash(name)))
        with st.expander(f"{name} ({len(flow_config.nodes)} nodi)"):
            st.markdown(f"**Start nodes:** {', '.join(flow_config.start)}")
            if flow_config.description:
                st.markdown(flow_config.description)
            if deployment and deployment.metadata:
                st.caption("Metadati del deployment")
                _render_metadata(deployment.metadata)
            code_locations = ", ".join(global_config.code_locations.keys()) or "Nessuna"
            st.markdown(f"**Code locations:** {code_locations}")
            if st.button("Deregistra flow", key=f"remove-{key_suffix}"):
                try:
                    runtime.unregister_flow(name)
                except Exception as exc:  # pragma: no cover - UI feedback
                    st.error(f"Impossibile deregistrare il flow: {exc}")
                else:
                    st.success(f"Flow '{name}' rimosso.")
                    st.rerun()


def _render_metadata(metadata: Dict[str, Optional[str]]) -> None:
    cleaned = {k: v for k, v in metadata.items() if v not in (None, "", "None")}
    if not cleaned:
        return
    st.json(cleaned)


__all__ = ["render"]


if __name__ == "__main__":  # pragma: no cover - support module execution
    from dashboard import state as _state

    render(_state.get_runtime())

