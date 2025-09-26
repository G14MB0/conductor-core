"""Configuration models and helpers for conductor flows."""
from __future__ import annotations

"""Configuration models and helpers for conductor flows."""

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple
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
    """Server-level runtime configuration shared across flows."""

    def __init__(
        self,
        *,
        env: Optional[Mapping[str, Any]] = None,
        shared_state: Optional[Mapping[str, Any]] = None,
        max_concurrency: Optional[int] = None,
        process_pool_size: Optional[int] = None,
        dependencies: Optional[Iterable[str]] = None,
        remote_logging: Optional[RemoteLoggingConfig] = None,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.env: Dict[str, Any] = dict(env or {})
        self.shared_state: Dict[str, Any] = dict(shared_state or {})
        self.max_concurrency = max_concurrency
        self.process_pool_size = process_pool_size
        self.dependencies = list(dependencies or [])
        self.remote_logging = remote_logging
        self.extra: Dict[str, Any] = dict(extra or {})

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
        max_concurrency = data.get("max_concurrency") or data.get("maxConcurrency")
        process_pool_size = data.get("process_pool_size") or data.get("processPoolSize")
        shared_state = dict(data.get("shared_state") or data.get("sharedState") or {})
        raw_dependencies = data.get("dependencies") or data.get("packages")
        dependencies: List[str] = []
        if raw_dependencies:
            if isinstance(raw_dependencies, (list, tuple, set)):
                dependencies = [str(item).strip() for item in raw_dependencies if str(item).strip()]
            elif isinstance(raw_dependencies, str):
                dependencies = [
                    line.strip()
                    for line in raw_dependencies.replace(",", "\n").splitlines()
                    if line.strip()
                ]
            else:
                raise TypeError("Global config 'dependencies' must be a sequence or string.")

        known_keys = {
            "remote_logging",
            "remoteLogging",
            "env",
            "environment",
            "max_concurrency",
            "maxConcurrency",
            "process_pool_size",
            "processPoolSize",
            "shared_state",
            "sharedState",
            "dependencies",
            "packages",
        }
        extra = {k: v for k, v in data.items() if k not in known_keys}

        return cls(
            remote_logging=remote_logging,
            env=env,
            max_concurrency=int(max_concurrency) if max_concurrency is not None else None,
            process_pool_size=int(process_pool_size) if process_pool_size is not None else None,
            shared_state=shared_state,
            dependencies=dependencies,
            extra=extra,
        )

@dataclass
class SecretConfig:
    """Description of a secret used while running a flow."""

    name: str
    value: Optional[str] = None
    env: Optional[str] = None
    file: Optional[str] = None
    type: str = "generic"
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, name: str, data: Mapping[str, Any] | str) -> "SecretConfig":
        if isinstance(data, str):
            return cls(name=name, value=data)
        if not isinstance(data, Mapping):
            raise TypeError(f"Secret '{name}' must be defined as a mapping or string literal.")
        value = data.get("value")
        env_name = data.get("env") or data.get("environment")
        file_path = data.get("file") or data.get("path")
        secret_type = str(data.get("type") or data.get("kind") or "generic")
        metadata = dict(data.get("metadata") or {})
        known_keys = {
            "value",
            "env",
            "environment",
            "file",
            "path",
            "type",
            "kind",
            "metadata",
            "extra",
        }
        extra = {k: v for k, v in data.items() if k not in known_keys}
        if extra:
            metadata.update(extra)
        return cls(
            name=name,
            value=str(value) if value is not None else None,
            env=str(env_name) if env_name else None,
            file=str(file_path) if file_path else None,
            type=secret_type,
            metadata=metadata,
        )

    def resolve(self) -> Optional[str]:
        if self.value is not None:
            return self.value
        if self.env:
            return os.environ.get(self.env)
        if self.file:
            path = Path(self.file).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"Secret file '{self.file}' does not exist.")
            return path.read_text().strip()
        return None

@dataclass
class ContainerRegistryConfig:
    """Configuration for a container registry used by docker executors."""

    name: str
    url: str
    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    password_secret: Optional[str] = None
    token_secret: Optional[str] = None
    verify: Optional[bool] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, name: str, data: Mapping[str, Any] | str) -> "ContainerRegistryConfig":
        if isinstance(data, str):
            return cls(name=name, url=data)
        if not isinstance(data, Mapping):
            raise TypeError(f"Container registry '{name}' must be a mapping or string literal.")
        url = data.get("url") or data.get("location") or data.get("registry")
        if not url:
            raise ValueError(f"Container registry '{name}' requires a 'url' or 'location'.")
        username = data.get("username")
        password = data.get("password")
        token = data.get("token")
        password_secret = data.get("password_secret") or data.get("passwordSecret")
        token_secret = data.get("token_secret") or data.get("tokenSecret")
        verify = data.get("verify")
        known_keys = {
            "url",
            "location",
            "registry",
            "username",
            "password",
            "password_secret",
            "passwordSecret",
            "token",
            "token_secret",
            "tokenSecret",
            "verify",
        }
        extra = {k: v for k, v in data.items() if k not in known_keys}
        return cls(
            name=name,
            url=str(url),
            username=str(username) if username is not None else None,
            password=str(password) if password is not None else None,
            token=str(token) if token is not None else None,
            password_secret=str(password_secret) if password_secret else None,
            token_secret=str(token_secret) if token_secret else None,
            verify=bool(verify) if verify is not None else None,
            extra=extra,
        )

    def resolve_password(self, secrets: Mapping[str, SecretConfig]) -> Optional[str]:
        if self.password_secret:
            secret = secrets.get(self.password_secret)
            if secret:
                resolved = secret.resolve()
                if resolved is not None:
                    return resolved
        return self.password

    def resolve_token(self, secrets: Mapping[str, SecretConfig]) -> Optional[str]:
        if self.token_secret:
            secret = secrets.get(self.token_secret)
            if secret:
                resolved = secret.resolve()
                if resolved is not None:
                    return resolved
        return self.token

def _parse_container_registries(raw: Any) -> Dict[str, ContainerRegistryConfig]:
    if raw is None:
        return {}
    items: List[Tuple[str, Any]] = []
    if isinstance(raw, Mapping):
        items = [(str(name), value) for name, value in raw.items()]
    elif isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
        for entry in raw:
            if not isinstance(entry, Mapping):
                raise TypeError("Container registry entries must be mappings.")
            name = entry.get("name")
            if not name:
                raise ValueError("Container registry entries must include a 'name'.")
            items.append((str(name), entry))
    else:
        raise TypeError("Container registries must be provided as a mapping or list of mappings.")
    registries: Dict[str, ContainerRegistryConfig] = {}
    for name, payload in items:
        if name in registries:
            raise ValueError(f"Duplicate container registry '{name}'.")
        registries[name] = ContainerRegistryConfig.from_mapping(name, payload)
    return registries

def _parse_secrets(raw: Any) -> Dict[str, SecretConfig]:
    if raw is None:
        return {}
    items: List[Tuple[str, Any]] = []
    if isinstance(raw, Mapping):
        items = [(str(name), value) for name, value in raw.items()]
    elif isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
        for entry in raw:
            if not isinstance(entry, Mapping):
                raise TypeError("Secret declarations must be mappings.")
            name = entry.get("name")
            if not name:
                raise ValueError("Secret declarations require a 'name'.")
            items.append((str(name), entry))
    else:
        raise TypeError("Secrets must be provided as a mapping or list of mappings.")
    secrets: Dict[str, SecretConfig] = {}
    for name, payload in items:
        if name in secrets:
            raise ValueError(f"Duplicate secret '{name}'.")
        secrets[name] = SecretConfig.from_mapping(name, payload)
    return secrets

@dataclass
class FlowRuntimeConfig:
    """Flow-scoped runtime configuration describing resources and credentials."""

    resource_locations: Dict[str, RepositoryLocation] = field(default_factory=dict)
    code_locations: Dict[str, RepositoryLocation] = field(default_factory=dict)
    container_registries: Dict[str, ContainerRegistryConfig] = field(default_factory=dict)
    secrets: Dict[str, SecretConfig] = field(default_factory=dict)
    flow_definition: Optional[str] = None
    callables: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "FlowRuntimeConfig":
        if not data:
            return cls()
        resources = _parse_repository_locations(
            data.get("resource_locations") or data.get("resourceLocations"),
            "resource_locations",
        )
        codes = _parse_repository_locations(
            data.get("code_locations") or data.get("codeLocations"),
            "code_locations",
        )
        registries = _parse_container_registries(
            data.get("container_registries")
            or data.get("containerRegistries")
            or data.get("registries")
        )
        secrets = _parse_secrets(data.get("secrets"))
        flow_definition = (
            data.get("flow_definition")
            or data.get("flowDefinition")
            or data.get("flow_path")
            or data.get("flow")
        )
        raw_callables = (
            data.get("callables")
            or data.get("callable_files")
            or data.get("callableFiles")
        )
        callables_list: List[str] = []
        if isinstance(raw_callables, (str, bytes)):
            callables_list = [str(raw_callables)]
        elif isinstance(raw_callables, Iterable):
            callables_list = [str(item) for item in raw_callables if item is not None]
        known_keys = {
            "resource_locations",
            "resourceLocations",
            "code_locations",
            "codeLocations",
            "container_registries",
            "containerRegistries",
            "registries",
            "secrets",
            "flow_definition",
            "flowDefinition",
            "flow_path",
            "flow",
            "callables",
            "callable_files",
            "callableFiles",
        }
        extra = {k: v for k, v in data.items() if k not in known_keys}
        return cls(
            resource_locations=resources,
            code_locations=codes,
            container_registries=registries,
            secrets=secrets,
            flow_definition=str(flow_definition) if flow_definition else None,
            callables=callables_list,
            extra=extra,
        )

    def get_secret(self, name: str) -> Optional[SecretConfig]:
        return self.secrets.get(name)

    def resolve_secret_value(self, name: str) -> Optional[str]:
        secret = self.get_secret(name)
        if secret is None:
            return None
        return secret.resolve()

    def resolve_image(self, image: str) -> str:
        if "://" in image or "/" in image.split("/")[0]:
            return image
        if not self.container_registries:
            return image
        first = next(iter(self.container_registries.values()))
        prefix = first.url.rstrip("/")
        return f"{prefix}/{image}"

    def registry_credentials(self, name: str) -> Dict[str, Optional[str]]:
        registry = self.container_registries.get(name)
        if registry is None:
            raise KeyError(f"Unknown container registry '{name}'.")
        return {
            "username": registry.username,
            "password": registry.resolve_password(self.secrets),
            "token": registry.resolve_token(self.secrets),
        }

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

@dataclass
class FlowDeployment:
    """Couples a flow definition with its runtime and server configuration."""

    flow: FlowConfig
    global_config: GlobalConfig
    runtime_config: FlowRuntimeConfig = field(default_factory=FlowRuntimeConfig)
    name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def resolved_name(self) -> str:
        """Return the canonical name for this deployment."""

        if self.name and self.name.strip():
            return self.name.strip()
        if self.flow.name and self.flow.name.strip():
            return self.flow.name.strip()
        return "flow"

    def normalized(self, *, name: Optional[str] = None) -> "FlowDeployment":
        """Return a detached copy with a concrete name assigned."""

        resolved_name = name or self.resolved_name()
        clone = FlowDeployment(
            flow=copy.deepcopy(self.flow),
            global_config=copy.deepcopy(self.global_config),
            runtime_config=copy.deepcopy(self.runtime_config),
            name=resolved_name,
            metadata=copy.deepcopy(self.metadata),
        )
        return clone

    @classmethod
    def from_components(
        cls,
        flow: FlowConfig,
        *,
        global_config: Optional[GlobalConfig] = None,
        runtime_config: Optional[FlowRuntimeConfig] = None,
        name: Optional[str] = None,
        resource_locations: Optional[Mapping[str, RepositoryLocation]] = None,
        code_locations: Optional[Mapping[str, RepositoryLocation]] = None,
        container_registries: Optional[Mapping[str, object]] = None,
        secrets: Optional[Mapping[str, object]] = None,
        flow_definition: Optional[str] = None,
        callables: Optional[Iterable[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "FlowDeployment":
        """Build a deployment from the provided parts, copying data defensively."""

        base_global = copy.deepcopy(global_config) if global_config is not None else GlobalConfig.from_mapping({})
        base_runtime = copy.deepcopy(runtime_config) if runtime_config is not None else FlowRuntimeConfig.from_mapping({})

        if resource_locations:
            base_runtime.resource_locations.update(copy.deepcopy(dict(resource_locations)))
        if code_locations:
            base_runtime.code_locations.update(copy.deepcopy(dict(code_locations)))

        if container_registries:
            registries: Dict[str, ContainerRegistryConfig] = {}
            for key, value in dict(container_registries).items():
                if isinstance(value, ContainerRegistryConfig):
                    registries[key] = copy.deepcopy(value)
                else:
                    registries[key] = ContainerRegistryConfig.from_mapping(key, value)
            base_runtime.container_registries.update(registries)

        if secrets:
            secret_map: Dict[str, SecretConfig] = {}
            for key, value in dict(secrets).items():
                if isinstance(value, SecretConfig):
                    secret_map[key] = copy.deepcopy(value)
                else:
                    secret_map[key] = SecretConfig.from_mapping(key, value)
            base_runtime.secrets.update(secret_map)

        if flow_definition:
            base_runtime.flow_definition = str(flow_definition)

        if callables is not None:
            base_runtime.callables = [str(item) for item in callables]

        deployment = cls(
            flow=flow,
            global_config=base_global,
            runtime_config=base_runtime,
            name=name or (flow.name if flow.name else None),
            metadata=dict(metadata or {}),
        )
        return deployment.normalized()

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

def load_flow_runtime_config(path: str | Path) -> FlowRuntimeConfig:
    """Load flow-scoped runtime settings from the provided path."""
    mapping = _load_mapping_from_path(Path(path))
    return FlowRuntimeConfig.from_mapping(mapping)

def load_global_config(path: str | Path) -> GlobalConfig:
    """Load a :class:`GlobalConfig` instance from the provided path."""
    mapping = _load_mapping_from_path(Path(path))
    return GlobalConfig.from_mapping(mapping)

