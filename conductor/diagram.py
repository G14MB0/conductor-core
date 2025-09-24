"""Helpers for rendering flow diagrams and summarising traces."""
from __future__ import annotations

import json
import textwrap
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .config import FlowConfig, NodeDefinition
from .execution import ExecutionTrace

_MAX_LABEL_LENGTH = 10000
_WRAP_WIDTH = 60
_NODE_BASE_WIDTH = 260
_NODE_METADATA_WIDTH = 420
_CHAR_PIXEL_WIDTH = 9
_NODE_HORIZONTAL_PADDING = 60


def _format_value(value: Any, *, max_length: int = _MAX_LABEL_LENGTH, wrap_width: int = _WRAP_WIDTH) -> str:
    if value is None:
        text = "None"
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, indent=2)
    else:
        text = str(value)
    text = text.replace('"', "'")
    wrapped_lines: List[str] = []
    source_lines = text.splitlines() or [text]
    for line in source_lines:
        if len(line) <= wrap_width:
            wrapped_lines.append(line)
        else:
            wrapped_lines.extend(
                textwrap.wrap(
                    line,
                    wrap_width,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
    formatted = "\n".join(wrapped_lines)
    if max_length and len(formatted) > max_length:
        formatted = formatted[: max_length - 3] + "..."
    return formatted


def _format_block(prefix: str, value: Any) -> str:
    formatted = _format_value(value)
    if "\n" not in formatted:
        return f"{prefix}: {formatted}"
    first_line, *rest = formatted.splitlines()
    lines = [f"{prefix}: {first_line}"]
    lines.extend(f"    {line}" for line in rest)
    return "\n".join(lines)


def _format_label(chunks: List[str]) -> Tuple[str, int]:
    html_lines: List[str] = []
    max_length = 0
    for chunk in chunks:
        for raw_line in chunk.splitlines():
            stripped = raw_line.lstrip()
            leading = len(raw_line) - len(stripped)
            if stripped:
                indent = "&nbsp;" * leading
                html_lines.append(f"{indent}{stripped}")
                max_length = max(max_length, leading + len(stripped))
            else:
                html_lines.append("")
    rendered: List[str] = []
    for line in html_lines:
        if line == "":
            if not rendered or rendered[-1] != "<br/>":
                rendered.append("<br/>")
        else:
            rendered.append(line)
    while rendered and rendered[0] == "<br/>":
        rendered.pop(0)
    while rendered and rendered[-1] == "<br/>":
        rendered.pop()
    return "<br/>".join(rendered), max_length


def summarise_trace(trace: ExecutionTrace) -> Dict[str, Any]:
    node_data: Dict[str, Dict[str, Any]] = {}
    edge_counts: Counter[Tuple[str, str, str]] = Counter()
    order: List[Dict[str, Any]] = []

    for event in trace.events:
        stats = node_data.setdefault(
            event.node_id,
            {
                "count": 0,
                "total_duration": 0.0,
                "last_duration": 0.0,
                "last_status": None,
                "last_input": None,
                "last_output": None,
                "last_metadata": None,
                "statuses": defaultdict(int),
            },
        )
        stats["count"] += 1
        stats["total_duration"] += event.duration
        stats["last_duration"] = event.duration
        stats["last_status"] = event.status
        stats["last_input"] = (event.node_input or {}).get("data") if event.node_input else None
        stats["last_output"] = (event.node_output or {}).get("data")
        stats["last_metadata"] = (event.node_output or {}).get("metadata")
        stats["statuses"][event.status] += 1

        order.append(
            {
                "index": event.index,
                "node_id": event.node_id,
                "status": event.status,
                "duration": event.duration,
            }
        )

        for successor in event.successors:
            edge_counts[(event.node_id, successor, event.status)] += 1

    nodes_summary: Dict[str, Any] = {}
    for node_id, data in node_data.items():
        average_duration = data["total_duration"] / data["count"] if data["count"] else 0.0
        nodes_summary[node_id] = {
            "count": data["count"],
            "statuses": dict(data["statuses"]),
            "last_status": data["last_status"],
            "last_duration": data["last_duration"],
            "average_duration": average_duration,
            "last_input": data["last_input"],
            "last_output": data["last_output"],
            "last_metadata": data["last_metadata"],
        }

    edges_summary = [
        {"source": source, "target": target, "status": status, "count": count}
        for (source, target, status), count in edge_counts.items()
    ]
    edges_summary.sort(key=lambda item: (item["source"], item["target"], item["status"]))

    duration = None
    if trace.finished_at is not None:
        duration = trace.finished_at - trace.started_at

    return {
        "flow_name": trace.flow_name,
        "events": len(trace.events),
        "started_at": trace.started_at,
        "finished_at": trace.finished_at,
        "duration": duration,
        "nodes": nodes_summary,
        "edges": edges_summary,
        "order": order,
    }


def _build_node_label(
    node_id: str,
    definition: NodeDefinition,
    stats: Optional[Dict[str, Any]],
    include_metadata: bool,
) -> Tuple[str, int]:
    lines: List[str] = [node_id]
    if definition.name and definition.name != node_id:
        lines.append(definition.name)
    if include_metadata:
        lines.append(f"executor: {definition.executor}")
        if stats:
            lines.append(f"runs: {stats['count']}")
            if stats.get("last_status"):
                lines.append(f"last: {stats['last_status']}")
            if stats.get("last_duration") is not None:
                lines.append(f"dur: {stats['last_duration']:.3f}s")
            if stats.get("last_input") is not None:
                lines.append(_format_block("in", stats['last_input']))
            if stats.get("last_output") is not None:
                lines.append(_format_block("out", stats['last_output']))
            if stats.get("last_metadata"):
                lines.append(_format_block("meta", stats['last_metadata']))
    return _format_label(lines)


def render_mermaid_diagram(
    flow: FlowConfig,
    trace: Optional[ExecutionTrace] = None,
    *,
    include_metadata: bool = False,
    title: Optional[str] = None,
) -> str:
    summary = summarise_trace(trace) if trace else None
    node_stats = summary["nodes"] if summary else {}
    executed_nodes = set(node_stats.keys())
    executed_edges = {
        (edge["source"], edge["target"], edge["status"]): edge["count"]
        for edge in summary["edges"]
    } if summary else {}

    lines: List[str] = []
    if title:
        lines.append(f"%% {title}")
    lines.append(f"%% Flow: {flow.name}")
    if summary:
        lines.append(f"%% Executed events: {summary['events']}")
        order_preview = " -> ".join(f"{item['node_id']}({item['status']})" for item in summary["order"])
        if order_preview:
            lines.append(f"%% Trace order: {order_preview}")

    lines.append("graph TD")

    for node_id, definition in flow.nodes.items():
        stats = node_stats.get(node_id)
        label, max_len = _build_node_label(node_id, definition, stats, include_metadata)
        base_width = _NODE_METADATA_WIDTH if include_metadata and stats else _NODE_BASE_WIDTH
        adjusted_width = max(base_width, max_len * _CHAR_PIXEL_WIDTH + _NODE_HORIZONTAL_PADDING)
        content = f"<div style=\"width:{int(adjusted_width - 20)}px;text-align:left\">{label}</div>"
        content = content.replace("\"", "&quot;")
        lines.append(f"    {node_id}[\"{content}\"]")
        lines.append(f"    style {node_id} width:{int(adjusted_width)}px")

    edge_index = 0
    styled_edges: List[Tuple[int, int]] = []
    for definition in flow.nodes.values():
        transitions = definition.transitions or {}
        for status, successors in transitions.items():
            if not successors:
                continue
            label = f"|{status}|" if status else ""
            for successor in successors:
                if label:
                    line = f"    {definition.id} --{label}--> {successor}"
                else:
                    line = f"    {definition.id} --> {successor}"
                lines.append(line)
                key = (definition.id, successor, status)
                if key in executed_edges:
                    styled_edges.append((edge_index, executed_edges[key]))
                edge_index += 1

    if executed_nodes:
        lines.append("    classDef executed fill:#bbf7d0,stroke:#15803d,stroke-width:2px,text-align:left;")
        nodes_csv = ','.join(sorted(executed_nodes))
        lines.append(f"    class {nodes_csv} executed;")

    if flow.start:
        lines.append("    classDef start fill:#dbeafe,stroke:#1d4ed8,stroke-width:2px;")
        start_csv = ','.join(flow.start)
        lines.append(f"    class {start_csv} start;")

    for index, count in styled_edges:
        lines.append(f"    linkStyle {index} stroke:#16a34a,stroke-width:3px;")
        if count > 1:
            lines.append(f"    %% edge {index} executed {count} times")

    return "\n".join(lines)

