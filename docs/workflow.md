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

Create `/srv/conductor/config/global.json` with the runtime defaults shared by
all flows. The global file no longer carries repository information or
per-deployment secrets - those live alongside each flow.

```jsonc
{
  "env": {
    "EXAMPLE_FLAG": "enabled"
  },
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
  "process_pool_size": 2,
  "max_concurrency": 4
}
```

Key fields:

- env: environment variables exported before the flow starts.
- remote_logging: ship structured events to an external endpoint. Provide a target URL, optional method/headers, and toggle TLS verification with `verify`. Leave `enabled` as `true` to activate remote delivery.
- shared_state: initial values written into the shared state proxy. Nodes can mutate these values via `conductor.global_state.get_global_state()`.
- process_pool_size / max_concurrency: tune the execution engine when running process-based or highly parallel flows. Set either value to `null` to let Conductor choose defaults automatically.

All artefact locations, container registries, and secrets are defined in the
flow-specific runtime configuration described in the next section.

## 5. Organise flow and payload sources

Author a runtime configuration for each flow under `/srv/conductor/config/flows`
(or a similar directory). This file complements the flow definition by describing
where assets live and which credentials the orchestrator should use.

```jsonc
{
  "resource_locations": {
    "flows": {
      "type": "git",
      "location": "https://github.com/acme/conductor-assets.git",
      "reference": "main",
      "subpath": "flows",
      "token_secret": "git-token"
    },
    "payloads": {
      "type": "http",
      "location": "https://assets.example.com/conductor/payloads"
    }
  },
  "code_locations": {
    "nodes": {
      "type": "git",
      "location": "https://github.com/acme/conductor-nodes.git",
      "reference": "prod",
      "subpath": "src",
      "token_secret": "git-token"
    }
  },
  "container_registries": {
    "dockerhub": {
      "url": "https://registry-1.docker.io",
      "token_secret": "dockerhub-token"
    }
  },
  "secrets": {
    "git-token": {"type": "git", "env": "GITHUB_TOKEN"},
    "dockerhub-token": {"type": "docker", "value": "<personal-access-token>"}
  },
  "flow_definition": "flows/order-routing.json",
  "callables": [
    "nodes/order_routing.py"
  ]
}
```

Runtime configuration tips:

- resource_locations: alias remote flow definitions or payload seeds. Supported types are `filesystem`, `git`, and `http`. Secrets referenced via `token_secret` or `password_secret` resolve from the secrets map.
- code_locations: same structure as `resource_locations`; each entry is added to `sys.path` during execution so inline/process nodes can import their modules.
- resource_cache_dir: override the directory used to cache cloned repositories (default `~/.conductor/sources`).
- container_registries: base URLs for Docker images referenced by image fields.
- secrets: resolve sensitive values via `value`, `env`, or `file`. The `type` attribute is informational and helps with tooling.
- flow_definition: relative path to the flow file within a repository when you want the orchestrator to resolve it dynamically.
- callables: optional hints pointing to Python modules or packages that should be added to `sys.path`.

Arrange node repositories so importing the callable path in your flow definition
works once the repo is added to `sys.path`. For example:

```text
conductor-nodes/
|-- pyproject.toml
|-- src/
|   |-- conductor_nodes/
|       |-- __init__.py
|       |-- common.py
|       |-- flows/
|           |-- __init__.py
|           |-- order_processing.py
|-- README.md
```

You can keep flow and payload files alongside the runtime configuration (use a `filesystem` alias) or push them to git/HTTP locations. Update your CI/CD pipeline to publish new commits so the orchestrator always pulls the latest versions.

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
  run --flow flows://order-routing.json --runtime-config /etc/conductor/flows/order-routing.runtime.json --payload-file payloads://order-42.json
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
  --runtime-config /etc/conductor/flows/order-routing.runtime.json \
  --flow flows://order-routing.json \
  --payload-file payloads://order-42.json \
  --trace-file /tmp/order-trace.json
```

The resolver fetches remote artefacts automatically and adds `code_locations` to
`sys.path` on the fly. Inspect traces or state snapshots as needed.

## 9. Maintenance tips

- **Python packages:** bake required dependencies into the container image or install them as part of your deployment pipeline before launching the service.
- **Cache hygiene:** purge `/srv/conductor/cache` if you need a clean checkout.
- **Credentials:** keep tokens and SSH keys outside the repository; mount them
  via Compose secrets or environment variables. Restrict permissions to read-only.
- **Monitoring:** use `--print-trace` or write traces to mounted directories to
  aggregate logs.

Following these steps gives you a reproducible path from a clean Linux host to a
fully configured Conductor deployment that sources flows and code from remote
locations and installs runtime dependencies automatically.
