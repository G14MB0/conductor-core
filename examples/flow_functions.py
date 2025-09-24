"""Example node implementations for demonstration and testing."""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from conductor.global_state import get_global_state
from conductor.node import NodeInput, NodeOutput


async def starter(node_input: NodeInput) -> NodeOutput:
    """Initial node that stores the inbound payload in the global state."""

    state = get_global_state()
    payload = dict(node_input.data or {})
    state.set_sync("last_payload", payload)
    counter = state.get_sync("start_invocations", 0) + 1
    state.set_sync("start_invocations", counter)
    await asyncio.sleep(0.05)
    payload["invocations"] = counter
    return NodeOutput(data=payload)


def branching(node_input: NodeInput) -> NodeOutput:
    """Decide which branch to execute based on a numeric value."""

    payload: Dict[str, Any] = dict(node_input.data or {})
    value = int(payload.get("number", 0))
    status = "even" if value % 2 == 0 else "odd"
    payload["parity"] = status
    return NodeOutput(status=status, data=payload)


def intensive(node_input: NodeInput) -> NodeOutput:
    """Simple CPU intensive task executed in a separate process."""

    payload: Dict[str, Any] = dict(node_input.data or {})
    number = int(payload.get("number", 0))
    total = sum(range(number + 1))
    payload["total"] = total
    return NodeOutput(data=payload)


def finalizer(node_input: NodeInput) -> NodeOutput:
    """Return the aggregated information and the final shared state."""

    state = get_global_state()
    snapshot = state.to_dict()
    result = {
        "input": node_input.data,
        "metadata": node_input.metadata,
        "state": snapshot,
    }
    return NodeOutput(data=result)

