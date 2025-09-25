"""Git utilities for the dashboard."""
from __future__ import annotations

import subprocess
from typing import Optional, Tuple


def test_git_connection(url: str, reference: Optional[str] = None, *, timeout: int = 15) -> Tuple[bool, str]:
    """Verify that the configured Git repository is reachable."""

    cmd = ["git", "ls-remote", "--heads", "--tags", url]
    if reference:
        cmd.append(reference)
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return False, "Git executable not found on the server."
    except subprocess.TimeoutExpired:
        return False, "Connection timed out."
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        return False, message
    output = completed.stdout.strip()
    if reference and reference not in output:
        return False, f"Reference '{reference}' not found in remote."
    return True, output or "Remote reachable and responding."


__all__ = ["test_git_connection"]
