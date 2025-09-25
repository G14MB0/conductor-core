"""Helpers for building flow deployments from local uploads or Git repositories."""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from conductor.config import (
    FlowDeployment,
    GlobalConfig,
    RepositoryLocation,
    load_flow_config,
    load_global_config,
)

LOGGER = logging.getLogger(__name__)

_STORAGE_ROOT = Path(__file__).resolve().parent.parent / "storage"
_LOCAL_ROOT = _STORAGE_ROOT / "local"
_GIT_ROOT = _STORAGE_ROOT / "git"


@dataclass
class DeploymentResult:
    """Result of preparing a deployment, including metadata for UI display."""

    deployment: FlowDeployment
    metadata: Dict[str, Optional[str]]


@dataclass
class GitRepositorySnapshot:
    """Description of a cloned Git repository with helper metadata."""

    repo_url: str
    requested_reference: Optional[str]
    root_path: Path
    commit: str
    config_candidates: List[str]
    directories: List[str]

    def resolve_path(self, relative: str) -> Path:
        """Resolve a repository-relative path safely inside the snapshot root."""

        return _resolve_within(self.root_path, Path(relative))


def prepare_local_deployment(
    *,
    flow_payload: bytes,
    flow_filename: str,
    base_config: GlobalConfig,
    flow_name: Optional[str] = None,
    global_payload: Optional[bytes] = None,
    global_filename: Optional[str] = None,
    code_archive: Optional[bytes] = None,
    code_filename: Optional[str] = None,
) -> DeploymentResult:
    """Materialise a :class:`FlowDeployment` from uploaded artefacts."""

    storage_root = _ensure_directory(_LOCAL_ROOT / uuid.uuid4().hex)
    metadata: Dict[str, Optional[str]] = {
        "source": "local",
        "storage": str(storage_root),
    }

    safe_flow_name = _normalise_filename(flow_filename, "flow.json")
    flow_path = _write_file(storage_root / "flow" / safe_flow_name, flow_payload)
    flow_config = load_flow_config(flow_path)
    metadata["flow_path"] = str(flow_path)

    expected_name = (flow_name or flow_config.name or "flow").strip()
    slug = _slugify(expected_name)

    global_config: GlobalConfig
    if global_payload is not None:
        safe_global_name = _normalise_filename(global_filename or "global.json", "global.json")
        global_path = _write_file(storage_root / "global" / safe_global_name, global_payload)
        global_config = load_global_config(global_path)
        metadata["global_path"] = str(global_path)
        metadata["global_source"] = "uploaded"
    else:
        global_config = base_config
        metadata["global_path"] = None
        metadata["global_source"] = "base"

    code_locations: Dict[str, RepositoryLocation] = {}
    if code_archive is not None:
        safe_code_name = _normalise_filename(code_filename or "code.zip", "code.zip")
        code_root = _ensure_directory(storage_root / "code" / slug)
        metadata["code_archive"] = safe_code_name
        extracted_root = _extract_zip_archive(code_archive, code_root)
        preferred_root = _default_code_root(extracted_root)
        metadata["code_path"] = str(preferred_root)
        location_key = f"{slug}-code"
        code_locations[location_key] = RepositoryLocation.from_mapping(
            location_key,
            {
                "type": "filesystem",
                "location": str(preferred_root),
            },
        )
    else:
        metadata["code_archive"] = None
        metadata["code_path"] = None

    deployment = FlowDeployment.from_components(
        flow_config,
        global_config=global_config,
        name=flow_name,
        code_locations=code_locations,
        metadata=metadata,
    )
    return DeploymentResult(deployment=deployment, metadata=metadata)


def prime_git_repository(
    url: str,
    *,
    reference: Optional[str] = None,
    token: Optional[str] = None,
    timeout: int = 120,
) -> GitRepositorySnapshot:
    """Clone a Git repository and prepare metadata for interactive selection."""

    _ensure_directory(_GIT_ROOT)
    repo_root = _ensure_directory(_GIT_ROOT / uuid.uuid4().hex)

    clone_url = _augment_url_with_token(url, token)
    _run_git([
        "clone",
        "--depth",
        "1",
        clone_url,
        str(repo_root),
    ], cwd=_GIT_ROOT, token=token, timeout=timeout)

    if reference:
        _run_git([
            "fetch",
            "origin",
            reference,
            "--depth",
            "1",
        ], cwd=repo_root, token=token, timeout=timeout)
        _run_git([
            "checkout",
            "FETCH_HEAD",
        ], cwd=repo_root, token=token, timeout=timeout)

    commit = _run_git([
        "rev-parse",
        "HEAD",
    ], cwd=repo_root, timeout=timeout).stdout.strip()

    config_candidates = _list_config_candidates(repo_root)
    directories = _list_directories(repo_root)

    metadata = GitRepositorySnapshot(
        repo_url=url,
        requested_reference=reference,
        root_path=repo_root,
        commit=commit,
        config_candidates=config_candidates,
        directories=directories,
    )
    return metadata


def build_deployment_from_git(
    snapshot: GitRepositorySnapshot,
    *,
    flow_path: str,
    base_config: GlobalConfig,
    flow_name: Optional[str] = None,
    global_config_path: Optional[str] = None,
    code_paths: Optional[Sequence[str]] = None,
) -> DeploymentResult:
    """Build a deployment from a cloned repository snapshot."""

    flow_file = snapshot.resolve_path(flow_path)
    flow_config = load_flow_config(flow_file)

    metadata: Dict[str, Optional[str]] = {
        "source": "git",
        "repo": snapshot.repo_url,
        "commit": snapshot.commit,
        "requested_reference": snapshot.requested_reference,
        "flow_path": flow_path,
        "global_path": global_config_path,
        "code_paths": ",".join(code_paths or ()),
        "storage": str(snapshot.root_path),
    }

    if global_config_path:
        global_file = snapshot.resolve_path(global_config_path)
        global_config = load_global_config(global_file)
        metadata["global_origin"] = "repository"
    else:
        global_config = base_config
        metadata["global_origin"] = "base"

    code_locations: Dict[str, RepositoryLocation] = {}
    selected_paths = list(code_paths or [])
    if selected_paths:
        slug = _slugify(flow_name or flow_config.name or "flow")
        total = len(selected_paths)
        for idx, rel_path in enumerate(selected_paths, start=1):
            key = f"{slug}-code" if total == 1 else f"{slug}-code-{idx}"
            code_locations[key] = RepositoryLocation.from_mapping(
                key,
                {
                    "type": "git",
                    "location": snapshot.repo_url,
                    "reference": snapshot.commit,
                    "subpath": rel_path,
                },
            )

    deployment = FlowDeployment.from_components(
        flow_config,
        global_config=global_config,
        name=flow_name,
        code_locations=code_locations,
        metadata=metadata,
    )
    return DeploymentResult(deployment=deployment, metadata=metadata)


def _ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _normalise_filename(name: str, default: str) -> str:
    if not name:
        return default
    candidate = Path(name).name.strip()
    return candidate or default


def _slugify(value: str) -> str:
    value = value.strip().lower()
    allowed = []
    for char in value:
        if char.isalnum():
            allowed.append(char)
        elif char in {"-", "_", "."}:
            allowed.append(char)
        elif char.isspace():
            allowed.append("-")
    slug = "".join(allowed).strip("-")
    return slug or "flow"


def _extract_zip_archive(data: bytes, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            relative = Path(member.filename)
            if not relative.parts:
                continue
            if any(part == ".." for part in relative.parts):
                raise ValueError("L'archivio contiene percorsi non validi.")
            target = _resolve_within(destination, relative)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    return destination


def _default_code_root(path: Path) -> Path:
    try:
        entries = [item for item in path.iterdir() if not item.name.startswith("__MACOSX")]
    except FileNotFoundError:
        return path
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return path


def _resolve_within(base: Path, relative: Path) -> Path:
    candidate = (base / relative).resolve()
    base_resolved = base.resolve()
    if base_resolved == candidate:
        return candidate
    if base_resolved not in candidate.parents:
        raise ValueError("Percorso fuori dalla directory di destinazione.")
    return candidate


def _augment_url_with_token(url: str, token: Optional[str]) -> str:
    if not token:
        return url
    if url.startswith("https://") and "@" not in url.split("://", 1)[1]:
        return url.replace("https://", f"https://{token}@", 1)
    return url


def _run_git(
    args: Sequence[str],
    *,
    cwd: Path,
    token: Optional[str] = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:  # pragma: no cover - dependency missing
        raise RuntimeError("Git non � installato sul server.") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        if token:
            message = message.replace(token, "***")
        LOGGER.debug("Git command failed: git %s", " ".join(args))
        raise RuntimeError(message) from exc


def _list_config_candidates(root: Path, limit: int = 200) -> List[str]:
    patterns = ("*.json", "*.yaml", "*.yml", "*.toml")
    seen = set()
    results: List[str] = []
    for pattern in patterns:
        for path in root.rglob(pattern):
            if path.is_dir():
                continue
            if any(part.startswith(".git") for part in path.relative_to(root).parts):
                continue
            relative = path.relative_to(root).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            results.append(relative)
            if len(results) >= limit:
                return results
    return results


def _list_directories(root: Path, limit: int = 150) -> List[str]:
    results = ["."]
    for path in root.rglob("*"):
        if not path.is_dir():
            continue
        relative = path.relative_to(root)
        if not relative.parts:
            continue
        if len(relative.parts) > 4:
            continue
        if any(part.startswith(".git") or part in {"__pycache__"} for part in relative.parts):
            continue
        rel_str = relative.as_posix()
        if rel_str not in results:
            results.append(rel_str)
        if len(results) >= limit:
            break
    return results


__all__ = [
    "DeploymentResult",
    "GitRepositorySnapshot",
    "prepare_local_deployment",
    "prime_git_repository",
    "build_deployment_from_git",
]

