#!/usr/bin/env bash
set -euo pipefail

ARTIFACTS=.verification-artifacts
mkdir -p "$ARTIFACTS"

# The estate control plane is judged by executable behavior, not repository-wide
# formatting debt. Compile the code paths this workflow actually protects.
python -m py_compile \
  automations/estate_function_restorer.py \
  automations/crystallization_executor.py \
  scripts/operate.py

# Prove the CRYSTALLIZATION terminal-status law and the compatibility plumbing
# that the executor actually calls.
python -m unittest automations/tests/test_crystallization_executor.py -v \
  2>&1 | tee "$ARTIFACTS/crystallization-tests.log"
python -m unittest automations/tests/test_estate_function_restorer.py -v \
  2>&1 | tee "$ARTIFACTS/restorer-plumbing-tests.log"

# Exercise the shipped Swarm runtime probe. This is a real mechanism call, not
# an import-only smoke. Preserve the emitted receipt for review.
python scripts/operate.py | tee "$ARTIFACTS/operate.json"
python - <<'PY'
import json
from pathlib import Path

path = Path('.verification-artifacts/operate.json')
data = json.loads(path.read_text(encoding='utf-8'))
if not isinstance(data, dict):
    raise SystemExit('operate receipt must be a JSON object')
if data.get('content_checked') is not True:
    raise SystemExit('operate did not prove a content-checked mechanism call')
if not data.get('module'):
    raise SystemExit('operate receipt missing executed module identity')
PY

# Preserve the exact mandate used by this verification run.
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
