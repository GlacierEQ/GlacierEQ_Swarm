# GlacierEQ Swarm Service

This document describes the executable service implemented by:

- `src/swarm_runtime.py`
- `src/swarm_store.py`
- `src/worker_adapters.py`
- `src/swarm_api.py`
- `Dockerfile`

It is an operating contract, not a claim of an external production deployment.

## Security boundary

API clients **do not supply shell commands**.

Workers are configured by the service operator before startup. A subprocess worker has a fixed `argv`, working directory, timeout, output limit, capability set, and concurrency limit. A submitted task supplies only:

- task id;
- required capability set;
- JSON payload;
- priority;
- retry budget.

The selected fixed worker receives the JSON payload on stdin and must return one JSON value on stdout. Non-JSON success output is treated as worker failure. No shell is used by the adapter.

Authenticated endpoints require a bearer token read from the environment variable named by `bearer_token_env` in the service config. The token is never stored in the repository config.

## Persistent state

The runtime stores:

- content-addressed scheduler snapshots;
- monotonic store revision;
- queued task payloads and payload digests;
- successful task results and result digests;
- worker execution receipts;
- scheduler telemetry inside the snapshot.

SQLite uses WAL mode and a busy timeout. A stale expected revision is rejected rather than silently overwriting newer scheduler state.

`GET /ready` fails readiness when store integrity fails, there are no configured execution adapters, or no active workers remain.

## Example configuration

`examples/swarm-service.json` configures one fixed JSON echo worker and durable state at `/data/swarm.sqlite3`.

Run locally with a non-placeholder secret:

```bash
export GLACIEREQ_SWARM_TOKEN="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
PYTHONPATH=src python src/swarm_api.py examples/swarm-service.json
```

Or build the container:

```bash
docker build -t glaciereq-swarm .
docker volume create glaciereq-swarm-data
docker run --rm \
  -p 8787:8787 \
  -e GLACIEREQ_SWARM_TOKEN \
  -v glaciereq-swarm-data:/data \
  glaciereq-swarm
```

The container health check calls `GET /health`. The persistence smoke in `.github/workflows/swarm-container.yml` executes a real task, restarts the container against the same volume, and verifies the exact result still exists.

## HTTP interface

Unauthenticated lifecycle probes:

- `GET /health`
- `GET /ready`

Authenticated operations:

- `GET /v1/status`
- `POST /v1/tasks`
- `GET /v1/tasks/<task_id>`
- `POST /v1/run`
- `POST /v1/run-until-idle`

### Submit

```json
{
  "task_id": "repo-001",
  "required_capabilities": ["crystallize_repo"],
  "payload": {
    "repository": "GlacierEQ/example"
  },
  "priority": 10,
  "max_attempts": 3
}
```

A capable configured worker must exist or the task remains queued. Execution failure is fed back into the scheduler retry/reassignment state rather than being reported as success.

## Current boundary

The service is now a real deployable runtime surface, but the repository is **not yet CRYSTALLIZED**. The current gap matrix remains authoritative until exact-head verification advances the new execution/persistence/service capabilities and until the service has a real deployment receipt. Dynamic worker registration/cancellation, MCP transport, deeper crash-atomic transitions, and direct integration of the estate crystallization executor remain material work where listed in the capability model.
