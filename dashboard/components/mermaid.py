"""Reusable UI components for the Streamlit dashboard."""
from __future__ import annotations

import html
import json
from typing import Optional

import streamlit as st
from streamlit.components.v1 import html as components_html

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"


def mermaid_diagram(
    mermaid_source: str,
    *,
    height: int = 600,
    theme: str = "default",
    key: Optional[str] = None,
    download: bool = True,
) -> None:
    """Render a Mermaid diagram within Streamlit using an embedded component."""

    if not mermaid_source.strip():
        st.info("Nessun diagramma disponibile.")
        return

    safe_source = html.escape(mermaid_source)
    config = json.dumps({"startOnLoad": True, "theme": theme})
    components_html(
        f"""
        <div class="mermaid">{safe_source}</div>
        <script src="{MERMAID_CDN}"></script>
        <script>mermaid.initialize({config});</script>
        """,
        height=height,
        scrolling=True,
    )
    if download:
        download_key = key or f"mermaid-download-{abs(hash(mermaid_source))}"
        st.download_button(
            "Scarica diagramma (Mermaid)",
            mermaid_source,
            file_name="flow.mmd",
            mime="text/plain",
            key=download_key,
        )


__all__ = ["mermaid_diagram"]
