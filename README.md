# GlacierEQ Swarm

**Multi-agent orchestration and estate-wide crystallization control plane for GlacierEQ.**

## What it actually does

The repository now contains a real deterministic swarm scheduler in `src/mechanism.py` rather than an empty mechanism shell.

The orchestration core supports:

- worker registration with explicit capability sets and concurrency limits;
- capability-aware and priority-aware task routing;
- deterministic load balancing across equally capable workers;
- task lifecycle tracking from queue → assignment → running → success/failure;
- bounded retries and dead-letter state after retry exhaustion;
- reassignment away from failed workers when alternate capacity exists;
- worker health, draining, heartbeat, offline/unhealthy recovery semantics;
- integrity-chained telemetry for every orchestration transition;
- content-addressed snapshots and validated snapshot recovery;
- machine-readable swarm status with capacity, queue, task, and worker state.

The control plane also carries the **CRYSTALLIZATION-MANDATE**, which governs the estate-wide metamorphosis process. That executor inventories accessible repositories, reconstructs purpose/capabilities, tracks explicit gaps, invokes isolated implementation workers, executes build/test/runtime proof, requires real deployment evidence where deployment is natural, and refuses estate completion while unresolved repositories remain.

## Quick execution

```bash
PYTHONPATH=src python - <<'PY'
import json
from mechanism import run
print(json.dumps(run(), indent=2, sort_keys=True))
PY
```

The cold-start demonstrator registers two workers, routes a capability-constrained task, executes its lifecycle, verifies telemetry integrity, and emits a snapshot digest.

The repository-owned CI verifier runs the same domain-native lifecycle plus behavioral orchestration tests and the crystallization contract tests.

## Core surfaces

| Surface | Purpose |
|---|---|
| `src/mechanism.py` | Swarm registry, scheduler, load balancer, recovery state machine, telemetry, snapshots |
| `tests/test_swarm.py` | Routing, capacity, priority, retry, failure recovery, telemetry, snapshot behavior |
| `scripts/operate.py` | Generic cold-start runtime proof |
| `governance/CRYSTALLIZATION_MANDATE.yaml` | Estate metamorphosis authority |
| `automations/crystallization_executor.py` | Parallel purpose-first repository executor and estate ledger |
| `automations/tests/test_crystallization_executor.py` | Terminal-status and anti-false-completion contract |
| `.github/verification/crystallization.sh` | Repository-owned functional verification path |

## Crystallization law

A green build is not completion. A passing CI run is not completion. The estate control plane may only call a repository `CRYSTALLIZED` when its purpose is reconstructed, material capabilities are explicit and working, runtime behavior is proven, deployment is real where applicable, documentation matches execution, and no material gap remains.

The estate itself is not complete until every in-scope repository is either:

- `CRYSTALLIZED`;
- intentionally archived with a verified reason; or
- canonicalized into a verified successor.

`UNKNOWN`, `BROKEN`, and `INCOMPLETE` must reach zero before estate completion can be asserted.
