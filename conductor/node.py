"""Node execution primitives and executor implementations."""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from asyncio import subprocess
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from .config import GlobalConfig, NodeDefinition
from .logging_utils import get_node_logger
from . import utils


@dataclass
class NodeInput:
    """Standardised payload flowing between nodes."""

    data: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    predecessor: Optional[str] = None

    @classmethod
    def from_value(cls, value: Any, predecessor: Optional[str] = None) -> "NodeInput":
        if isinstance(value, NodeInput):
            result = NodeInput(data=value.data, metadata=dict(value.metadata), predecessor=value.predecessor)
            if predecessor is not None:
                result.predecessor = predecessor
            return result
        if isinstance(value, NodeOutput):
            return cls(data=value.data, metadata=dict(value.metadata), predecessor=predecessor)
        if isinstance(value, dict) and "data" in value and "metadata" in value:
            return cls(data=value.get("data"), metadata=dict(value.get("metadata", {})), predecessor=predecessor)
        metadata: Dict[str, Any] = {}
        if isinstance(value, dict) and "metadata" in value:
            metadata = dict(value.get("metadata", {}))
            payload = value.get("data")
        else:
            payload = value
        return cls(data=payload, metadata=metadata, predecessor=predecessor)

    def to_primitive(self) -> Dict[str, Any]:
        return {
            "data": self.data,
            "metadata": dict(self.metadata),
            "predecessor": self.predecessor,
        }


@dataclass
class NodeOutput:
    """Result produced by a node execution."""

    status: str = "success"
    data: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "NodeOutput":
        if isinstance(value, NodeOutput):
            return NodeOutput(status=value.status, data=value.data, metadata=dict(value.metadata))
        if isinstance(value, dict):
            status = str(value.get("status", "success"))
            data = value.get("data")
            metadata = dict(value.get("metadata", {}))
            for key, val in value.items():
                if key not in {"status", "data", "metadata"}:
                    metadata.setdefault(key, val)
            return cls(status=status, data=data, metadata=metadata)
        return cls(status="success", data=value, metadata={})

    def to_primitive(self) -> Dict[str, Any]:
        return {"status": self.status, "data": self.data, "metadata": dict(self.metadata)}


class NodeExecutor(Protocol):
    async def run(self, node_input: NodeInput, env: Dict[str, str]) -> NodeOutput:
        ...


class InlinePythonExecutor:
    def __init__(self, callable_path: str):
        self._callable_path = callable_path

    async def run(self, node_input: NodeInput, env: Dict[str, str]) -> NodeOutput:
        func = utils.load_callable(self._callable_path)
        with utils.scoped_env(env):
            result = func(node_input)
            if inspect.isawaitable(result):
                result = await result
        return NodeOutput.from_value(result)


def _execute_in_process(callable_path: str, node_input: Dict[str, Any], env: Dict[str, str]) -> Dict[str, Any]:  # pragma: no cover - executed in child
    node_input_obj = NodeInput.from_value(node_input)
    func = utils.load_callable(callable_path)
    with utils.scoped_env(env):
        result = func(node_input_obj)
    if inspect.isawaitable(result):  # pragma: no cover - process pool cannot await
        raise RuntimeError("Functions executed in a process pool cannot be asynchronous.")
    return NodeOutput.from_value(result).to_primitive()


class ProcessPythonExecutor:
    def __init__(self, callable_path: str, pool: ProcessPoolExecutor):
        self._callable_path = callable_path
        self._pool = pool

    async def run(self, node_input: NodeInput, env: Dict[str, str]) -> NodeOutput:
        loop = asyncio.get_running_loop()
        primitive_input = node_input.to_primitive()
        output = await loop.run_in_executor(self._pool, _execute_in_process, self._callable_path, primitive_input, env)
        return NodeOutput.from_value(output)


class DockerExecutor:
    def __init__(self, definition: NodeDefinition, global_config: GlobalConfig):
        self._definition = definition
        self._global_config = global_config

    async def run(self, node_input: NodeInput, env: Dict[str, str]) -> NodeOutput:
        if not self._definition.image:
            raise ValueError(f"Node '{self._definition.id}' requires a container image.")
        image = self._global_config.resolve_image(self._definition.image)
        command: List[str] = ["docker", "run", "--rm"]
        for key, value in env.items():
            command.extend(["-e", f"{key}={value}"])
        if self._definition.workdir:
            command.extend(["-w", self._definition.workdir])
        command.append(image)
        if self._definition.command:
            command.extend(self._definition.command)
        if self._definition.args:
            command.extend(self._definition.args)

        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        payload = json.dumps(node_input.to_primitive()).encode("utf-8")
        stdout, stderr = await process.communicate(payload)
        if process.returncode != 0:
            return NodeOutput(
                status="error",
                data={
                    "returncode": process.returncode,
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "stdout": stdout.decode("utf-8", errors="replace"),
                },
                metadata={"executor": "docker"},
            )
        if stdout:
            text = stdout.decode("utf-8", errors="replace").strip()
            if text:
                try:
                    return NodeOutput.from_value(json.loads(text))
                except json.JSONDecodeError:
                    return NodeOutput(status="success", data=text, metadata={"raw": True})
        return NodeOutput(status="success", data=None, metadata={"executor": "docker"})


class ExecutableNode:
    """Runtime wrapper responsible for executing node definitions."""

    def __init__(
        self,
        definition: NodeDefinition,
        global_config: GlobalConfig,
        process_pool: Optional[ProcessPoolExecutor] = None,
    ) -> None:
        self.definition = definition
        self.global_config = global_config
        self.process_pool = process_pool
        self.logger = get_node_logger(definition.id)
        self._executor = self._build_executor()

    def _build_executor(self) -> NodeExecutor:
        if self.definition.executor == "inline":
            if not self.definition.callable:
                raise ValueError(f"Node '{self.definition.id}' requires a callable.")
            return InlinePythonExecutor(self.definition.callable)
        if self.definition.executor == "process":
            if not self.definition.callable:
                raise ValueError(f"Node '{self.definition.id}' requires a callable.")
            if self.process_pool is None:
                raise RuntimeError("Process executor requested without an available process pool.")
            return ProcessPythonExecutor(self.definition.callable, self.process_pool)
        if self.definition.executor == "docker":
            return DockerExecutor(self.definition, self.global_config)
        raise ValueError(f"Unknown executor type '{self.definition.executor}' for node '{self.definition.id}'.")

    async def execute(self, value: Any = None, predecessor: Optional[str] = None) -> NodeOutput:
        node_input = NodeInput.from_value(value, predecessor=predecessor)
        env = utils.merge_env(self.global_config.env, self.definition.env)
        start_time = time.perf_counter()
        self.logger.info("Starting node '%s'", self.definition.id)
        try:
            if self.definition.timeout:
                result = await asyncio.wait_for(self._executor.run(node_input, env), timeout=self.definition.timeout)
            else:
                result = await self._executor.run(node_input, env)
        except asyncio.TimeoutError:
            duration = time.perf_counter() - start_time
            self.logger.error("Node '%s' timed out after %.2f seconds", self.definition.id, self.definition.timeout)
            return NodeOutput(
                status="timeout",
                data=None,
                metadata={
                    "duration": duration,
                    "node_id": self.definition.id,
                    "predecessor": predecessor,
                },
            )
        except FileNotFoundError as exc:
            duration = time.perf_counter() - start_time
            self.logger.exception("Node '%s' failed because an executable was not found", self.definition.id)
            return NodeOutput(
                status="error",
                data={"error": str(exc)},
                metadata={"duration": duration, "node_id": self.definition.id, "predecessor": predecessor},
            )
        except Exception as exc:  # pragma: no cover - depends on user code
            duration = time.perf_counter() - start_time
            self.logger.exception("Node '%s' raised an exception", self.definition.id)
            return NodeOutput(
                status="error",
                data={"error": str(exc)},
                metadata={"duration": duration, "node_id": self.definition.id, "predecessor": predecessor},
            )

        output = NodeOutput.from_value(result)
        duration = time.perf_counter() - start_time
        metadata = {
            **output.metadata,
            "duration": duration,
            "node_id": self.definition.id,
            "global_state": self.definition.with_global_state,
            "executor": self.definition.executor,
        }
        if predecessor is not None:
            metadata["predecessor"] = predecessor
        output.metadata = metadata
        self.logger.info("Node '%s' completed with status %s", self.definition.id, output.status)
        return output

