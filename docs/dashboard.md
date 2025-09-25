# Conductor Dashboard

The Conductor dashboard is a Streamlit application that exposes the main
orchestration capabilities of Conductor through an interactive interface. It
lets you inspect registered flows, trigger ad-hoc executions, configure
schedules, and adjust the global settings that drive your automation.

This guide explains how to run the dashboard locally—either directly with
Python or through Docker Compose—and describes the most important
sections of the UI.

## Prerequisites

* Python 3.11 or later if you plan to run the application locally.
* Docker and Docker Compose v2 for containerised deployments.
* Access to the flows and configuration files that you want to manage. The
  examples below assume the repository root as the working directory.

## Running the dashboard locally

1. Install the dashboard dependencies. The Streamlit app depends on the core
   Conductor package as well as the UI extras listed in
   `dashboard/requirements.txt`.

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   pip install -r dashboard/requirements.txt
   ```

2. Start Streamlit from the repository root:

   ```bash
   streamlit run dashboard/app.py
   ```

3. Open your browser at <http://localhost:8501>. The dashboard will discover
   flows and schedules using the same working directory as the process.

### Environment variables

The dashboard honours the environment variables used by Conductor. Notable
settings include:

* `CONDUCTOR_GLOBAL_CONFIG` – path to a global configuration file loaded when
  the app starts.
* `CONDUCTOR_CODE_LOCATIONS` – additional directories where flow definitions
  can be discovered.

When running locally you can export these variables before launching the app to
customise its behaviour.

## Running the dashboard with Docker Compose

The repository ships with a ready-to-use Docker Compose service that exposes
Streamlit on port 8501. From the repository root:

```bash
cd deploy
docker compose up dashboard
```

The first run builds an image containing the Conductor package and the
Streamlit dependencies. Once the service is up, open
<http://localhost:8501> to access the dashboard.

The Compose service mounts the `deploy/config` directory into the container so
that you can share configuration files between the dashboard and the core
conductor service. Adjust the volume mappings if your flows live elsewhere.

### Customising the service

You can override the following environment variables in `docker-compose.yaml`
if required:

* `STREAMLIT_SERVER_PORT` – change the HTTP port exposed by Streamlit.
* `CONDUCTOR_GLOBAL_CONFIG` – specify the default global configuration file to
  load.

For production deployments consider publishing the container image to a
registry and referencing it in the Compose file instead of building it locally.

## Navigating the UI

The sidebar exposes shortcuts to the major sections of the dashboard:

* **Monitoraggio** – overview of active and recent flow runs.
* **Flow registrati** – inspect registered flows and view their definitions.
* **Scheduler** – manage recurring executions and upcoming triggers.
* **Flow Designer** – design new flows or edit existing ones using the visual
  editor.
* **Global settings** – review and update the global Conductor configuration.

Use the *Aggiorna* button in the sidebar to refresh the runtime state and the
*Reset sessione* action to clear cached data when experimenting.

## Troubleshooting

*If the dashboard cannot discover your flows*, double-check that the process or
container has access to the directories that store the JSON/TOML definitions.

*If Streamlit shows a blank page or reports missing dependencies*, make sure
that the `pip install` steps completed successfully. When using Docker Compose,
rebuild the service with `docker compose build dashboard` to refresh the
image.
