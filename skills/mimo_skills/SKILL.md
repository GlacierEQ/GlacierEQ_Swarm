---
name: mimo_skills
description: Router for MiMo skill package + capability merge with Grok Swarm. Primary child test-runner; also points to mimo-code make-it-heavy, helix-pro-code, GROK_DEV_PIPELINE. Use when mimo, test suites, make-it-heavy, or agent capability merge.
when-to-use: "mimo", "test-runner", "make-it-heavy", "capability merge", "mimo-code"
---

# mimo_skills (package router + merge)

**Not a dump.** Load child bodies JIT. Canonical capability matrix: `~/GlacierEQ_Swarm/state/capability_merge.json`.

## Children / pointers

| ID | Path | When |
|----|------|------|
| **test-runner** | `mimo_skills/test-runner/SKILL.md` | Standardized GlacierEQ/colossus/mastermind tests |
| **make-it-heavy** | `~/mimo-code/packages/opencode/skills/gemini/make-it-heavy/SKILL.md` | Exhaustive rigor (legal, hashes, evidence) — depth over chat savings |
| **helix-pro-code** | `~/mimo-code/packages/opencode/skills/grok/helix-pro-code/SKILL.md` | Alpha/Omega coding laws |
| **GROK_DEV_PIPELINE** | `~/mimo-code/GROK_DEV_PIPELINE.md` | Multi-repo orchestration map |
| **mimo CLI** | `~/.mimocode/bin/mimo` | Prefer over `packages/opencode/bin/mimo` (ESM break) |

## Grok Swarm merge (this device)

| Layer | Owner | Rule |
|-------|-------|------|
| Chat / MCP / skills | **Grok agent** | token-saver pure_pointer; sequential-thinking; humanizer final |
| Batch hash/duration/queue | **Flippers MICROWAVE** | zero LLM |
| Exhaustive verify | **make-heavy-microwave-flipper** | make-it-heavy × MICROWAVE |
| Voice Stage A | `voice-memo-stage-a-flipper.py` | SHA-256 + afinfo |
| Voice Stage C queue | `voice-memo-stage-c-queue-flipper.py` | priority; STT blocked until engine |
| Tests | **test-runner** | local pytest only |

## Host constraints (honest)

- 8GB RAM · i5-5250U · ~6GB free disk → **no** torch/whisper large local
- STT: queue only until whisper-cpp tiny/base or remote WhisperX
- Legal/AEON audio: private, no chat dumps

## Activation

```bash
# capability + savings + heavy verify
python3 ~/GlacierEQ_Swarm/automations/make-heavy-microwave-flipper.py
python3 ~/GlacierEQ_Swarm/automations/token-100pct-savings-flipper.py
python3 ~/GlacierEQ_Swarm/automations/toolbelt-doctor.py

# voice mission
python3 ~/GlacierEQ_Swarm/automations/voice-memo-stage-a-flipper.py
python3 ~/GlacierEQ_Swarm/automations/voice-memo-stage-c-queue-flipper.py
```

Ptr results: `state/*_last.json` · `state/capability_merge.json` · `state/voice_memos/`.
