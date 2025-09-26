"""Runtime utilities for hosting FlowOrchestrator in the Streamlit dashboard."""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from conductor.config import (
    FlowConfig,
    FlowDeployment,
    GlobalConfig,
    load_flow_config,
    load_global_config,
)
from conductor.logging_utils import configure_logging
from conductor.orchestrator import FlowExecution, FlowOrchestrator, ScheduledFlow


from dashboard.services.container_logs import (
    ContainerLogSnapshot,
    collect_container_logs,
)
from dashboard.services.logs import LogEntry, install_dashboard_log_handler

LOGGER = logging.getLogger(__name__)
_DEFAULT_STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"
_HISTORY_FILENAME = "run_history.json"


def _ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class RunSummary:
    """Lightweight view of an execution for UI purposes."""

    id: str
    flow_name: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    duration: Optional[float]
    schedule_id: Optional[str]
    payload_preview: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    result: Optional[FlowExecution] = None


@dataclass
class _RunEntry:
    id: str
    flow_name: str
    task: asyncio.Task[FlowExecution]
    started_at: datetime
    payload: Any
    metadata: Dict[str, Any]
    schedule_id: Optional[str] = None


class OrchestratorRuntime:
    """Background runner that exposes FlowOrchestrator operations to Streamlit."""

    def __init__(self, *, max_history: int = 50, storage_dir: Optional[Path] = None):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="conductor-dashboard-orchestrator",
            daemon=True,
        )
        self._orchestrator: Optional[FlowOrchestrator] = None
        self._ready = threading.Event()
        self._max_history = max_history
        base_dir = Path(storage_dir).expanduser() if storage_dir is not None else _DEFAULT_STORAGE_DIR
        self._storage_dir = _ensure_directory(base_dir)
        self._history_file = self._storage_dir / _HISTORY_FILENAME
        self._history_lock = threading.Lock()
        self._active_runs: Dict[str, _RunEntry] = {}
        self._history: List[RunSummary] = []
        self._completed: "queue.Queue[tuple[str, RunSummary]]" = queue.Queue()
        self._log_buffer = install_dashboard_log_handler()
        self._closed = False
        self._load_persisted_history()
        self._thread.start()
        self._ready.wait()



    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def register_flow(
        self,
        flow: FlowConfig | FlowDeployment | str,
        *,
        global_config: Optional[GlobalConfig | str] = None,
        name: Optional[str] = None,
        replace: bool = False,
    ) -> str:
        """Register a new flow deployment with the orchestrator and return its name."""

        def _register() -> str:
            assert self._orchestrator is not None
            deployment = self._normalize_deployment(
                flow,
                global_config=global_config,
                name=name,
            )
            handle = self._orchestrator.register_flow(
                deployment,
                replace=replace,
            )
            return handle.name

        return self._call_in_loop(_register)


    def unregister_flow(self, name: str) -> bool:
        """Remove a registered flow if it has no active executions."""

        def _unregister() -> bool:
            assert self._orchestrator is not None
            return self._orchestrator.unregister_flow(name)

        return self._call_in_loop(_unregister)

    def list_flows(self) -> List[str]:
        def _list() -> List[str]:
            assert self._orchestrator is not None
            return list(self._orchestrator.list_flows())

        return self._call_in_loop(_list)

    def get_flow_config(self, name: str) -> FlowConfig:
        def _get() -> FlowConfig:
            assert self._orchestrator is not None
            return self._orchestrator.get_flow(name).flow

        return self._call_in_loop(_get)

    def get_global_config(self, name: str) -> GlobalConfig:
        def _get() -> GlobalConfig:
            assert self._orchestrator is not None
            return self._orchestrator.get_flow(name).global_config

        return self._call_in_loop(_get)

    def get_deployment(self, name: str) -> FlowDeployment:
        def _get() -> FlowDeployment:
            assert self._orchestrator is not None
            handle = self._orchestrator.get_flow(name)
            return handle.deployment.normalized()

        return self._call_in_loop(_get)


    def run_flow(
        self,
        name: str,
        *,
        payload: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
        background: bool = False,
        schedule_id: Optional[str] = None,
    ) -> RunSummary:
        """Execute a flow immediately."""

        if background:
            return self._start_background_run(
                name,
                payload=payload,
                metadata=metadata,
                schedule_id=schedule_id,
            )
        execution = self._run_coroutine(
            self._run_flow_once(name, payload, metadata, schedule_id)
        )
        summary = self._summarise_execution(
            execution,
            run_id=execution.id,
            default_status="completed",
        )
        return summary

    def schedule_flow(
        self,
        name: str,
        *,
        interval: Optional[float] = None,
        cron: Optional[str] = None,
        payload: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
        timezone: Optional[Any] = None,
        start_immediately: bool = False,
    ) -> ScheduledFlow:
        def _schedule() -> ScheduledFlow:
            assert self._orchestrator is not None
            handle = self._orchestrator.get_flow(name)
            return handle.schedule(
                interval=interval,
                cron=cron,
                payload=payload,
                metadata=metadata,
                timezone=timezone,
                start_immediately=start_immediately,
            )

        return self._call_in_loop(_schedule)

    def list_schedules(self) -> List[ScheduledFlow]:
        def _collect() -> List[ScheduledFlow]:
            assert self._orchestrator is not None
            return list(self._orchestrator.list_schedules())

        return self._call_in_loop(_collect)

    def cancel_schedule(self, schedule_id: str) -> bool:
        def _cancel() -> bool:
            assert self._orchestrator is not None
            return self._orchestrator.unschedule(schedule_id)

        return self._call_in_loop(_cancel)

    def cancel_run(self, run_id: str) -> bool:
        async def _cancel() -> bool:
            entry = self._active_runs.get(run_id)
            if not entry:
                return False
            entry.task.cancel()
            return True

        return self._run_coroutine(_cancel())


    def runs(self) -> Dict[str, List[RunSummary]]:
        """Return snapshots of active and completed runs."""

        active = self._run_coroutine(self._snapshot_active())
        self._drain_completed()
        with self._history_lock:
            history_snapshot = list(self._history)
        return {"active": active, "history": history_snapshot}

    def logs(self, *, minimum_level: Optional[str] = None) -> List[LogEntry]:
        """Return captured log entries, optionally filtered by level name."""

        return self._log_buffer.snapshot(level=minimum_level)

    def container_logs(self, *, tail: int = 200) -> List[ContainerLogSnapshot]:
        """Collect recent log lines from the configured Docker containers."""

        return collect_container_logs(tail=tail)

    def clear_logs(self) -> None:
        """Clear the in-memory log buffer."""

        self._log_buffer.clear()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True

        async def _close() -> None:
            if self._orchestrator is not None:
                await self._orchestrator.shutdown(cancel_running=True)

        if self._loop.is_running():
            try:
                self._run_coroutine(_close())
            except RuntimeError:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._orchestrator = FlowOrchestrator(
            on_execution_start=self._handle_execution_start,
            on_execution_success=self._handle_execution_success,
            on_execution_error=self._handle_execution_error,
            on_execution_cancelled=self._handle_execution_cancelled,
        )
        self._ready.set()
        self._loop.run_forever()
        pending = asyncio.all_tasks(loop=self._loop)
        for task in pending:
            task.cancel()
        try:
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        finally:
            self._loop.close()

    def _call_in_loop(self, func: Callable[[], Any]) -> Any:
        async def _runner() -> Any:
            return func()

        return self._run_coroutine(_runner())

    def _run_coroutine(self, awaitable: Awaitable[Any]) -> Any:
        future = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
        return future.result()

    def _start_background_run(
        self,
        name: str,
        *,
        payload: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
        schedule_id: Optional[str] = None,
    ) -> RunSummary:
        run_id = uuid.uuid4().hex

        async def _launch() -> RunSummary:
            assert self._orchestrator is not None
            handle = self._orchestrator.get_flow(name)
            task = handle.run_in_background(
                payload=payload,
                metadata=metadata,
                schedule_id=schedule_id,
                run_id=run_id,
            )
            entry = _RunEntry(
                id=run_id,
                flow_name=name,
                task=task,
                started_at=datetime.now(timezone.utc),
                payload=payload,
                metadata=dict(metadata or {}),
                schedule_id=schedule_id,
            )
            self._active_runs[run_id] = entry
            return RunSummary(
                id=run_id,
                flow_name=name,
                status="running",
                started_at=entry.started_at,
                finished_at=None,
                duration=None,
                schedule_id=schedule_id,
                payload_preview=payload,
                metadata=dict(metadata or {}),
            )

        return self._run_coroutine(_launch())

    async def _run_flow_once(
        self,
        name: str,
        payload: Any,
        metadata: Optional[Dict[str, Any]],
        schedule_id: Optional[str],
    ) -> FlowExecution:
        assert self._orchestrator is not None
        return await self._orchestrator.run_flow(
            name,
            payload=payload,
            metadata=metadata,
            schedule_id=schedule_id,
        )

    def _handle_execution_start(
        self,
        run_id: str,
        flow_name: str,
        task: Optional[asyncio.Task[FlowExecution]],
        payload: Any,
        metadata: Dict[str, Any],
        schedule_id: Optional[str],
        started_at: datetime,
    ) -> None:
        loop_task = task or asyncio.current_task()
        if loop_task is None:
            return
        metadata_copy = dict(metadata or {})
        entry = self._active_runs.get(run_id)
        if entry is None:
            entry = _RunEntry(
                id=run_id,
                flow_name=flow_name,
                task=loop_task,
                started_at=started_at,
                payload=payload,
                metadata=metadata_copy,
                schedule_id=schedule_id,
            )
            self._active_runs[run_id] = entry
        else:
            entry.task = loop_task
            entry.started_at = started_at
            entry.payload = payload
            entry.metadata = metadata_copy
            entry.schedule_id = schedule_id

    def _handle_execution_success(self, execution: FlowExecution) -> None:
        summary = self._summarise_execution(
            execution,
            run_id=execution.id,
            default_status="completed",
        )
        self._active_runs.pop(execution.id, None)
        self._completed.put((execution.id, summary))

    def _handle_execution_error(
        self,
        run_id: str,
        flow_name: str,
        payload: Any,
        metadata: Dict[str, Any],
        schedule_id: Optional[str],
        started_at: datetime,
        finished_at: datetime,
        error: BaseException,
    ) -> None:
        metadata_copy = dict(metadata or {})
        duration = (finished_at - started_at).total_seconds()
        summary = RunSummary(
            id=run_id,
            flow_name=flow_name,
            status="error",
            started_at=started_at,
            finished_at=finished_at,
            duration=duration,
            schedule_id=schedule_id,
            payload_preview=payload,
            metadata=metadata_copy,
            error=str(error),
        )
        self._active_runs.pop(run_id, None)
        self._completed.put((run_id, summary))

    def _handle_execution_cancelled(
        self,
        run_id: str,
        flow_name: str,
        payload: Any,
        metadata: Dict[str, Any],
        schedule_id: Optional[str],
        started_at: datetime,
        finished_at: datetime,
    ) -> None:
        metadata_copy = dict(metadata or {})
        duration = (finished_at - started_at).total_seconds()
        summary = RunSummary(
            id=run_id,
            flow_name=flow_name,
            status="cancelled",
            started_at=started_at,
            finished_at=finished_at,
            duration=duration,
            schedule_id=schedule_id,
            payload_preview=payload,
            metadata=metadata_copy,
            error="Run cancelled",
        )
        self._active_runs.pop(run_id, None)
        self._completed.put((run_id, summary))

    def _summarise_execution(
        self,
        execution: FlowExecution,
        *,
        run_id: Optional[str] = None,
        default_status: str = "completed",
    ) -> RunSummary:
        finished_at = execution.finished_at
        started_at = execution.started_at
        duration = None
        if finished_at and started_at:
            duration = (finished_at - started_at).total_seconds()
        payload_preview = execution.payload
        metadata = dict(execution.metadata)
        last_output = None
        if execution.results:
            last = execution.results[-1]
            last_output = last.output
            metadata.setdefault("last_status", last.output.status)
            metadata.setdefault("last_node", last.node_id)
        computed_status = metadata.get("last_status")
        if not computed_status and last_output is not None:
            computed_status = last_output.status
        normalized = str(computed_status or "").lower()
        status = default_status
        if normalized in {"error", "failed", "failure"}:
            status = "error"
        elif normalized in {"cancelled", "canceled"}:
            status = "cancelled"
        error_message = metadata.get("error")
        if (
            status == "error"
            and error_message is None
            and last_output is not None
            and isinstance(getattr(last_output, "status", ""), str)
        ):
            data = getattr(last_output, "data", None)
            if isinstance(data, dict):
                error_message = str(data.get("error") or data)
            elif data not in (None, ""):
                error_message = str(data)
            else:
                error_message = "Flow execution ended with status 'error'."
        return RunSummary(
            id=run_id or uuid.uuid4().hex,
            flow_name=execution.flow_name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration=duration,
            schedule_id=execution.schedule_id,
            payload_preview=payload_preview,
            metadata=metadata,
            error=error_message,
            result=execution,
        )

    async def _snapshot_active(self) -> List[RunSummary]:
        snapshots: List[RunSummary] = []
        for entry in self._active_runs.values():
            now = datetime.now(timezone.utc)
            duration = (now - entry.started_at).total_seconds()
            snapshots.append(
                RunSummary(
                    id=entry.id,
                    flow_name=entry.flow_name,
                    status="running",
                    started_at=entry.started_at,
                    finished_at=None,
                    duration=duration,
                    schedule_id=entry.schedule_id,
                    payload_preview=entry.payload,
                    metadata=dict(entry.metadata),
                )
            )
        return snapshots

    def _drain_completed(self) -> None:
        while not self._completed.empty():
            run_id, summary = self._completed.get()
            self._active_runs.pop(run_id, None)
            self._record_summary(summary)

    def _record_summary(self, summary: RunSummary) -> None:
        with self._history_lock:
            self._history.insert(0, summary)
            self._history.sort(key=lambda item: item.started_at, reverse=True)
            if len(self._history) > self._max_history:
                self._history = self._history[: self._max_history]
            snapshot = list(self._history)
        self._persist_history(snapshot)

    def _persist_history(self, history: List[RunSummary]) -> None:
        try:
            data = [self._summary_to_dict(item) for item in history]
            _ensure_directory(self._history_file.parent)
            tmp_path = self._history_file.with_suffix('.tmp')
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp_path.replace(self._history_file)
        except Exception as exc:  # pragma: no cover - best effort persistence
            LOGGER.warning("Failed to persist run history to %s: %s", self._history_file, exc)


    def _load_persisted_history(self) -> None:
        if not self._history_file.exists():
            return
        try:
            raw = json.loads(self._history_file.read_text(encoding='utf-8'))
        except Exception as exc:  # pragma: no cover - best effort persistence
            LOGGER.warning("Failed to load run history from %s: %s", self._history_file, exc)
            return
        records: List[RunSummary] = []
        for entry in raw:
            if isinstance(entry, Mapping):
                try:
                    records.append(self._summary_from_dict(entry))
                except Exception as exc:  # pragma: no cover - defensive parsing
                    LOGGER.debug("Ignored invalid run history entry: %s", exc)
        if not records:
            return
        records.sort(key=lambda item: item.started_at, reverse=True)
        with self._history_lock:
            self._history = records[: self._max_history]


    @staticmethod
    def _summary_to_dict(summary: RunSummary) -> Dict[str, Any]:
        return {
            'id': summary.id,
            'flow_name': summary.flow_name,
            'status': summary.status,
            'started_at': OrchestratorRuntime._serialize_datetime(summary.started_at),
            'finished_at': OrchestratorRuntime._serialize_datetime(summary.finished_at),
            'duration': summary.duration,
            'schedule_id': summary.schedule_id,
            'payload_preview': OrchestratorRuntime._jsonify(summary.payload_preview),
            'metadata': OrchestratorRuntime._jsonify(summary.metadata),
            'error': summary.error,
        }


    @staticmethod
    def _summary_from_dict(data: Mapping[str, Any]) -> RunSummary:
        started_at = OrchestratorRuntime._parse_datetime(data.get('started_at'))
        finished_at = OrchestratorRuntime._parse_datetime(data.get('finished_at'))
        duration_value = data.get('duration')
        try:
            duration = float(duration_value) if duration_value is not None else None
        except (TypeError, ValueError):
            duration = None
        metadata_raw = data.get('metadata') or {}
        metadata = {str(key): value for key, value in metadata_raw.items()} if isinstance(metadata_raw, Mapping) else {}
        payload = data.get('payload_preview')
        schedule_id = data.get('schedule_id')
        if schedule_id is not None:
            schedule_id = str(schedule_id)
        error_value = data.get('error')
        error = str(error_value) if error_value is not None else None
        return RunSummary(
            id=str(data.get('id') or uuid.uuid4().hex),
            flow_name=str(data.get('flow_name') or 'flow'),
            status=str(data.get('status') or 'unknown'),
            started_at=started_at or datetime.now(timezone.utc),
            finished_at=finished_at,
            duration=duration,
            schedule_id=schedule_id,
            payload_preview=payload,
            metadata=metadata,
            error=error,
            result=None,
        )


    @staticmethod
    def _serialize_datetime(value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()


    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        return None


    @staticmethod
    def _jsonify(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, datetime):
            return OrchestratorRuntime._serialize_datetime(value)
        if isinstance(value, Mapping):
            return {str(key): OrchestratorRuntime._jsonify(val) for key, val in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [OrchestratorRuntime._jsonify(item) for item in value]
        return repr(value)

    @staticmethod
    def _normalize_deployment(

        flow: FlowConfig | FlowDeployment | str,
        *,
        global_config: Optional[GlobalConfig | str],
        name: Optional[str],
    ) -> FlowDeployment:
        if isinstance(flow, FlowDeployment):
            if global_config is not None:
                raise ValueError("global_config cannot be provided when a FlowDeployment is supplied.")
            return flow.normalized(name=name)
        flow_cfg = flow if isinstance(flow, FlowConfig) else load_flow_config(flow)
        if isinstance(global_config, GlobalConfig):
            global_cfg = global_config
        elif isinstance(global_config, str):
            global_cfg = load_global_config(global_config)
        elif global_config is None:
            global_cfg = GlobalConfig.from_mapping({})
        else:
            raise TypeError("global_config must be a GlobalConfig instance, a path, or None.")
        return FlowDeployment.from_components(
            flow_cfg,
            global_config=global_cfg,
            name=name,
        )


__all__ = ["OrchestratorRuntime", "RunSummary"]
