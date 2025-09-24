"""Container entrypoint that installs runtime dependencies before invoking the CLI."""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from .config import GlobalConfig, load_global_config


def _extract_cli_global_config(argv: List[str]) -> Tuple[Optional[int], Optional[Path]]:
    for index, value in enumerate(argv):
        if value == "--global-config" and index + 1 < len(argv):
            return index, Path(argv[index + 1])
        if value.startswith("--global-config="):
            path_value = value.split("=", 1)[1]
            return index, Path(path_value)
    return None, None


def _load_global_config_from_sources(
    cli_path: Optional[Path], env_path: Optional[str], inline_json: Optional[str]
) -> Tuple[Optional[Path], GlobalConfig, Optional[Path]]:
    temp_file: Optional[Path] = None
    if cli_path:
        return cli_path, load_global_config(cli_path), temp_file
    if env_path:
        config_path = Path(env_path).expanduser()
        return config_path, load_global_config(config_path), temp_file
    if inline_json:
        data = json.loads(inline_json)
        config = GlobalConfig.from_mapping(data)
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", prefix="conductor_config_", delete=False)
        json.dump(data, handle)
        handle.flush()
        handle.close()
        temp_file = Path(handle.name)
        return temp_file, config, temp_file
    return None, GlobalConfig.from_mapping({}), temp_file


def _install_dependencies(packages: List[str]) -> None:
    if not packages:
        return
    extra_args = os.environ.get("CONDUCTOR_PIP_EXTRA_ARGS")
    args: List[str] = [sys.executable, "-m", "pip", "install", "--no-cache-dir"]
    if extra_args:
        args.extend(extra_args.split())
    args.extend(packages)
    process = subprocess.run(args)
    if process.returncode != 0:
        raise RuntimeError(
            f"Dependency installation failed with exit code {process.returncode}: {' '.join(packages)}"
        )


def main(argv: Optional[List[str]] = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)

    cli_index, cli_path = _extract_cli_global_config(args)
    env_path = os.environ.get("CONDUCTOR_GLOBAL_CONFIG")
    inline_json = os.environ.get("CONDUCTOR_GLOBAL_CONFIG_JSON") or os.environ.get(
        "CONDUCTOR_GLOBAL_CONFIG_INLINE"
    )

    config_path, config, temp_file = _load_global_config_from_sources(cli_path, env_path, inline_json)
    if temp_file:
        atexit.register(temp_file.unlink, missing_ok=True)

    if config.dependencies:
        _install_dependencies(config.dependencies)

    if config_path is not None and cli_index is None:
        args.extend(["--global-config", str(config_path)])

    from .cli import main as cli_main

    cli_main(args)


if __name__ == "__main__":  # pragma: no cover
    main()
