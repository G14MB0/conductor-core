"""Helpers to collect container logs for display inside the dashboard."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Sequence


_DEFAULT_CONTAINERS = ("deploy-dashboard-1", "deploy-conductor-1")


@dataclass
class ContainerLogSnapshot:
    """Result of fetching the tail of a container log."""

    name: str
    content: str
    error: Optional[str] = None

    @property
    def lines(self) -> List[str]:
        if not self.content:
            return []
        return self.content.splitlines()


def _resolve_container_names(names: Optional[Sequence[str]]) -> List[str]:
    if names:
        return [name for name in names if name]
    env_value = os.environ.get("CONDUCTOR_DASHBOARD_LOG_CONTAINERS")
    if env_value:
        items = [item.strip() for item in env_value.split(",") if item.strip()]
        if items:
            return items
    return list(_DEFAULT_CONTAINERS)


def _resolve_docker_command() -> Optional[str]:
    override = os.environ.get("CONDUCTOR_DASHBOARD_DOCKER_BIN")
    if override:
        return override
    return shutil.which("docker")


def collect_container_logs(*, names: Optional[Sequence[str]] = None, tail: int = 200) -> List[ContainerLogSnapshot]:
    """Return log snippets for the configured containers.

    The function is resilient to missing Docker installations or containers.
    """

    resolved_names = _resolve_container_names(names)
    if not resolved_names:
        return []
    docker_cmd = _resolve_docker_command()
    if not docker_cmd:
        return [
            ContainerLogSnapshot(name=name, content="", error="Docker CLI non disponibile nel container dashboard.")
            for name in resolved_names
        ]

    snapshots: List[ContainerLogSnapshot] = []
    for name in resolved_names:
        args = [docker_cmd, "logs", "--tail", str(max(1, tail)), name]
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except Exception as exc:  # pragma: no cover - defensive runtime safeguard
            snapshots.append(ContainerLogSnapshot(name=name, content="", error=str(exc)))
            continue
        if result.returncode != 0:
            error_message = result.stderr.strip() or f"Exit code {result.returncode}"
            snapshots.append(ContainerLogSnapshot(name=name, content=result.stdout.strip(), error=error_message))
        else:
            snapshots.append(ContainerLogSnapshot(name=name, content=result.stdout.strip()))
    return snapshots


__all__ = ["ContainerLogSnapshot", "collect_container_logs"]
