"""Tests for the Streamlit dashboard runtime utilities."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Dict, Generator

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest

# Ensure modules that depend on Streamlit can be imported even if the package
# is not installed in the test environment. Only the dashboard state helpers
# rely on ``st.session_state`` which is mimicked by this stub module.
if "streamlit" not in sys.modules:
    fake_streamlit = ModuleType("streamlit")
    fake_streamlit.session_state = {}

    def _noop(*_args, **_kwargs):
        return None

    fake_streamlit.warning = _noop
    fake_streamlit.error = _noop
    fake_streamlit.info = _noop
    fake_streamlit.rerun = _noop
    sys.modules["streamlit"] = fake_streamlit

from conductor.config import FlowConfig, FlowDeployment, FlowRuntimeConfig, GlobalConfig, load_flow_config

from dashboard import state
from dashboard.services import deployments
from dashboard.services.runtime import OrchestratorRuntime, RunSummary


MODULE_PATH = "tests.test_dashboard_runtime"


def start_node(node_input):
    """Increment the incoming payload and forward it."""

    value = node_input.data or 0
    return {"status": "success", "data": value + 1}


def finish_node(node_input):
    """Double the received payload to signal completion."""

    value = node_input.data or 0
    return {"status": "success", "data": value * 2}


async def slow_node(node_input):
    """Simulate a long running operation that can be cancelled."""

    await asyncio.sleep(0.2)
    return {"status": "success", "data": node_input.data}


@pytest.fixture
def runtime() -> Generator[OrchestratorRuntime, None, None]:
    instance = OrchestratorRuntime()
    try:
        yield instance
    finally:
        instance.shutdown()


@pytest.fixture
def flow_config_path(tmp_path: Path) -> str:
    config: Dict[str, object] = {
        "name": "sample-flow",
        "start": ["start"],
        "nodes": [
            {
                "id": "start",
                "callable": f"{MODULE_PATH}:start_node",
                "transitions": {"success": ["finish"]},
            },
            {
                "id": "finish",
                "callable": f"{MODULE_PATH}:finish_node",
            },
        ],
    }
    path = tmp_path / "flow.json"
    path.write_text(json.dumps(config))
    return str(path)


@pytest.fixture
def global_config_path(tmp_path: Path) -> str:
    config = {"env": {"DASHBOARD": "true"}, "metadata": {"source": "test"}}
    path = tmp_path / "global.json"
    path.write_text(json.dumps(config))
    return str(path)


def _wait_for_history(runtime: OrchestratorRuntime, run_id: str, *, timeout: float = 1.0) -> RunSummary:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = runtime.runs()
        for entry in snapshot["history"]:
            if entry.id == run_id:
                return entry
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} did not appear in history within {timeout} seconds")


def test_register_and_run_flow(runtime: OrchestratorRuntime, flow_config_path: str, global_config_path: str) -> None:
    name = runtime.register_flow(flow_config_path, global_config=global_config_path)

    assert name == "sample-flow"
    assert sorted(runtime.list_flows()) == ["sample-flow"]

    flow = runtime.get_flow_config(name)
    assert isinstance(flow, FlowConfig)
    assert set(flow.nodes) == {"start", "finish"}

    global_config = runtime.get_global_config(name)
    assert isinstance(global_config, GlobalConfig)
    assert global_config.env["DASHBOARD"] == "true"

    summary = runtime.run_flow(
        name,
        payload=2,
        metadata={"request_id": "abc"},
    )

    assert summary.status == "completed"
    assert summary.result is not None
    assert summary.result.flow_name == name
    assert summary.metadata.get("last_node") == "finish"
    runs = runtime.runs()
    assert runs["active"] == []
    history = runs["history"]
    assert len(history) == 1
    assert history[0].id == summary.id
    assert history[0].flow_name == name


def test_register_flow_from_deployment(runtime: OrchestratorRuntime, flow_config_path: str) -> None:
    flow_cfg = load_flow_config(flow_config_path)
    base_cfg = GlobalConfig.from_mapping({"env": {"LEVEL": "prod"}})
    deployment = FlowDeployment.from_components(flow_cfg, global_config=base_cfg, name="deployed-flow")

    name = runtime.register_flow(deployment)

    assert name == "deployed-flow"
    registered = runtime.get_global_config(name)
    assert registered.env["LEVEL"] == "prod"
    assert registered is not base_cfg


def test_register_flow_deployment_rejects_extra_global_config(
    runtime: OrchestratorRuntime, flow_config_path: str
) -> None:
    flow_cfg = load_flow_config(flow_config_path)
    deployment = FlowDeployment.from_components(flow_cfg)

    with pytest.raises(ValueError):
        runtime.register_flow(deployment, global_config=GlobalConfig.from_mapping({}))


def test_background_execution_tracking(runtime: OrchestratorRuntime, flow_config_path: str) -> None:
    runtime.register_flow(flow_config_path)

    pending = runtime.run_flow("sample-flow", payload=1, background=True, metadata={"source": "background"})

    assert pending.status == "running"
    snapshot = runtime.runs()
    assert any(run.id == pending.id for run in snapshot["active"])

    # Allow the asynchronous task to complete and propagate to the history queue.
    entry = _wait_for_history(runtime, pending.id)
    snapshot = runtime.runs()
    assert not snapshot["active"]
    assert entry.status == "completed"
    assert entry.metadata.get("last_node") == "finish"

    # Once the flow is idle it can be unregistered safely.
    assert runtime.unregister_flow("sample-flow") is True
    assert runtime.unregister_flow("sample-flow") is False


def test_cancel_background_run(runtime: OrchestratorRuntime) -> None:
    slow_flow = FlowConfig.from_mapping(
        {
            "name": "slow-flow",
            "start": ["slow"],
            "nodes": [
                {
                    "id": "slow",
                    "callable": f"{MODULE_PATH}:slow_node",
                }
            ],
        }
    )
    runtime.register_flow(slow_flow)

    pending = runtime.run_flow("slow-flow", background=True)
    assert pending.status == "running"

    # Give the coroutine a chance to start before issuing the cancellation.
    time.sleep(0.05)
    assert runtime.cancel_run(pending.id) is True

    entry = _wait_for_history(runtime, pending.id)
    snapshot = runtime.runs()
    assert entry.status == "cancelled"
    assert entry.error == "Run cancelled"




def test_get_deployment_returns_copy(runtime: OrchestratorRuntime, flow_config_path: str) -> None:
    runtime.register_flow(flow_config_path)

    deployment = runtime.get_deployment("sample-flow")
    assert deployment.flow.name == "sample-flow"

    # Mutare la copia non deve influenzare il runtime.
    deployment.metadata["mutation"] = "value"
    fresh = runtime.get_deployment("sample-flow")
    assert "mutation" not in fresh.metadata
def test_save_and_restore_deployment_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(deployments, "_STORAGE_ROOT", storage_root)
    monkeypatch.setattr(deployments, "_LOCAL_ROOT", storage_root / "local")
    monkeypatch.setattr(deployments, "_GIT_ROOT", storage_root / "git")
    monkeypatch.setattr(deployments, "_DEPLOYMENTS_DIR", storage_root / "deployments")

    flow_cfg = FlowConfig.from_mapping(
        {
            "name": "restored",
            "start": ["start"],
            "nodes": [
                {
                    "id": "start",
                    "callable": f"{MODULE_PATH}:start_node",
                }
            ],
        }
    )
    global_cfg = GlobalConfig.from_mapping({"env": {"MODE": "test"}})
    runtime_cfg = FlowRuntimeConfig.from_mapping(
        {
            "flow_definition": "flows/restored.json",
            "resource_locations": {
                "flows": {"type": "git", "location": "https://example.com/repo.git"},
            },
            "code_locations": {
                "library": {"type": "filesystem", "location": "/opt/nodes"},
            },
            "container_registries": {
                "dockerhub": {"url": "https://registry-1.docker.io", "token": "abc123"},
            },
            "secrets": {
                "docker-token": {"type": "docker", "value": "abc123"},
            },
            "callables": ["library"],
        }
    )
    deployment = FlowDeployment.from_components(
        flow_cfg,
        global_config=global_cfg,
        runtime_config=runtime_cfg,
        name="restored",
        metadata={"origin": "unit"},
    )

    path_file = deployments.save_deployment(deployment)
    assert path_file.exists()

    restored = deployments.load_saved_deployments()
    assert "restored" in restored
    loaded = restored["restored"]
    assert loaded.global_config.env["MODE"] == "test"
    assert loaded.runtime_config.flow_definition == "flows/restored.json"
    assert "flows" in loaded.runtime_config.resource_locations
    assert loaded.runtime_config.container_registries["dockerhub"].token == "abc123"
    assert loaded.runtime_config.secrets["docker-token"].value == "abc123"
    assert loaded.runtime_config.callables == ["library"]
    assert loaded.metadata["origin"] == "unit"

    deployments.remove_saved_deployment("restored")
    assert not path_file.exists()

def test_session_state_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_streamlit = ModuleType("streamlit_stub")
    fake_streamlit.session_state = {}
    fake_streamlit.warning = lambda *_args, **_kwargs: None
    fake_streamlit.error = lambda *_args, **_kwargs: None
    fake_streamlit.info = lambda *_args, **_kwargs: None
    monkeypatch.setattr(state, "st", fake_streamlit, raising=False)

    created_instances = []

    class DummyRuntime:
        def __init__(self):
            self.shutdown_called = False
            self.registered = []
            created_instances.append(self)

        def register_flow(self, deployment, *, replace: bool = False):
            self.registered.append((deployment, replace))
            return deployment.resolved_name()

        def shutdown(self) -> None:
            self.shutdown_called = True

    monkeypatch.setattr(state, "OrchestratorRuntime", DummyRuntime)

    restored_deployment = FlowDeployment.from_components(
        FlowConfig.from_mapping(
            {
                "name": "restored-flow",
                "start": ["start"],
                "nodes": [
                    {
                        "id": "start",
                        "callable": f"{MODULE_PATH}:start_node",
                    }
                ],
            }
        ),
        name="restored-flow",
    )

    restore_calls = []

    def fake_load_saved_deployments():
        restore_calls.append(True)
        return {"restored-flow": restored_deployment}

    monkeypatch.setattr(state, "load_saved_deployments", fake_load_saved_deployments)

    runtime_first = state.get_runtime()
    runtime_second = state.get_runtime()
    assert runtime_first is runtime_second
    assert len(created_instances) == 1
    assert runtime_first.registered == [(restored_deployment, True)]
    assert len(restore_calls) == 1

    cfg = GlobalConfig.from_mapping({"env": {"FOO": "bar"}})
    state.set_global_config(cfg, path="/tmp/config.json", dirty=False)
    global_state = state.get_global_config_state()
    assert global_state["config"] is cfg
    assert global_state["path"] == "/tmp/config.json"
    assert global_state["dirty"] is False

    state.mark_global_config_dirty()
    assert state.get_global_config_state()["dirty"] is True
    state.mark_global_config_clean()
    assert state.get_global_config_state()["dirty"] is False

    state.reset_state()
    assert runtime_first.shutdown_called is True
    assert state.get_global_config_state()["dirty"] is False

    runtime_third = state.get_runtime()
    assert runtime_third is not runtime_first
    assert len(created_instances) == 2
    assert runtime_third.registered == [(restored_deployment, True)]
    assert len(restore_calls) == 2
