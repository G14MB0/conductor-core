"""High level orchestration and scheduling helpers built on top of Conductor."""
from __future__ import annotations

import asyncio
import copy
import heapq
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Set

from ..config import FlowConfig, GlobalConfig
from ..execution import ExecutionTrace, FlowExecutor, FlowResult

UTC = timezone.utc


def _utcnow() -> datetime:
    """Return the current time in UTC with timezone information."""

    return datetime.now(tz=UTC)


class ScheduleTrigger(Protocol):
    """Protocol describing trigger strategies for scheduled flow executions."""

    def next_run(self, previous: Optional[datetime] = None) -> datetime:
        """Return the next execution time after ``previous``."""


class IntervalSchedule:
    """Trigger that fires every fixed time interval."""

    def __init__(self, interval: timedelta | float | int):
        if isinstance(interval, timedelta):
            if interval.total_seconds() <= 0:
                raise ValueError("Interval must represent a positive amount of time.")
            self.interval = interval
        else:
            if interval <= 0:
                raise ValueError("Interval must be a positive number of seconds.")
            self.interval = timedelta(seconds=float(interval))

    def next_run(self, previous: Optional[datetime] = None) -> datetime:
        base = previous or _utcnow()
        return base + self.interval


class CronSchedule:
    """Trigger that evaluates the next execution time from a cron expression."""

    def __init__(self, expression: str, *, tz: Optional[tzinfo] = None):
        self.expression = expression
        self.timezone = tz or UTC
        try:  # pragma: no cover - optional dependency
            from croniter import croniter as _croniter
        except ImportError as exc:  # pragma: no cover - handled at runtime
            raise RuntimeError(
                "Cron expressions require the 'croniter' package. Install conductor[orchestrator]."
            ) from exc
        self._croniter = _croniter

    def next_run(self, previous: Optional[datetime] = None) -> datetime:
        if previous is not None:
            base = previous.astimezone(self.timezone)
        else:
            base = datetime.now(tz=self.timezone)
        iterator = self._croniter(self.expression, base)
        next_time = iterator.get_next(datetime)
        if next_time.tzinfo is None:
            next_time = next_time.replace(tzinfo=self.timezone)
        return next_time.astimezone(UTC)


@dataclass
class FlowExecution:
    """Result of a single flow execution triggered by the orchestrator."""

    flow_name: str
    results: List[FlowResult]
    payload: Any = None
    trace: Optional[ExecutionTrace] = None
    started_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime = field(default_factory=_utcnow)
    schedule_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FlowRegistration:
    """Container for a flow configuration registered with the orchestrator."""

    name: str
    flow: FlowConfig
    global_config: GlobalConfig
    logger: logging.Logger

    def create_executor(self) -> FlowExecutor:
        return FlowExecutor(self.flow, self.global_config, logger=self.logger)


@dataclass(order=True)
class ScheduledFlow:
    """Internal representation of a scheduled flow entry."""

    next_run: datetime
    id: str = field(compare=False)
    flow_name: str = field(compare=False)
    trigger: ScheduleTrigger = field(compare=False)
    payload: Any = field(compare=False, default=None)
    payload_factory: Optional[Callable[[], Any]] = field(compare=False, default=None)
    timezone: tzinfo = field(compare=False, default=UTC)
    metadata: Dict[str, Any] = field(compare=False, default_factory=dict)
    active: bool = field(compare=False, default=True)
    last_run: Optional[datetime] = field(compare=False, default=None)

    def compute_payload(self) -> Any:
        if self.payload_factory is not None:
            return self.payload_factory()
        if isinstance(self.payload, (dict, list, set)):
            return copy.deepcopy(self.payload)
        return self.payload

    def advance(self, reference: Optional[datetime] = None) -> None:
        self.last_run = reference or _utcnow()
        self.next_run = self.trigger.next_run(self.last_run)


class FlowHandle:
    """Facade exposed to interact with a registered flow."""

    def __init__(self, orchestrator: "FlowOrchestrator", registration: FlowRegistration):
        self._orchestrator = orchestrator
        self._registration = registration

    @property
    def name(self) -> str:
        return self._registration.name

    @property
    def flow(self) -> FlowConfig:
        return self._registration.flow

    @property
    def global_config(self) -> GlobalConfig:
        return self._registration.global_config

    async def run(self, payload: Any = None, *, metadata: Optional[Dict[str, Any]] = None) -> FlowExecution:
        return await self._orchestrator.run_flow(self.name, payload=payload, metadata=metadata)

    def run_in_background(
        self,
        payload: Any = None,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> asyncio.Task[FlowExecution]:
        return self._orchestrator.run_flow_in_background(self.name, payload=payload, metadata=metadata)

    def schedule(
        self,
        *,
        interval: timedelta | float | int | None = None,
        cron: Optional[str] = None,
        payload: Any = None,
        payload_factory: Optional[Callable[[], Any]] = None,
        timezone: Optional[tzinfo] = None,
        metadata: Optional[Dict[str, Any]] = None,
        start_immediately: bool = False,
    ) -> ScheduledFlow:
        return self._orchestrator.schedule_flow(
            self.name,
            interval=interval,
            cron=cron,
            payload=payload,
            payload_factory=payload_factory,
            timezone=timezone,
            metadata=metadata,
            start_immediately=start_immediately,
        )


class FlowOrchestrator:
    """Register, execute, and schedule flows backed by :class:`FlowExecutor`."""

    def __init__(self, *, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("conductor.orchestrator")
        self._flows: Dict[str, FlowRegistration] = {}
        self._handles: Dict[str, FlowHandle] = {}
        self._active_tasks: Dict[str, Set[asyncio.Task[FlowExecution]]] = {}
        self._scheduled_heap: List[ScheduledFlow] = []
        self._scheduled_index: Dict[str, ScheduledFlow] = {}
        self._scheduler_task: Optional[asyncio.Task[None]] = None
        self._wake_event: Optional[asyncio.Event] = None
        self._running_scheduler = False

    # ------------------------------------------------------------------
    # Flow registration
    # ------------------------------------------------------------------
    def register_flow(
        self,
        flow: FlowConfig,
        global_config: Optional[GlobalConfig] = None,
        *,
        name: Optional[str] = None,
        replace: bool = False,
        logger: Optional[logging.Logger] = None,
    ) -> FlowHandle:
        flow_name = name or flow.name or "flow"
        if not replace and flow_name in self._flows:
            raise ValueError(f"Flow '{flow_name}' is already registered.")

        flow_logger = logger or self.logger.getChild(flow_name)
        registration = FlowRegistration(
            name=flow_name,
            flow=flow,
            global_config=global_config or GlobalConfig.from_mapping({}),
            logger=flow_logger,
        )
        self._flows[flow_name] = registration
        handle = FlowHandle(self, registration)
        self._handles[flow_name] = handle
        self.logger.debug("Registered flow '%s'", flow_name)
        return handle

    def unregister_flow(self, name: str) -> bool:
        if name not in self._flows:
            return False
        if name in self._active_tasks and self._active_tasks[name]:
            raise RuntimeError(f"Cannot unregister flow '{name}' while executions are active.")
        self._flows.pop(name, None)
        self._handles.pop(name, None)
        for schedule in list(self._scheduled_index.values()):
            if schedule.flow_name == name:
                self.unschedule(schedule.id)
        self.logger.debug("Unregistered flow '%s'", name)
        return True

    def get_flow(self, name: str) -> FlowHandle:
        try:
            return self._handles[name]
        except KeyError as exc:
            raise KeyError(f"Flow '{name}' is not registered.") from exc

    def list_flows(self) -> Sequence[str]:
        return tuple(self._flows.keys())

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------
    async def run_flow(
        self,
        name: str,
        payload: Any = None,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        schedule_id: Optional[str] = None,
    ) -> FlowExecution:
        registration = self._get_registration(name)
        return await self._execute_flow(registration, payload, metadata=metadata, schedule_id=schedule_id)

    def run_flow_in_background(
        self,
        name: str,
        payload: Any = None,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        schedule_id: Optional[str] = None,
    ) -> asyncio.Task[FlowExecution]:
        registration = self._get_registration(name)
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            self._execute_flow(registration, payload, metadata=metadata, schedule_id=schedule_id)
        )
        self._track_task(registration, task)
        return task

    def run_flow_sync(
        self,
        name: str,
        payload: Any = None,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FlowExecution:
        return asyncio.run(self.run_flow(name, payload=payload, metadata=metadata))

    async def wait_for(self, *names: str) -> List[FlowExecution]:
        tasks: Iterable[asyncio.Task[FlowExecution]]
        if names:
            tasks = (
                task
                for name in names
                for task in self._active_tasks.get(name, set())
            )
        else:
            tasks = (
                task
                for task_set in self._active_tasks.values()
                for task in task_set
            )
        gathered = [task for task in tasks if not task.done()]
        if not gathered:
            return []
        results = await asyncio.gather(*gathered)
        return list(results)

    async def shutdown(self, *, cancel_running: bool = False) -> None:
        await self._stop_scheduler()
        if cancel_running:
            for task_set in self._active_tasks.values():
                for task in task_set:
                    task.cancel()
        all_tasks = [task for task_set in self._active_tasks.values() for task in task_set]
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)
        self._active_tasks.clear()

    # ------------------------------------------------------------------
    # Scheduling helpers
    # ------------------------------------------------------------------
    def schedule_flow(
        self,
        name: str,
        *,
        interval: timedelta | float | int | None = None,
        cron: Optional[str] = None,
        payload: Any = None,
        payload_factory: Optional[Callable[[], Any]] = None,
        timezone: Optional[tzinfo] = None,
        metadata: Optional[Dict[str, Any]] = None,
        start_immediately: bool = False,
    ) -> ScheduledFlow:
        if interval is None and cron is None:
            raise ValueError("Either 'interval' or 'cron' must be provided to schedule a flow.")
        if interval is not None and cron is not None:
            raise ValueError("Specify only one of 'interval' or 'cron'.")
        registration = self._get_registration(name)
        tz = timezone or UTC
        trigger: ScheduleTrigger
        if cron is not None:
            trigger = CronSchedule(cron, tz=tz)
        else:
            trigger = IntervalSchedule(interval)  # type: ignore[arg-type]
        schedule_id = uuid.uuid4().hex
        first_run = _utcnow() if start_immediately else trigger.next_run()
        scheduled = ScheduledFlow(
            next_run=first_run,
            id=schedule_id,
            flow_name=registration.name,
            trigger=trigger,
            payload=payload,
            payload_factory=payload_factory,
            timezone=tz,
            metadata=dict(metadata or {}),
        )
        self._scheduled_index[schedule_id] = scheduled
        heapq.heappush(self._scheduled_heap, scheduled)
        self.logger.debug(
            "Scheduled flow '%s' (id=%s) for %s", registration.name, schedule_id, scheduled.next_run
        )
        self._ensure_scheduler()
        if self._wake_event is not None:
            self._wake_event.set()
        return scheduled

    def unschedule(self, schedule_id: str) -> bool:
        schedule = self._scheduled_index.get(schedule_id)
        if not schedule:
            return False
        schedule.active = False
        self._scheduled_index.pop(schedule_id, None)
        if self._wake_event is not None:
            self._wake_event.set()
        self.logger.debug("Unscheduling flow '%s' (id=%s)", schedule.flow_name, schedule_id)
        return True

    def list_schedules(self) -> Sequence[ScheduledFlow]:
        return tuple(schedule for schedule in self._scheduled_index.values() if schedule.active)

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------
    def _get_registration(self, name: str) -> FlowRegistration:
        try:
            return self._flows[name]
        except KeyError as exc:
            raise KeyError(f"Flow '{name}' is not registered.") from exc

    async def _execute_flow(
        self,
        registration: FlowRegistration,
        payload: Any = None,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        schedule_id: Optional[str] = None,
    ) -> FlowExecution:
        started_at = _utcnow()
        metadata = dict(metadata or {})
        async with registration.create_executor() as executor:
            results = await executor.run(initial_payload=payload)
            trace = executor.trace
        finished_at = _utcnow()
        execution = FlowExecution(
            flow_name=registration.name,
            results=results,
            payload=payload,
            trace=trace,
            started_at=started_at,
            finished_at=finished_at,
            schedule_id=schedule_id,
            metadata=metadata,
        )
        self.logger.debug(
            "Flow '%s' finished in %.3fs", registration.name, (finished_at - started_at).total_seconds()
        )
        return execution

    def _track_task(self, registration: FlowRegistration, task: asyncio.Task[FlowExecution]) -> None:
        task_set = self._active_tasks.setdefault(registration.name, set())
        task_set.add(task)

        def _cleanup(_: asyncio.Future[Any]) -> None:
            task_set.discard(task)
            if not task_set:
                self._active_tasks.pop(registration.name, None)

        task.add_done_callback(_cleanup)

    def _ensure_scheduler(self) -> None:
        if self._running_scheduler:
            return
        loop = asyncio.get_running_loop()
        self._wake_event = asyncio.Event()
        self._scheduler_task = loop.create_task(self._scheduler_loop())
        self._running_scheduler = True
        self.logger.debug("Scheduler loop started")

    async def _stop_scheduler(self) -> None:
        if not self._running_scheduler:
            return
        self._running_scheduler = False
        if self._wake_event is not None:
            self._wake_event.set()
        if self._scheduler_task is not None:
            await self._scheduler_task
        self._scheduler_task = None
        self._wake_event = None
        self.logger.debug("Scheduler loop stopped")

    async def _scheduler_loop(self) -> None:
        try:
            while self._running_scheduler:
                if not self._scheduled_heap:
                    if self._wake_event is None:
                        break
                    self._wake_event.clear()
                    try:
                        await asyncio.wait_for(self._wake_event.wait(), timeout=60.0)
                    except asyncio.TimeoutError:
                        continue
                    else:
                        continue
                schedule = self._scheduled_heap[0]
                if not schedule.active:
                    heapq.heappop(self._scheduled_heap)
                    continue
                now = _utcnow()
                delay = (schedule.next_run - now).total_seconds()
                if delay > 0:
                    if self._wake_event is None:
                        await asyncio.sleep(delay)
                        continue
                    self._wake_event.clear()
                    try:
                        await asyncio.wait_for(self._wake_event.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
                    continue
                heapq.heappop(self._scheduled_heap)
                registration = self._flows.get(schedule.flow_name)
                if registration is None:
                    schedule.active = False
                    continue
                payload = schedule.compute_payload()
                scheduled_time = schedule.next_run
                task = asyncio.create_task(
                    self._execute_flow(
                        registration,
                        payload,
                        metadata=schedule.metadata,
                        schedule_id=schedule.id,
                    )
                )
                self._track_task(registration, task)
                schedule.advance(reference=scheduled_time)
                if schedule.active:
                    heapq.heappush(self._scheduled_heap, schedule)
        finally:
            self._running_scheduler = False


__all__ = [
    "CronSchedule",
    "FlowExecution",
    "FlowHandle",
    "FlowOrchestrator",
    "IntervalSchedule",
    "ScheduledFlow",
    "ScheduleTrigger",
]
