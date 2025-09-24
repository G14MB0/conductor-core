"""Command line interface for running conductor flows."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from .config import GlobalConfig, load_flow_config, load_global_config
from .diagram import render_mermaid_diagram, summarise_trace
from .execution import ExecutionTrace, FlowExecutor
from .resources import ResourceResolver
from .logging_utils import configure_logging


def _load_payload(
    payload: Optional[str],
    payload_file: Optional[str],
    resolver: Optional[ResourceResolver],
) -> Any:
    if payload and payload_file:
        raise ValueError("Specify either --payload or --payload-file, not both.")
    if payload:
        return json.loads(payload)
    if payload_file:
        file_path = resolver.resolve_file(payload_file) if resolver else Path(payload_file)
        text = Path(file_path).read_text()
        return json.loads(text)
    return None



def _load_trace(path: Optional[str], resolver: Optional[ResourceResolver]) -> Optional[ExecutionTrace]:
    if not path:
        return None
    file_path = resolver.resolve_file(path) if resolver else Path(path)
    data = json.loads(Path(file_path).read_text())
    return ExecutionTrace.from_dict(data)





@contextlib.contextmanager
def _inject_code_paths(paths: Sequence[Path]) -> None:
    added: list[str] = []
    try:
        for path in paths:
            location = str(path.resolve())
            if location not in sys.path:
                sys.path.insert(0, location)
                added.append(location)
        yield
    finally:
        for location in reversed(added):
            try:
                sys.path.remove(location)
            except ValueError:  # pragma: no cover - defensive
                pass


async def _run_flow(args: argparse.Namespace) -> None:
    global_config = load_global_config(args.global_config) if args.global_config else GlobalConfig.from_mapping({})

    with ResourceResolver(global_config) as resources:
        code_paths = list(resources.code_paths().values())
        with _inject_code_paths(code_paths):
            flow_path = resources.resolve_file(args.flow)
            flow_config = load_flow_config(flow_path)

            log_level = getattr(logging, args.log_level.upper(), logging.INFO)
            logger = configure_logging(global_config, level=log_level)

            payload = _load_payload(args.payload, args.payload_file, resources)

            async with FlowExecutor(flow_config, global_config, logger=logger) as executor:
                results = await executor.run(initial_payload=payload)
                if args.print_results:
                    print(json.dumps([result.to_dict() for result in results], indent=2))
                if args.print_state:
                    print(json.dumps(executor.global_state.to_dict(), indent=2))

                trace = executor.trace.to_dict() if executor.trace else None
                if args.trace_file:
                    if trace is None:
                        raise RuntimeError("Trace data is not available for this run.")
                    Path(args.trace_file).write_text(json.dumps(trace, indent=2))
                if args.print_trace and trace:
                    print(json.dumps(trace, indent=2))





def _render_diagram(args: argparse.Namespace) -> None:
    global_config = load_global_config(args.global_config) if args.global_config else GlobalConfig.from_mapping({})

    with ResourceResolver(global_config) as resources:
        code_paths = list(resources.code_paths().values())
        with _inject_code_paths(code_paths):
            flow_path = resources.resolve_file(args.flow)
            flow_config = load_flow_config(flow_path)
            trace: Optional[ExecutionTrace] = _load_trace(args.trace_file, resources)

            if args.format != "mermaid":
                raise ValueError(f"Unsupported diagram format '{args.format}'.")

            diagram = render_mermaid_diagram(
                flow_config,
                trace=trace,
                include_metadata=args.include_metadata,
                title=args.title,
            )

            if args.output:
                Path(args.output).write_text(diagram)
            else:
                print(diagram)

            if args.print_summary and trace:
                summary = summarise_trace(trace)
                print(json.dumps(summary, indent=2))





def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute conductor flows defined in configuration files.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Execute a flow")
    run_parser.add_argument("--flow", required=True, help="Path to the flow configuration file (JSON/YAML/TOML).")
    run_parser.add_argument("--global-config", help="Path to the global configuration file (JSON/YAML/TOML).")
    run_parser.add_argument("--payload", help="Inline JSON string to pass as the initial payload for the flow.")
    run_parser.add_argument("--payload-file", help="Path to a JSON file to use as the initial payload.")
    run_parser.add_argument("--log-level", default="INFO", help="Logging level (default: INFO).")
    run_parser.add_argument(
        "--print-results",
        dest="print_results",
        action="store_true",
        default=True,
        help="Print the final results of the flow in JSON format (default: enabled).",
    )
    run_parser.add_argument(
        "--no-print-results",
        dest="print_results",
        action="store_false",
        help="Disable printing the final results.",
    )
    run_parser.add_argument(
        "--print-state",
        action="store_true",
        help="Print the shared global state after flow execution.",
    )
    run_parser.add_argument(
        "--print-trace",
        action="store_true",
        help="Print the execution trace in JSON format once the flow completes.",
    )
    run_parser.add_argument(
        "--trace-file",
        help="Write the execution trace to the provided path as JSON.",
    )
    run_parser.set_defaults(func=_run_flow)

    diagram_parser = subparsers.add_parser("diagram", help="Render a diagram for the flow definition")
    diagram_parser.add_argument("--flow", required=True, help="Path to the flow configuration file (JSON/YAML/TOML).")
    diagram_parser.add_argument("--global-config", help="Path to the global configuration file (JSON/YAML/TOML).")
    diagram_parser.add_argument("--trace-file", help="Path to an execution trace JSON file to highlight the executed path.")
    diagram_parser.add_argument(
        "--format",
        default="mermaid",
        choices=["mermaid"],
        help="Output format for the diagram (default: mermaid).",
    )
    diagram_parser.add_argument(
        "--output",
        help="Write the generated diagram to the specified path instead of stdout.",
    )
    diagram_parser.add_argument(
        "--include-metadata",
        action="store_true",
        help="Embed execution statistics (when available) inside node labels.",
    )
    diagram_parser.add_argument(
        "--title",
        help="Optional title to embed in the generated diagram.",
    )
    diagram_parser.add_argument(
        "--print-summary",
        action="store_true",
        help="Print aggregated execution statistics when a trace is provided.",
    )
    diagram_parser.set_defaults(func=_render_diagram)

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return
    func = args.func
    try:
        if asyncio.iscoroutinefunction(func):
            asyncio.run(func(args))
            return
        result = func(args)
        if asyncio.iscoroutine(result):
            asyncio.run(result)
    except KeyboardInterrupt:  # pragma: no cover - CLI convenience
        pass


if __name__ == "__main__":  # pragma: no cover
    main()

