#!/usr/bin/env bash
set -euo pipefail

ARTIFACTS=.verification-artifacts
mkdir -p "$ARTIFACTS"

# Compile execution and transport surfaces this workflow actually protects.
python -m py_compile \
  src/mechanism.py \
  automations/estate_function_restorer.py \
  automations/estate_function_restorer_safe.py \
  automations/crystallization_repo_worker.py \
  automations/crystallization_work_unit.py \
  automations/crystallization_swarm_dispatch.py \
  automations/crystallization_executor.py \
  scripts/operate.py

# Prove actual Swarm behavior: routing, capacity, priority, retry/reassignment,
# worker health, telemetry integrity, snapshot recovery, and cold-start runtime.
python -m unittest tests/test_swarm.py -v \
  2>&1 | tee "$ARTIFACTS/swarm-behavior-tests.log"

# Prove CRYSTALLIZATION terminal-status and persistent dispatch semantics.
python -m unittest automations/tests/test_crystallization_executor.py -v \
  2>&1 | tee "$ARTIFACTS/crystallization-tests.log"
python -m unittest tests/test_crystallization_dispatch.py -v \
  2>&1 | tee "$ARTIFACTS/crystallization-dispatch-tests.log"

# Lock source-preserving branch transmission: stable continuation branches,
# prior-head checkpoints, descendant-only normal pushes, and hard divergence refusal.
python -m unittest tests/test_crystallization_source_transport.py -v \
  2>&1 | tee "$ARTIFACTS/crystallization-source-transport-tests.log"

# Preserve the legacy restorer's verification plumbing while separately proving
# that the recommended safe steering shell intercepts its force-push primitive.
python -m unittest automations/tests/test_estate_function_restorer.py -v \
  2>&1 | tee "$ARTIFACTS/restorer-plumbing-tests.log"
python -m unittest tests/test_estate_restorer_source_transport.py -v \
  2>&1 | tee "$ARTIFACTS/restorer-source-transport-tests.log"

# Static anti-regression: the active crystallization worker itself must not
# contain a force-push option. The legacy raw restorer is allowed to retain the
# intercepted call site until its implementation is fully folded into the safe
# interface, but the safe shell test above proves that interface rewrites it.
if grep -n -- '--force-with-lease\|--force' automations/crystallization_repo_worker.py; then
  echo "active crystallization worker contains a forbidden force-push option" >&2
  exit 1
fi

# Exercise the shipped runtime probe and preserve the emitted evidence.
python scripts/operate.py | tee "$ARTIFACTS/operate.json"
python - <<'PY'
import json
from pathlib import Path

path = Path('.verification-artifacts/operate.json')
data = json.loads(path.read_text(encoding='utf-8'))
if not isinstance(data, dict):
    raise SystemExit('operate receipt must be a JSON object')
smoke = data.get('smoke')
if not isinstance(smoke, dict):
    raise SystemExit('operate receipt missing smoke evidence')
if smoke.get('content_checked') is not True:
    raise SystemExit('operate did not prove a content-checked mechanism call')
if smoke.get('invoked') is not True:
    raise SystemExit('operate did not invoke the mechanism')
if not data.get('module'):
    raise SystemExit('operate receipt missing executed module identity')
result = smoke.get('result')
if not isinstance(result, dict):
    raise SystemExit('operate smoke result must be structured')
if result.get('status') != 'HEALTHY':
    raise SystemExit('operate smoke result is not healthy')
if (result.get('telemetry') or {}).get('status') != 'PASS':
    raise SystemExit('operate smoke telemetry integrity did not pass')
PY

# Also execute the domain-native demonstrator directly rather than relying only
# on generic introspection.
PYTHONPATH=src python - <<'PY' | tee "$ARTIFACTS/swarm-demo.json"
import json
from mechanism import run
receipt = run()
assert receipt['status'] == 'HEALTHY'
assert receipt['result'] == 'swarm_demo_complete'
assert receipt['telemetry']['status'] == 'PASS'
print(json.dumps(receipt, sort_keys=True))
PY

cp governance/CRYSTALLIZATION_MANDATE.yaml "$ARTIFACTS/CRYSTALLIZATION_MANDATE.yaml"
python - <<'PY'
import hashlib
import json
from pathlib import Path

artifacts = Path('.verification-artifacts')
rows = {}
for path in sorted(p for p in artifacts.iterdir() if p.is_file()):
    rows[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
(artifacts / 'verification-manifest.json').write_text(
    json.dumps(
        {'schema': 'glaciereq.swarm-functional-verification.v1', 'artifacts': rows},
        indent=2,
        sort_keys=True,
    ) + '\n',
    encoding='utf-8',
)
PY
