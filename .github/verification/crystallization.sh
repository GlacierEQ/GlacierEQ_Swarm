#!/usr/bin/env bash
set -euo pipefail

ARTIFACTS=.verification-artifacts
mkdir -p "$ARTIFACTS"

# Compile only execution surfaces this workflow actually protects.
python -m py_compile \
  src/mechanism.py \
  automations/estate_function_restorer.py \
  automations/crystallization_executor.py \
  scripts/operate.py

# Prove actual Swarm behavior: routing, capacity, priority, retry/reassignment,
# worker health, telemetry integrity, snapshot recovery, and cold-start runtime.
python -m unittest tests/test_swarm.py -v \
  2>&1 | tee "$ARTIFACTS/swarm-behavior-tests.log"

# Prove the CRYSTALLIZATION terminal-status law and compatibility plumbing.
python -m unittest automations/tests/test_crystallization_executor.py -v \
  2>&1 | tee "$ARTIFACTS/crystallization-tests.log"
python -m unittest automations/tests/test_estate_function_restorer.py -v \
  2>&1 | tee "$ARTIFACTS/restorer-plumbing-tests.log"

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
    json.dumps({'schema': 'glaciereq.swarm-functional-verification.v1', 'artifacts': rows}, indent=2, sort_keys=True) + '\n',
    encoding='utf-8',
)
PY
