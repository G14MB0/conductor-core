"""Inline/process demo callables for the dashboard."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from conductor.node import NodeInput, NodeOutput


def inline_prepare(node_input: NodeInput) -> NodeOutput:
    """Inline node that prepares the payload for the process worker."""

    logger = logging.getLogger("conductor.node.inline_prepare")
    payload = dict(node_input.data or {})
    if "number" not in payload:
        payload["number"] = 7
    payload["prepared_at"] = datetime.utcnow().isoformat() + "Z"
    logger.info("Inline prepare received number=%s", payload["number"])
    return NodeOutput(status="ready", data=payload)


def process_calculate(node_input: NodeInput) -> NodeOutput:
    """Process node that performs an expensive calculation."""

    logger = logging.getLogger("conductor.node.process_calculate")
    payload = dict(node_input.data or {})
    number = int(payload.get("number", 0))
    logger.warning("Process calculate starting computation for number=%s", number)
    total = sum(range(number + 1))
    payload["total"] = total
    payload["computed_at"] = datetime.utcnow().isoformat() + "Z"
    log_records: List[str] = [
        f"process_calculate started for number={number}",
        f"process_calculate produced total={total}",
    ]
    metadata = {"log_records": log_records}
    return NodeOutput(status="success", data=payload, metadata=metadata)


def inline_report(node_input: NodeInput) -> NodeOutput:
    """Inline node that reports the process result and emits dashboard-friendly logs."""

    logger = logging.getLogger("conductor.node.inline_report")
    payload = dict(node_input.data or {})
    metadata = dict(node_input.metadata)
    logs = metadata.get("log_records") or []
    for entry in logs:
        logger.info("Process log -> %s", entry)
    logger.info("Inline report sees total=%s", payload.get("total"))
    payload["reported_at"] = datetime.utcnow().isoformat() + "Z"
    return NodeOutput(status="success", data=payload, metadata={"forwarded_logs": len(logs)})


__all__ = ["inline_prepare", "process_calculate", "inline_report"]
