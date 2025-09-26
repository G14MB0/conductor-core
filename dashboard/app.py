"""Streamlit entrypoint for the Conductor operations dashboard."""
from __future__ import annotations

import streamlit as st


def main() -> None:
    """Redirect to the Deployment page so navigation only shows real sections."""

    try:
        st.switch_page("pages/deployment.py")
    except Exception:  # pragma: no cover - fallback for older Streamlit versions
        st.write("Navigazione non disponibile: aggiorna Streamlit alla versione 1.25 o successiva.")


if __name__ == "__main__":  # pragma: no cover - Streamlit entrypoint
    main()
