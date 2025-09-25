# Conductor Dashboard

The Conductor dashboard is a Streamlit application that exposes the main
orchestration capabilities of Conductor through an interactive interface. It lets
operators register flows, connect per-deployment assets, schedule executions and
monitor the runtime without leaving the browser.

This guide explains how to launch the dashboard locally or via Docker Compose
and describes the three primary areas of the UI.

## Prerequisites

- Python 3.11 or later if you plan to run the application locally.
- Docker and Docker Compose v2 if you prefer the containerised setup.
- Access to the flows, configuration files and Git repositories that you want to
  manage. The examples below assume the repository root as the working directory.

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

3. Open your browser at <http://localhost:8501>. The dashboard uses the same
   working directory as the process to discover flows and store temporary assets.

### Environment variables

The dashboard honours the environment variables used by Conductor. Notable
settings include:

- `CONDUCTOR_GLOBAL_CONFIG` - path to a global configuration file loaded when
  the app starts.
- `CONDUCTOR_CODE_LOCATIONS` - additional directories where flow definitions
  can be discovered.

Export these variables before launching the app to customise its behaviour.

## Running the dashboard with Docker Compose

The repository ships with a ready-to-use Docker Compose service that exposes
Streamlit on port 8501. From the repository root:

```bash
cd deploy
docker compose up dashboard
```

The first run builds an image containing the Conductor package and the Streamlit
dependencies. Once the service is up, open <http://localhost:8501> to access the
dashboard.

The Compose service mounts the `deploy/config` directory into the container so
you can share configuration files between the dashboard and the core Conductor
service. Adjust the volume mappings if your flows live elsewhere. The dashboard
automatically loads the configuration file pointed to by the
`CONDUCTOR_GLOBAL_CONFIG` environment variable. The default Compose
configuration sets this to `/etc/conductor/global.json`, so edits performed in
the dashboard start from that baseline.

An optional `conductor` service is available under the `cli` profile. It keeps a
shell-friendly container around without executing any flow on startup, which is
useful if you want to run CLI commands alongside the dashboard:

```bash
docker compose --profile cli up dashboard
docker compose run --rm conductor run --flow flows://your-flow.json
```

If you do not enable the profile only the dashboard container will be started.

### Customising the service

You can override the following environment variables in `docker-compose.yaml` if
required:

- `STREAMLIT_SERVER_PORT` - change the HTTP port exposed by Streamlit.
- `CONDUCTOR_GLOBAL_CONFIG` - specify the default global configuration file to
  load. The dashboard reads this value on startup to pre-populate the
  application settings.

For production deployments consider publishing the container image to a registry
and referencing it in the Compose file instead of building it locally.

## Navigating the UI

The sidebar exposes shortcuts to three consolidated areas:

- **Deployment** - register flows from local uploads or Git repositories. Each
  deployment can point to its own global configuration and code/resource
  locations. Uploaded assets are stored under `dashboard/storage/`.
- **Operazioni** - monitor active runs, inspect history, schedule recurring
  executions (interval or cron), trigger manual runs for one or more flows and
  consult the live log viewer. The page supports automatic refresh with a
  configurable interval.
- **Impostazioni** - review and edit the global application settings (remote
  logging, environment variables, shared state defaults, repository locations).
  The same base configuration is reused when building new deployments.

### Deployment workspace

The Deployment tab offers two workflows:

- **File locali**: upload a flow configuration, optionally a global
  configuration and a zip archive containing the flow code or resources. The
  files are saved on the server and referenced through a filesystem code
  location assigned to the deployment.
- **Repository Git**: clone a repository (with optional branch/tag/commit and
  personal access token), pick the flow/global configuration files and select
  one or more directories to treat as code locations. The deployment records the
  commit hash so future executions are deterministic.

Each successful registration returns a summary of the artefacts used and lists
all registered flows with the ability to deregister them.

### Operations workspace

The Operazioni tab provides:

- Real time metrics for active executions, including payload and metadata
  previews, with the option to cancel runs.
- A condensed history of recent runs with final status, timings and errors.
- A scheduler form that supports multi-flow interval or cron schedules, optional
  payload/metadata and immediate kick-off of the first run.
- A manual run form that triggers single or multiple flows either synchronously
  or in the background with customised payloads.
- A log viewer to inspect the orchestrator output filtered by level without leaving the dashboard.

### Application settings

The Impostazioni tab surfaces the same configuration model exposed by the CLI.
It lets you edit environment variables, dependencies, remote logging and
repository locations. The values edited here become the base template used when
creating new deployments from the Deployment tab.

## Storage and security notes

- Local uploads and cloned repositories are stored under
  `dashboard/storage/`. Add this directory to your `.gitignore` (already covered
  in the repository) and monitor its size in long-running environments.
- Git personal access tokens are only used during the clone/fetch step and are
  never persisted to disk or displayed back in the UI.

## Troubleshooting

- If the dashboard cannot discover your flows, ensure the process or container
  has access to the directories that store the JSON/TOML definitions.
- If Streamlit shows a blank page or reports missing dependencies, make sure the
  `pip install` steps completed successfully. When using Docker Compose,
  rebuild the service with `docker compose build dashboard` to refresh the
  image.
