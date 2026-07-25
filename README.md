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

---

## Fleet ops (transparent)

This repo may include **`.integrity/`** (SHA-256 baselines / watchdog) and/or a health sidecar.
These are **documented multi-repo fleet operations**, not covert implants.

See [SECURITY_AND_FLEET_OPS.md](SECURITY_AND_FLEET_OPS.md) and
`~/GlacierEQ_Swarm/state/PORTFOLIO_SHADOW_AND_GAUNTLET.md`.

## Helix strand

See [HELIX_STRAND.md](HELIX_STRAND.md) — piston/spiral role in the portfolio double helix.
