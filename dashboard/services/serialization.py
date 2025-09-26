"""Serialisation helpers for dashboard interactions."""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from conductor.config import (
    ContainerRegistryConfig,
    FlowConfig,
    FlowRuntimeConfig,
    GlobalConfig,
    NodeDefinition,
    RepositoryLocation,
    RemoteLoggingConfig,
    SecretConfig,
)


def flow_config_to_dict(flow: FlowConfig) -> Dict[str, Any]:
    """Convert a FlowConfig instance into a serialisable dictionary."""

    data: Dict[str, Any] = {
        "name": flow.name,
        "start": list(flow.start),
        "metadata": dict(flow.metadata),
    }
    if flow.description:
        data["description"] = flow.description
    nodes: List[Dict[str, Any]] = []
    for node in flow.nodes.values():
        nodes.append(_node_to_mapping(node))
    data["nodes"] = nodes
    return data


def _node_to_mapping(node: NodeDefinition) -> Dict[str, Any]:
    mapping: Dict[str, Any] = {
        "id": node.id,
        "executor": node.executor,
        "transitions": dict(node.transitions),
    }
    if node.name:
        mapping["name"] = node.name
    if node.callable:
        mapping["callable"] = node.callable
    if node.image:
        mapping["image"] = node.image
    if node.command:
        mapping["command"] = list(node.command)
    if node.args:
        mapping["args"] = list(node.args)
    if node.env:
        mapping["env"] = dict(node.env)
    if node.timeout is not None:
        mapping["timeout"] = node.timeout
    mapping["with_global_state"] = node.with_global_state
    if node.workdir:
        mapping["workdir"] = node.workdir
    if node.description:
        mapping["description"] = node.description
    if node.extra:
        mapping.update(node.extra)
    return mapping


def global_config_to_dict(config: GlobalConfig) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "env": dict(config.env),
        "max_concurrency": config.max_concurrency,
        "process_pool_size": config.process_pool_size,
        "shared_state": dict(config.shared_state),
    }
    if config.remote_logging:
        data["remote_logging"] = _remote_logging_to_dict(config.remote_logging)
    if config.dependencies:
        data["dependencies"] = list(config.dependencies)
    if config.extra:
        data.update(config.extra)
    return data


def _remote_logging_to_dict(config: RemoteLoggingConfig) -> Dict[str, Any]:
    return {
        "target": config.target,
        "method": config.method,
        "headers": dict(config.headers),
        "enabled": config.enabled,
        "verify": config.verify,
    }


def runtime_config_to_dict(config: FlowRuntimeConfig) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if config.resource_locations:
        data["resource_locations"] = repository_locations_to_mapping(config.resource_locations)
    if config.code_locations:
        data["code_locations"] = repository_locations_to_mapping(config.code_locations)
    if config.container_registries:
        data["container_registries"] = container_registries_to_mapping(config.container_registries)
    if config.secrets:
        data["secrets"] = secrets_to_mapping(config.secrets)
    if config.flow_definition:
        data["flow_definition"] = config.flow_definition
    if config.callables:
        data["callables"] = list(config.callables)
    if config.extra:
        data.update(config.extra)
    return data


def runtime_config_from_dict(data: Mapping[str, Any] | None) -> FlowRuntimeConfig:
    if not data:
        return FlowRuntimeConfig()
    return FlowRuntimeConfig.from_mapping(data)


def container_registry_to_dict(registry: ContainerRegistryConfig) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "url": registry.url,
    }
    if registry.username:
        data["username"] = registry.username
    if registry.password:
        data["password"] = registry.password
    if registry.token:
        data["token"] = registry.token
    if registry.password_secret:
        data["password_secret"] = registry.password_secret
    if registry.token_secret:
        data["token_secret"] = registry.token_secret
    if registry.verify is not None:
        data["verify"] = registry.verify
    if registry.extra:
        data.update(registry.extra)
    return data


def container_registries_to_mapping(
    registries: Mapping[str, ContainerRegistryConfig]
) -> Dict[str, Any]:
    return {name: container_registry_to_dict(registry) for name, registry in registries.items()}


def secret_to_dict(secret: SecretConfig) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "type": secret.type,
    }
    if secret.value is not None:
        data["value"] = secret.value
    if secret.env:
        data["env"] = secret.env
    if secret.file:
        data["file"] = secret.file
    if secret.metadata:
        data["metadata"] = dict(secret.metadata)
    return data


def secrets_to_mapping(secrets: Mapping[str, SecretConfig]) -> Dict[str, Any]:
    return {name: secret_to_dict(secret) for name, secret in secrets.items()}



def repository_locations_to_mapping(
    locations: Mapping[str, RepositoryLocation]
) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for name, location in locations.items():
        result[name] = repository_location_to_dict(location)
    return result


def repository_location_to_dict(location: RepositoryLocation) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "type": location.kind,
        "location": location.location,
    }
    if location.reference:
        data["reference"] = location.reference
    if location.subpath:
        data["subpath"] = location.subpath
    if location.headers:
        data["headers"] = dict(location.headers)
    if location.extra:
        data.update(location.extra)
    return data


def repository_locations_to_rows(
    locations: Mapping[str, RepositoryLocation]
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name, location in locations.items():
        row = {
            "name": name,
            "kind": location.kind,
            "location": location.location,
            "reference": location.reference or "",
            "subpath": location.subpath or "",
            "headers": _dump_json_field(location.headers),
            "extra": _dump_json_field(location.extra),
        }
        rows.append(row)
    return rows


def rows_to_repository_locations(rows: Iterable[Mapping[str, Any]]) -> Dict[str, RepositoryLocation]:
    locations: Dict[str, RepositoryLocation] = {}
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        kind = str(row.get("kind") or "filesystem").strip() or "filesystem"
        location = row.get("location")
        if not location:
            raise ValueError(f"Resource '{name}' is missing a location.")
        reference = str(row.get("reference") or "").strip() or None
        subpath = str(row.get("subpath") or "").strip() or None
        headers = _load_json_field(row.get("headers"), field_name=f"headers ({name})")
        extra = _load_json_field(row.get("extra"), field_name=f"extra ({name})")
        payload: Dict[str, Any] = {
            "type": kind,
            "location": location,
        }
        if reference:
            payload["reference"] = reference
        if subpath:
            payload["subpath"] = subpath
        if headers:
            payload["headers"] = headers
        if extra:
            payload.update(extra)
        locations[name] = RepositoryLocation.from_mapping(name, payload)
    return locations


def _dump_json_field(value: Optional[Mapping[str, Any]]) -> str:
    if not value:
        return ""
    return json.dumps(value, ensure_ascii=False, indent=2)


def _load_json_field(value: Any, *, field_name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    text = str(value).strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {field_name}: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise ValueError(f"Expected {field_name} to be a JSON object.")
    return dict(loaded)


__all__ = [
    "flow_config_to_dict",
    "global_config_to_dict",
    "runtime_config_to_dict",
    "runtime_config_from_dict",
    "container_registry_to_dict",
    "container_registries_to_mapping",
    "secret_to_dict",
    "secrets_to_mapping",
    "repository_locations_to_rows",
    "rows_to_repository_locations",
    "repository_location_to_dict",
    "repository_locations_to_mapping",
]
