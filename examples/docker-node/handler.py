#!/usr/bin/env python3
"""Example Docker node entrypoint for conductor flows."""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict


def _load_input() -> Dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {"data": {}, "metadata": {}}
    return json.loads(raw)


def _build_output(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload.get("data") or {})
    metadata = dict(payload.get("metadata") or {})
    number = data.get("number")
    total = data.get("total")
    if isinstance(total, (int, float)):
        data["docker_total"] = total * 2
    if isinstance(number, (int, float)):
        data["docker_number_squared"] = number * number
    data["docker_processed"] = True
    metadata.update(
        {
            "handled_at": time.time(),
            "handler": "examples/docker-node/handler.py",
        }
    )
    return {
        "status": "success",
        "data": data,
        "metadata": metadata,
    }


def main() -> None:
    payload = _load_input()
    result = _build_output(payload)
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
