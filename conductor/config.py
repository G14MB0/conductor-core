"""Configuration models and helpers for conductor flows."""
from __future__ import annotations

"""Configuration models and helpers for conductor flows."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional
import json

try:  # pragma: no cover - optional import
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - Python <3.11
    tomllib = None  # type: ignore[assignment]

try:  # pragma: no cover - optional import
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml is optional
    yaml = None  # type: ignore[assignment]


@dataclass
class RemoteLoggingConfig:
    """Settings describing the remote logging target."""

    target: str
    method: str = "POST"
    headers: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    verify: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "RemoteLoggingConfig | None":
        if not data:
            return None
        if isinstance(data, Mapping):
            target = data.get("target") or data.get("url")
            if not target:
                raise ValueError("Remote logging configuration requires a 'target' or 'url'.")
            method = str(data.get("method", "POST")).upper()
            headers = dict(data.get("headers", {}))
            enabled = bool(data.get("enabled", True))
            verify = bool(data.get("verify", True))
            return cls(target=target, method=method, headers=headers, enabled=enabled, verify=verify)
        raise TypeError("Remote logging configuration must be a mapping.")


@dataclass
class RepositoryLocation:
    """Description of a repository that stores resources or code."""

    name: str
    kind: str
    location: str
    reference: Optional[str] = None
    subpath: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, name: str, data: Mapping[str, Any]) -> "RepositoryLocation":
        if not isinstance(data, Mapping):
            raise TypeError(f"Repository location '{name}' must be a mapping.")
        kind = str(data.get("type") or data.get("kind") or "filesystem").lower()
        allowed = {"filesystem", "http", "git"}
        if kind not in allowed:
            raise ValueError(
                f"Repository location '{name}' uses unsupported type '{kind}'."
            )
        location = (
            data.get("location")
            or data.get("path")
            or data.get("url")
            or data.get("target")
        )
        if not location:
            raise ValueError(
                f"Repository location '{name}' requires a 'location', 'path', 'url', or 'target'."
            )
        reference = data.get("reference") or data.get("ref") or data.get("branch")
        subpath = data.get("subpath") or data.get("sub_path") or data.get("folder")
        headers = dict(data.get("headers", {}))
        known_keys = {
            "name",
            "type",
            "kind",
            "location",
            "path",
            "url",
            "target",
            "reference",
            "ref",
            "branch",
            "subpath",
            "sub_path",
            "folder",
            "headers",
        }
        extra = {k: v for k, v in data.items() if k not in known_keys}
        return cls(
            name=name,
            kind=kind,
            location=str(location),
            reference=str(reference) if reference is not None else None,
            subpath=str(subpath) if subpath is not None else None,
            headers=headers,
            extra=extra,
        )


def _parse_repository_locations(raw: Any, section_name: str) -> Dict[str, RepositoryLocation]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        items = list(raw.items())
    elif isinstance(raw, Iterable):
        items = []
        for entry in raw:
            if not isinstance(entry, Mapping):
                raise TypeError(
                    f"Entries in '{section_name}' must be mappings containing at least a name."
                )
            name = entry.get("name")
            if not name:
                raise ValueError(
                    f"Entries in '{section_name}' require a 'name' attribute."
                )
            items.append((str(name), entry))
    else:
        raise TypeError(
            f"Configuration section '{section_name}' must be a mapping or a list of mappings."
        )
    locations: Dict[str, RepositoryLocation] = {}
    for name, data in items:
        key = str(name)
        if key in locations:
            raise ValueError(f"Duplicate repository location '{key}' in '{section_name}'.")
        locations[key] = RepositoryLocation.from_mapping(key, data)
    return locations


@dataclass
class GlobalConfig:
    """Runtime configuration shared across the entire flow."""

    remote_logging: Optional[RemoteLoggingConfig] = None
    env: Dict[str, str] = field(default_factory=dict)
    container_registries: List[str] = field(default_factory=list)
    max_concurrency: Optional[int] = None
    process_pool_size: Optional[int] = None
    shared_state: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    resource_locations: Dict[str, RepositoryLocation] = field(default_factory=dict)
    code_locations: Dict[str, RepositoryLocation] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "GlobalConfig":
        if not data:
            return cls()

        remote_logging = None
        if "remote_logging" in data or "remoteLogging" in data:
            remote_logging = RemoteLoggingConfig.from_mapping(
                data.get("remote_logging") or data.get("remoteLogging")
            )

        env = dict(data.get("env") or data.get("environment") or {})
        registries = list(data.get("container_registries") or data.get("containerRegistries") or [])
        max_concurrency = data.get("max_concurrency") or data.get("maxConcurrency")
        process_pool_size = data.get("process_pool_size") or data.get("processPoolSize")
        shared_state = dict(data.get("shared_state") or data.get("sharedState") or {})
        dependencies = list(data.get("dependencies") or data.get("python_dependencies") or [])
        resource_locations = _parse_repository_locations(
            data.get("resource_locations") or data.get("resourceLocations"),
            "resource_locations",
        )
        code_locations = _parse_repository_locations(
            data.get("code_locations") or data.get("codeLocations"),
            "code_locations",
        )

        known_keys = {
            "remote_logging",
            "remoteLogging",
            "env",
            "environment",
            "container_registries",
            "containerRegistries",
            "max_concurrency",
            "maxConcurrency",
            "process_pool_size",
            "processPoolSize",
            "shared_state",
            "sharedState",
            "dependencies",
            "python_dependencies",
            "resource_locations",
            "resourceLocations",
            "code_locations",
            "codeLocations",
        }
        extra = {k: v for k, v in data.items() if k not in known_keys}

        return cls(
            remote_logging=remote_logging,
            env=env,
            container_registries=registries,
            max_concurrency=int(max_concurrency) if max_concurrency is not None else None,
            process_pool_size=int(process_pool_size) if process_pool_size is not None else None,
            shared_state=shared_state,
            dependencies=dependencies,
            resource_locations=resource_locations,
            code_locations=code_locations,
            extra=extra,
        )

    def resolve_image(self, image: str) -> str:
        """Return the fully qualified container image using the configured registries."""
        if "://" in image or "/" in image.split("/")[0]:
            return image
        if not self.container_registries:
            return image
        # Use the first registry as default prefix
        prefix = self.container_registries[0].rstrip("/")
        return f"{prefix}/{image}"


@dataclass
class NodeDefinition:
    """Description of a single node within a flow."""

    id: str
    name: Optional[str] = None
    executor: str = "inline"  # inline | process | docker
    callable: Optional[str] = None
    image: Optional[str] = None
    command: List[str] = field(default_factory=list)
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    transitions: Dict[str, List[str]] = field(default_factory=dict)
    timeout: Optional[float] = None
    with_global_state: bool = True
    workdir: Optional[str] = None
    description: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "NodeDefinition":
        if "id" not in data:
            raise ValueError("Each node definition requires an 'id'.")
        transitions = data.get("transitions", {})
        if isinstance(transitions, list):
            # Allow shorthand list meaning default transitions
            transitions = {"default": list(transitions)}
        elif isinstance(transitions, Mapping):
            transitions = {
                str(key): list(value) if isinstance(value, Iterable) and not isinstance(value, (str, bytes)) else [value]
                for key, value in transitions.items()
            }
        else:
            raise TypeError("'transitions' must be a mapping or list if provided.")

        extra_keys = {
            "id",
            "name",
            "executor",
            "callable",
            "image",
            "command",
            "args",
            "env",
            "transitions",
            "timeout",
            "with_global_state",
            "withGlobalState",
            "workdir",
            "description",
        }

        with_global_state = data.get("with_global_state")
        if with_global_state is None and "withGlobalState" in data:
            with_global_state = data["withGlobalState"]
        if with_global_state is None:
            executor_type = str(data.get("executor", "inline")).lower()
            with_global_state = executor_type != "docker"

        command = data.get("command") or []
        if isinstance(command, str):
            command = [command]
        args = data.get("args") or []
        if isinstance(args, str):
            args = [args]

        return cls(
            id=str(data["id"]),
            name=data.get("name"),
            executor=str(data.get("executor", "inline")).lower(),
            callable=data.get("callable") or data.get("function"),
            image=data.get("image"),
            command=list(command),
            args=list(args),
            env=dict(data.get("env", {})),
            transitions=transitions,
            timeout=float(data["timeout"]) if "timeout" in data and data["timeout"] is not None else None,
            with_global_state=bool(with_global_state),
            workdir=data.get("workdir"),
            description=data.get("description"),
            extra={k: v for k, v in data.items() if k not in extra_keys},
        )


@dataclass
class FlowConfig:
    """Complete configuration for a flow graph."""

    name: str
    start: List[str]
    nodes: Dict[str, NodeDefinition]
    description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "FlowConfig":
        if "nodes" not in data:
            raise ValueError("Flow configuration requires a 'nodes' collection.")

        nodes_data = data["nodes"]
        if isinstance(nodes_data, Mapping):
            nodes_iter = nodes_data.values()
        elif isinstance(nodes_data, Iterable):
            nodes_iter = nodes_data
        else:
            raise TypeError("Flow configuration 'nodes' must be a mapping or a list.")

        nodes: Dict[str, NodeDefinition] = {}
        for node_mapping in nodes_iter:
            node = NodeDefinition.from_mapping(node_mapping)
            if node.id in nodes:
                raise ValueError(f"Duplicate node identifier '{node.id}'.")
            nodes[node.id] = node

        start = data.get("start") or data.get("triggers")
        if start is None:
            raise ValueError("Flow configuration requires a 'start' list.")
        if isinstance(start, (str, bytes)):
            start = [start]
        start_ids = [str(item) for item in start]

        description = data.get("description")
        metadata = dict(data.get("metadata", {}))

        flow = cls(name=str(data.get("name", "flow")), start=start_ids, nodes=nodes, description=description, metadata=metadata)
        flow.validate()
        return flow

    def validate(self) -> None:
        if not self.start:
            raise ValueError("At least one start node must be defined.")
        unknown_starts = [node_id for node_id in self.start if node_id not in self.nodes]
        if unknown_starts:
            raise ValueError(f"Unknown start node(s): {', '.join(unknown_starts)}")
        for node in self.nodes.values():
            for targets in node.transitions.values():
                for target in targets:
                    if target not in self.nodes:
                        raise ValueError(f"Node '{node.id}' references unknown successor '{target}'.")

    def get_node(self, node_id: str) -> NodeDefinition:
        try:
            return self.nodes[node_id]
        except KeyError as exc:  # pragma: no cover - validated earlier
            raise KeyError(f"Node '{node_id}' is not defined in the flow configuration.") from exc

    def next_nodes(self, node_id: str, status: str) -> List[str]:
        node = self.get_node(node_id)
        transitions = node.transitions or {}
        if status in transitions:
            return list(transitions[status])
        if "default" in transitions:
            return list(transitions["default"])
        return []

    def __iter__(self):  # pragma: no cover - convenience helper
        return iter(self.nodes.values())


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------


def _load_mapping_from_path(path: Path) -> MutableMapping[str, Any]:
    suffix = path.suffix.lower()
    text = path.read_text()
    if suffix in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("PyYAML is required to load YAML configuration files.")
        data = yaml.safe_load(text)
    elif suffix == ".toml":
        if tomllib is None:
            raise RuntimeError("tomllib is required to load TOML configuration files on this Python version.")
        data = tomllib.loads(text)
    else:
        data = json.loads(text)
    if not isinstance(data, MutableMapping):
        raise TypeError("Configuration file must contain a mapping at the top level.")
    return data


def load_flow_config(path: str | Path) -> FlowConfig:
    """Load a :class:`FlowConfig` instance from the provided path."""
    mapping = _load_mapping_from_path(Path(path))
    return FlowConfig.from_mapping(mapping)


def load_global_config(path: str | Path) -> GlobalConfig:
    """Load a :class:`GlobalConfig` instance from the provided path."""
    mapping = _load_mapping_from_path(Path(path))
    return GlobalConfig.from_mapping(mapping)

