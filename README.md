# GlacierEQ_Swarm

Private Swarm OS for GlacierEQ operator machine.

## Contents

- `automations/` — MICROWAVE flippers (token-100pct, voice Stage A/C queue, make-heavy, toolbelt-doctor, …)
- `skills/` — mirrored skill routers (mimo_skills, token_saver_connector)
- `state/capability_merge.json` — Grok vs MiMo capability matrix (no evidence)
- `toolbelt/` — activation map when present

## Privacy

- **No** voice memo audio, intake hashes of legal audio, or AEON case packs in this repo.
- Local full state remains at `~/GlacierEQ_Swarm/state/` (gitignored on workstation).

## Quick run

```bash
python3 automations/toolbelt-doctor.py
python3 automations/token-100pct-savings-flipper.py
python3 automations/make-heavy-microwave-flipper.py
python3 automations/voice-memo-stage-c-queue-flipper.py
```

## Host notes

8GB / i5 device: STT blocked until whisper-cpp tiny or remote WhisperX.
