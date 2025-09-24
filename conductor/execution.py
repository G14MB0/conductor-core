"""High level orchestration utilities for conductor flows."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .config import FlowConfig, GlobalConfig
from .global_state import child_initializer, get_global_state, get_shared_proxy, set_initial_state
from .logging_utils import get_node_logger
from .node import ExecutableNode, NodeInput, NodeOutput


@dataclass
class ExecutionEvent:
    """Single execution of a node within a flow run."""

    node_id: str
    status: str
    predecessor: Optional[str]
    started_at: float
    finished_at: float
    duration: float
    node_input: Optional[Dict[str, Any]]
    node_output: Dict[str, Any]
    successors: List[str]
    index: int = field(default=0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "node_id": self.node_id,
            "status": self.status,
            "predecessor": self.predecessor,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": self.duration,
            "node_input": self.node_input,
            "node_output": self.node_output,
            "successors": list(self.successors),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionEvent":
        return cls(
            node_id=str(data.get("node_id")),
            status=str(data.get("status", "")),
            predecessor=data.get("predecessor"),
            started_at=float(data.get("started_at", 0.0)),
            finished_at=float(data.get("finished_at", 0.0)),
            duration=float(data.get("duration", 0.0)),
            node_input=data.get("node_input"),
            node_output=dict(data.get("node_output") or {}),
            successors=list(data.get("successors", [])),
            index=int(data.get("index", 0)),
        )


@dataclass
class ExecutionTrace:
    """Ordered collection of node execution events."""

    flow_name: str
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    events: List[ExecutionEvent] = field(default_factory=list)

    def add_event(self, event: ExecutionEvent) -> ExecutionEvent:
        event.index = len(self.events)
        self.events.append(event)
        return event

    def mark_finished(self) -> None:
        self.finished_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flow_name": self.flow_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionTrace":
        trace = cls(flow_name=str(data.get("flow_name") or data.get("flow") or "flow"))
        trace.started_at = float(data.get("started_at", trace.started_at))
        finished_at = data.get("finished_at")
        if finished_at is not None:
            trace.finished_at = float(finished_at)
        for event_data in data.get("events", []):
            trace.add_event(ExecutionEvent.from_dict(event_data))
        return trace


@dataclass
class FlowResult:
    """Represents the terminal output of a node."""

    node_id: str
    output: NodeOutput

    def to_dict(self) -> Dict[str, Any]:
        return {"node_id": self.node_id, "output": self.output.to_primitive()}


class FlowExecutor:
    """Coordinate the execution of nodes according to the flow definition."""

    def __init__(self, flow: FlowConfig, global_config: Optional[GlobalConfig] = None, logger: Optional[logging.Logger] = None):
        self.flow = flow
        self.global_config = global_config or GlobalConfig.from_mapping({})
        self.logger = logger or logging.getLogger("conductor.flow")
        self._process_pool: Optional[ProcessPoolExecutor] = None
        self._requires_pool = any(node.executor == "process" for node in self.flow.nodes.values())
        self._nodes: Dict[str, ExecutableNode] = {}
        self.global_state = get_global_state()
        self.trace: Optional[ExecutionTrace] = None

        if self.global_config.env:
            for key, value in self.global_config.env.items():
                os.environ.setdefault(str(key), str(value))

        if self.global_config.shared_state:
            set_initial_state(self.global_config.shared_state)

        if self._requires_pool:
            self._process_pool = ProcessPoolExecutor(
                max_workers=self.global_config.process_pool_size,
                initializer=child_initializer,
                initargs=(get_shared_proxy(),),
            )

        for node_id, node in self.flow.nodes.items():
            self._nodes[node_id] = ExecutableNode(node, self.global_config, self._process_pool)

        self._concurrency = self.global_config.max_concurrency or len(self._nodes) or 1
        self.logger.debug(
            "Flow executor initialised with %s nodes (concurrency=%s)", len(self._nodes), self._concurrency
        )

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        if self._process_pool:
            self._process_pool.shutdown(wait=True)
            self._process_pool = None

    async def run(self, initial_payload: Any = None) -> List[FlowResult]:
        self.trace = ExecutionTrace(flow_name=self.flow.name)
        queue: asyncio.Queue[Tuple[Optional[str], Optional[NodeInput], Optional[str]]] = asyncio.Queue()
        for node_id in self.flow.start:
            await queue.put((node_id, NodeInput.from_value(initial_payload), None))
            self.logger.debug("Scheduled start node '%s'", node_id)

        results: List[FlowResult] = []
        worker_count = max(1, self._concurrency)
        sentinel: Tuple[Optional[str], Optional[NodeInput], Optional[str]] = (None, None, None)

        async def worker(worker_id: int) -> None:
            worker_logger = get_node_logger(f"worker.{worker_id}")
            while True:
                node_id, node_input, predecessor = await queue.get()
                if node_id is None:
                    queue.task_done()
                    worker_logger.debug("Worker %s received shutdown signal", worker_id)
                    break
                worker_logger.debug("Worker %s executing node '%s'", worker_id, node_id)
                try:
                    node = self._nodes[node_id]
                except KeyError:
                    self.logger.error("Received task for unknown node '%s'", node_id)
                    queue.task_done()
                    continue
                started_wall = time.time()
                started_perf = time.perf_counter()
                try:
                    output = await node.execute(node_input, predecessor)
                except Exception as exc:  # pragma: no cover - node execution already handles errors
                    self.logger.exception("Unhandled exception while executing node '%s'", node_id)
                    output = NodeOutput(status="error", data={"error": str(exc)})
                finished_wall = time.time()
                next_nodes = self.flow.next_nodes(node_id, output.status)
                if not next_nodes:
                    results.append(FlowResult(node_id=node_id, output=output))
                    worker_logger.debug("Node '%s' reached terminal state", node_id)
                else:
                    for successor in next_nodes:
                        await queue.put((successor, NodeInput.from_value(output, predecessor=node_id), node_id))
                        worker_logger.debug(
                            "Node '%s' scheduled successor '%s' due to status '%s'", node_id, successor, output.status
                        )

                if self.trace is not None:
                    primitive_input = node_input.to_primitive() if node_input else None
                    primitive_output = output.to_primitive()
                    duration = output.metadata.get("duration") if output.metadata else None
                    if duration is None:
                        duration = time.perf_counter() - started_perf
                    event = ExecutionEvent(
                        node_id=node_id,
                        status=output.status,
                        predecessor=predecessor,
                        started_at=started_wall,
                        finished_at=finished_wall,
                        duration=float(duration),
                        node_input=primitive_input,
                        node_output=primitive_output,
                        successors=list(next_nodes),
                    )
                    self.trace.add_event(event)

                queue.task_done()

        workers = [asyncio.create_task(worker(index)) for index in range(worker_count)]
        await queue.join()
        for _ in workers:
            await queue.put(sentinel)
        await asyncio.gather(*workers)
        if self.trace is not None:
            self.trace.mark_finished()
        return results

    # Context manager helpers -------------------------------------------------
    def __enter__(self) -> "FlowExecutor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # pragma: no cover - thin wrapper
        self.shutdown()

    async def __aenter__(self) -> "FlowExecutor":  # pragma: no cover - convenience
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.shutdown()

