"""Runtime utilities for hosting FlowOrchestrator in the Streamlit dashboard."""
from __future__ import annotations

import asyncio
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from conductor.config import (
    FlowConfig,
    GlobalConfig,
    load_flow_config,
    load_global_config,
)
from conductor.orchestrator import FlowExecution, FlowOrchestrator, ScheduledFlow


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

    def __init__(self, *, max_history: int = 50):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="conductor-dashboard-orchestrator",
            daemon=True,
        )
        self._orchestrator: Optional[FlowOrchestrator] = None
        self._ready = threading.Event()
        self._max_history = max_history
        self._active_runs: Dict[str, _RunEntry] = {}
        self._history: List[RunSummary] = []
        self._completed: "queue.Queue[tuple[str, RunSummary]]" = queue.Queue()
        self._closed = False
        self._thread.start()
        self._ready.wait()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def register_flow(
        self,
        flow: FlowConfig | str,
        *,
        global_config: Optional[GlobalConfig | str] = None,
        name: Optional[str] = None,
        replace: bool = False,
    ) -> str:
        """Register a new flow with the orchestrator and return its name."""

        def _register() -> str:
            assert self._orchestrator is not None
            flow_cfg = self._ensure_flow_config(flow)
            global_cfg = self._ensure_global_config(global_config)
            handle = self._orchestrator.register_flow(
                flow_cfg,
                global_config=global_cfg,
                name=name,
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
        return self._summarise_execution(execution, status="completed")

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
        return {"active": active, "history": list(self._history)}

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
        self._orchestrator = FlowOrchestrator()
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

            def _done_callback(fut: asyncio.Future[FlowExecution]) -> None:
                finished_at = datetime.now(timezone.utc)
                try:
                    execution = fut.result()
                    summary = self._summarise_execution(
                        execution,
                        status="completed",
                        run_id=run_id,
                    )
                except asyncio.CancelledError:
                    duration = (finished_at - entry.started_at).total_seconds()
                    summary = RunSummary(
                        id=run_id,
                        flow_name=name,
                        status="cancelled",
                        started_at=entry.started_at,
                        finished_at=finished_at,
                        duration=duration,
                        schedule_id=entry.schedule_id,
                        payload_preview=entry.payload,
                        metadata=dict(entry.metadata),
                        error="Run cancelled",
                    )
                except Exception as exc:  # pragma: no cover - surfaced in UI
                    duration = (finished_at - entry.started_at).total_seconds()
                    summary = RunSummary(
                        id=run_id,
                        flow_name=name,
                        status="error",
                        started_at=entry.started_at,
                        finished_at=finished_at,
                        duration=duration,
                        schedule_id=entry.schedule_id,
                        payload_preview=entry.payload,
                        metadata=dict(entry.metadata),
                        error=str(exc),
                    )
                self._completed.put((run_id, summary))

            task.add_done_callback(_done_callback)
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

    def _summarise_execution(
        self,
        execution: FlowExecution,
        *,
        status: str,
        run_id: Optional[str] = None,
    ) -> RunSummary:
        finished_at = execution.finished_at
        started_at = execution.started_at
        duration = None
        if finished_at and started_at:
            duration = (finished_at - started_at).total_seconds()
        payload_preview = execution.payload
        metadata = dict(execution.metadata)
        if execution.results:
            last = execution.results[-1]
            metadata.setdefault("last_status", last.output.status)
            metadata.setdefault("last_node", last.node_id)
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
        updated = False
        while not self._completed.empty():
            run_id, summary = self._completed.get()
            self._active_runs.pop(run_id, None)
            self._history.insert(0, summary)
            if len(self._history) > self._max_history:
                self._history = self._history[: self._max_history]
            updated = True
        if updated:
            self._history.sort(key=lambda item: item.started_at, reverse=True)

    @staticmethod
    def _ensure_flow_config(flow: FlowConfig | str) -> FlowConfig:
        if isinstance(flow, FlowConfig):
            return flow
        return load_flow_config(flow)

    @staticmethod
    def _ensure_global_config(config: Optional[GlobalConfig | str]) -> GlobalConfig:
        if config is None:
            return GlobalConfig.from_mapping({})
        if isinstance(config, GlobalConfig):
            return config
        return load_global_config(config)


__all__ = ["OrchestratorRuntime", "RunSummary"]
