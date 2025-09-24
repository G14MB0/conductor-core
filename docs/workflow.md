# Conductor End-to-End Workflow Guide

This document walks through deploying the Conductor CLI on a Linux server using
Docker (or Docker Compose), configuring remote assets, and executing a flow from
a shell session.

> Substitute placeholders such as `your-dockerhub-namespace` and repository URLs
> with your actual values.

## 1. Prerequisites

- Linux host with Docker Engine = 24.x (and optionally Docker Compose v2).
- Outbound network access to Docker Hub (or your registry) and to any remote
  repositories that hold flows, payloads, or node code.
- SSH or terminal access to the server.

Optional but recommended:

- A dedicated folder on the host (e.g. `/srv/conductor`) to persist
  configuration and cache checkouts of remote repositories.

## 2. Pull the container image

```bash
sudo docker pull your-dockerhub-namespace/conductor:latest
```

If you maintain versioned tags, replace `latest` with a specific release.

## 3. Prepare host directories

```bash
sudo mkdir -p /srv/conductor/config
sudo mkdir -p /srv/conductor/cache
sudo chown -R $USER:$USER /srv/conductor
```

- `/srv/conductor/config` stores your global configuration file(s).
- `/srv/conductor/cache` persists git checkouts and downloaded assets when the
  container restarts. (This directory will be mounted at `/root/.conductor`.)

## 4. Author the global configuration

Create `/srv/conductor/config/global.json` with the runtime settings. The
example below shows how to declare remote repositories, dependencies, and other
options.

```jsonc
{
  "env": {
    "EXAMPLE_FLAG": "enabled"
  },
  "dependencies": ["requests==2.31.0"],
  "remote_logging": {
    "target": "https://logs.example.com/ingest",
    "method": "POST",
    "headers": {
      "X-API-Key": "REPLACE_ME"
    },
    "enabled": true,
    "verify": true
  },
  "shared_state": {
    "start_invocations": 0
  },
  "resource_locations": {
    "flows": {
      "type": "git",
      "url": "https://github.com/acme/conductor-assets.git",
      "reference": "main",
      "subpath": "flows"
    },
    "payloads": {
      "type": "http",
      "url": "https://assets.example.com/conductor/payloads"
    }
  },
  "code_locations": {
    "nodes": {
      "type": "git",
      "url": "https://github.com/acme/conductor-nodes.git",
      "reference": "prod",
      "subpath": "src"
    }
  },
  "resource_cache_dir": "/root/.conductor/sources"
}
```

Key fields:

- `dependencies`: pip packages installed by the container entrypoint before the
  flow runs (honours `CONDUCTOR_PIP_EXTRA_ARGS` for custom indexes or auth).
- `remote_logging`: ship structured events to an external endpoint. Provide a
  `target` URL, optional `method`/`headers`, and toggle TLS verification with
  `verify`. Leave `enabled` as `true` to activate remote delivery.
- `resource_locations`: alias remote flows/payloads. Supported types:
  - `filesystem`: absolute path on the container filesystem (`/etc/conductor` is
    mounted read-only by default).
  - `git`: cloned into `/root/.conductor/sources/<name>` and optionally checked
    out at a specific branch, tag, or commit (`reference`).
  - `http`: downloaded via HTTPS/HTTP/FTP. Provide `headers` to include auth
    tokens when required.
- `code_locations`: same structure as `resource_locations`, and each location is
  added to `sys.path` so your inline/process nodes can resolve their modules.

### Private repositories

- **Git over HTTPS:** embed a limited-scope personal access token in the URL,
  e.g. `https://x-access-token:<token>@github.com/acme/private-nodes.git`.
- **Git over SSH:** mount an SSH key directory into the container and set
  `GIT_SSH_COMMAND` via `env` in the global config or Compose file. Ensure the
  key has read-only access.
- **Authenticated HTTP endpoints:** include `"headers": {"Authorization": "Bearer <token>"}`
  on the repository entry.

## 5. Organise flow and payload sources

You have two main options:

1. **Bundled with the container:** place flow definitions and payload seeds
   under `/srv/conductor/config/flows` (or similar) and refer to them with a
   `filesystem` repository, e.g. `"flows": {"type": "filesystem", "path": "/etc/conductor/flows"}`.
2. **Remote repository:** commit flows and payloads to a git or HTTP location as
   shown in the `resource_locations` example. Update your CI/CD pipeline to push
   changes to those repos so the runtime always pulls the latest definition.

Node implementations should live in `code_locations`. For git repositories,
arrange the package structure so importing the callable path in your flow
configuration works once the repo is added to `sys.path` (e.g. `src/conductor_nodes/foo.py`).

### Node repository layout example

A typical git repository for code locations might look like:

```text
conductor-nodes/
|-- pyproject.toml
|-- src/
|   |-- conductor_nodes/
|       |-- __init__.py
|       |-- common.py
|       `-- flows/
|           |-- __init__.py
|           `-- order_processing.py
`-- README.md
```

In your flow definition you would then reference callables such as
`conductor_nodes.flows.order_processing:route_order`. Ensure `pyproject.toml` or
`setup.cfg` declares the package under `[project]`/`[tool.setuptools]` so local
testing mirrors the runtime import path. Any packages required by the repo
should be listed in the global configuration `dependencies` or handled via your
deployment pipeline.\n\n
## 6. Run with Docker Compose (recommended)

Copy the provided template and adjust values:

```bash
cp deploy/docker-compose.yaml /srv/conductor/docker-compose.yaml
cd /srv/conductor
```

Review `docker-compose.yaml` and update:

- `image`: point to your registry tag.
- `CONDUCTOR_GLOBAL_CONFIG`: absolute path inside the container
  (`/etc/conductor/global.json`) for the mounted file.
- `command`: arguments forwarded to `python -m conductor.cli` by the entrypoint.
- `volumes`: ensure `./config:/etc/conductor:ro` matches the host directory; the
  cache volume keeps cloned repos between restarts.

Start the service:

```bash
sudo docker compose up -d
```

Follow logs:

```bash
sudo docker compose logs -f conductor
```

Updating flows or code typically requires pushing to the remote repository and
restarting the service to pick up new commits:

```bash
sudo docker compose restart conductor
```

## 7. One-off execution with docker run

For ad-hoc runs without Compose:

```bash
sudo docker run --rm \
  -v /srv/conductor/config:/etc/conductor:ro \
  -v /srv/conductor/cache:/root/.conductor \
  -e CONDUCTOR_GLOBAL_CONFIG=/etc/conductor/global.json \
  your-dockerhub-namespace/conductor:latest \
  run --flow flows://order-routing.json --payload-file payloads://order-42.json
```

This command mounts the configuration, persists cache state, and executes the
`run` subcommand using the aliases defined earlier.

## 8. Executing from an interactive shell

Once the container is running (via Compose or `docker run`), you can log into the
host and trigger flows manually:

```bash
# Ensure the container is up (Compose example)
cd /srv/conductor
sudo docker compose exec conductor bash

# Inside the container shell
python -m conductor.cli run \
  --global-config /etc/conductor/global.json \
  --flow flows://order-routing.json \
  --payload-file payloads://order-42.json \
  --trace-file /tmp/order-trace.json
```

The resolver fetches remote artefacts automatically and adds `code_locations` to
`sys.path` on the fly. Inspect traces or state snapshots as needed.

## 9. Maintenance tips

- **Dependency updates:** edit the `dependencies` array, then restart the
  container. The entrypoint installs packages every time, so pin versions to
  avoid surprises.
- **Cache hygiene:** purge `/srv/conductor/cache` if you need a clean checkout.
- **Credentials:** keep tokens and SSH keys outside the repository; mount them
  via Compose secrets or environment variables. Restrict permissions to read-only.
- **Monitoring:** use `--print-trace` or write traces to mounted directories to
  aggregate logs.

Following these steps gives you a reproducible path from a clean Linux host to a
fully configured Conductor deployment that sources flows and code from remote
locations and installs runtime dependencies automatically.






