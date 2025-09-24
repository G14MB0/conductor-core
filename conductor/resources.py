"""Resource resolution utilities for conductor."""
from __future__ import annotations

import contextlib

import subprocess
import tempfile
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Dict, Optional

from .config import GlobalConfig, RepositoryLocation


class ResourceResolver:
    """Resolve files and code locations declared in :class:`GlobalConfig`."""

    def __init__(self, config: GlobalConfig, *, cache_root: Optional[Path] = None):
        self._config = config
        self._stack = contextlib.ExitStack()
        self._temp_dir: Optional[Path] = None
        self._cache_root = Path(
            cache_root
            or config.extra.get("resource_cache_dir")
            or (Path.home() / ".conductor" / "sources")
        ).expanduser()
        self._cache_root.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "ResourceResolver":
        self._stack.__enter__()
        temp_dir = self._stack.enter_context(tempfile.TemporaryDirectory(prefix="conductor_res_"))
        self._temp_dir = Path(temp_dir)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stack.__exit__(exc_type, exc, tb)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_file(self, identifier: Optional[str]) -> Optional[Path]:
        """Return a local path for the provided identifier."""

        if identifier is None:
            return None
        identifier = str(identifier)
        parsed = urllib.parse.urlparse(identifier)
        scheme = parsed.scheme.lower()

        if not scheme:
            path = Path(identifier).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"File '{identifier}' does not exist.")
            return path

        if scheme == "file":
            path_str = parsed.path
            if parsed.netloc:
                path_str = f"{parsed.netloc}{path_str}"
            path = Path(urllib.request.url2pathname(path_str)).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"File '{identifier}' does not exist.")
            return path

        if scheme in {"http", "https", "ftp"}:
            return self._download_url(identifier)

        if scheme in self._config.resource_locations:
            location = self._config.resource_locations[scheme]
            return self._resolve_from_location(location, parsed)

        raise ValueError(
            f"Unsupported resource identifier '{identifier}'. Provide a local path, URL, or registered alias."
        )

    def code_paths(self) -> Dict[str, Path]:
        """Return filesystem paths for configured code locations keyed by their alias."""

        paths: Dict[str, Path] = {}
        for name, location in self._config.code_locations.items():
            root = self._repository_root(location)
            if isinstance(root, str):
                raise ValueError(
                    f"Code location '{name}' uses type '{location.kind}' which does not resolve to a filesystem path."
                )
            path = root
            if location.subpath:
                subpath = self._normalise_relative(location.subpath)
                path = (root / subpath).resolve()
            if not path.exists():
                raise FileNotFoundError(
                    f"Code location '{name}' resolved to '{path}', but that path does not exist."
                )
            paths[name] = path
        return paths

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_from_location(self, location: RepositoryLocation, parsed: urllib.parse.ParseResult) -> Path:
        relative = self._relative_from_parsed(parsed)
        if location.kind in {"filesystem", "git"}:
            root = self._repository_root(location)
            if isinstance(root, str):  # pragma: no cover - defensive
                raise ValueError(
                    f"Repository '{location.name}' of type '{location.kind}' did not resolve to a local path."
                )
            if location.subpath:
                root = root / self._normalise_relative(location.subpath)
            target = (root / relative).resolve()
            if not target.exists():
                raise FileNotFoundError(
                    f"Resource '{relative}' not found inside repository '{location.name}'."
                )
            return target
        if location.kind == "http":
            base_url = location.location
            if location.subpath:
                base_url = self._join_url(base_url, location.subpath)
            url = self._join_url(base_url, relative.as_posix())
            return self._download_url(url, headers=location.headers, suggested_name=relative.name)
        raise ValueError(f"Unsupported repository type '{location.kind}' for '{location.name}'.")

    def _repository_root(self, location: RepositoryLocation) -> Path | str:
        if location.kind == "filesystem":
            root = Path(location.location).expanduser()
            if not root.exists():
                raise FileNotFoundError(
                    f"Filesystem repository '{location.name}' expected at '{root}' does not exist."
                )
            return root.resolve()
        if location.kind == "git":
            return self._ensure_git_checkout(location)
        if location.kind == "http":
            url = location.location.rstrip("/")
            return url
        raise ValueError(f"Unknown repository type '{location.kind}'.")

    def _ensure_git_checkout(self, location: RepositoryLocation) -> Path:
        repo_dir = (self._cache_root / location.name).expanduser()
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        if not repo_dir.exists():
            self._run_git(["clone", location.location, str(repo_dir)])
        else:
            self._run_git(["-C", str(repo_dir), "fetch", "--all", "--tags", "--prune"])
        if location.reference:
            ref = location.reference
            self._run_git(["-C", str(repo_dir), "checkout", ref])
            try:
                self._run_git(["-C", str(repo_dir), "pull", "--ff-only"])
            except RuntimeError:
                # Likely on a tag or detached commit; ignore pull failures.
                pass
        return repo_dir.resolve()

    def _run_git(self, args: list[str]) -> None:
        command = ["git", *args]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Git command '{' '.join(command)}' failed with exit code {result.returncode}: {result.stderr.strip()}"
            )

    def _relative_from_parsed(self, parsed: urllib.parse.ParseResult) -> Path:
        segments = []
        if parsed.netloc:
            segments.append(parsed.netloc)
        if parsed.path:
            segments.append(parsed.path.lstrip("/"))
        relative = "/".join(segment for segment in segments if segment)
        if not relative:
            raise ValueError("Repository identifiers must include a relative path.")
        rel_path = Path(relative)
        if rel_path.is_absolute() or any(part == ".." for part in rel_path.parts):
            raise ValueError("Relative paths cannot escape the repository root.")
        return rel_path

    def _normalise_relative(self, value: str) -> Path:
        rel = Path(value)
        if rel.is_absolute() or any(part == ".." for part in rel.parts):
            raise ValueError(f"Relative path '{value}' cannot contain '..' or be absolute.")
        return rel

    def _join_url(self, base: str, relative: str) -> str:
        base = base.rstrip("/") + "/"
        relative = relative.lstrip("/")
        return urllib.parse.urljoin(base, relative)

    def _download_url(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        suggested_name: Optional[str] = None,
    ) -> Path:
        if self._temp_dir is None:
            raise RuntimeError("ResourceResolver must be entered before downloading files.")
        request = urllib.request.Request(url)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        with contextlib.closing(urllib.request.urlopen(request)) as response:
            data = response.read()
        name = suggested_name or Path(urllib.parse.urlparse(url).path).name or "resource"
        safe_name = name.replace("/", "_")
        target = self._temp_dir / f"{uuid.uuid4().hex}_{safe_name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target


__all__ = ["ResourceResolver"]
