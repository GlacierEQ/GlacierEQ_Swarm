# AZOP — A–Z Orchestration Protocol (Kilo × Grok)

**Name note:** This is **not** a rename of **AKOS** (Apex Knowledge OS).  
AZOP = multi-wave agent orchestration on Grok. AKOS = knowledge/governance OS.

**Law:** measure or unknown · no invented 75%/90%/100% · token-saver + sequential + humanizer  
**Ptrs:** `TOOLBELT.md` · `GROK_BUILD_CLI.md` · `~/AGENTS.md` · path pistons

---

## Phase map → Grok reality

| Phase | Flash label | Grok action (real) |
|------:|-------------|---------------------|
| **A** | Context init | Open map `ecosystem_map.json` · brainsync/memory JIT · optional session register |
| **B** | Gauntlet / auth | Env/OIDC already loaded; avoid re-auth loops; use existing MCP tokens |
| **C** | MICROWAVE discover | Parallel `spawn_subagent` **explore** · `capability_mode=read-only` · `background=true` |
| **D** | CORE-THINK synth | Parent synthesizes **pointers only** (`path:L-R`) · sequential_thinking |
| **E** | VIPER implement | `general-purpose` · `isolation=worktree` · read-write · merge after verify |
| **F** | SPECTER async | Long jobs `background=true` · poll `get_command_or_subagent_output` |
| **G** | GHOST quiet tools | Prefer local flippers / hooks over LLM for format/lint |
| **H** | SONIC hooks | `PreToolUse` / `PostToolUse` for gates & auto-lint (no chat noise) |
| **I** | Resume chain | `resume_from=<subagent_id>` same type — reuse transcript, don't re-bootstrap |
| **J** | Compact guard | `auto_compact_threshold_percent=70` (token-saver; **not** 85) |
| **K** | Headless shard | Multiple `grok -p … --yolo` only when disk/CPU allow |
| **L–Z** | Close | Flush state · measure · humanize final · stop |

---

## Wave protocol (default multi-step job)

```
Parent (token-saver)
  ├─ Wave1 explore×N  [read-only, background, concise persona]
  │     └─ return: 3–5 facts + paths (no dumps)
  ├─ Wave2 parent synth + sequential_thinking
  │     └─ design / plan_mode if high risk
  ├─ Wave3 implementer [worktree, read-write]
  │     └─ tests + check-work
  └─ Integrate · humanize · ptr state
```

### Subagent spawn pattern (conceptual)

| Wave | type | mode | isolation | notes |
|------|------|------|-----------|-------|
| Discover | `explore` | read-only | none | parallel OK |
| Plan | `plan` | read-only | none | architecture |
| Build | `general-purpose` | read-write | **worktree** | edits isolated |
| Chain | any | same as source | — | `resume_from` prior id |

Child prompt always: `token-saver + concise; 3–5 facts + pointer; no wall of text`.

---

## Elite protocols (truth-checked)

| Protocol | Flash claim | Verified practice |
|----------|-------------|-------------------|
| **Resume chain** | “90% startup saved” | Real: `resume_from` keeps transcript — savings **unknown** until measured |
| **Concise persona** | “30–50% smaller” | Real: `subagents.personas.concise` in config — measure payloads if needed |
| **SPECTER** | “100% active runtime” | Real: background subagents + get_task_output — parent can continue |
| **SONIC hooks** | intercept formatters | Real: `~/.grok/hooks/*.json` Pre/PostToolUse |
| **MICROWAVE** | parallel explore | Real: multiple explore agents; prefer local scripts when no LLM needed |
| **GHOST** | hide terminal | Prefer flippers; headless `--disallowed-tools` when appropriate |
| **Session register** | gemini script | Path exists: `gemini-cli/.../register_gemini_session.py` — run only if that stack is in use |

---

## Session bootstrap (A/B)

```bash
# Optional (gemini-cli stack only)
python3 /Users/kcbflux/gemini-cli/packages/intelligence/register_gemini_session.py

# Always-local
python3 ~/GlacierEQ_Swarm/automations/toolbelt-doctor.py
# open map ptr: state/ecosystem_map.json
```

---

## Compaction & token policy

| Setting | GlacierEQ value | Why |
|---------|-----------------|-----|
| auto_compact | **70** | Earlier than flash’s 85 → less bloat |
| pure_pointer | large bodies on disk | chat = ptr only |
| flippers first | MICROWAVE scripts | zero LLM when possible |

Do **not** raise compact to 85 for “max performance” — that holds more junk longer.

---

## Headless parallel (careful)

```bash
# Only if free disk ≥ few GB and CPU OK
grok -p "Analyze packages/ui" --tools "list_dir,grep" --yolo &
grok -p "Analyze packages/sdk" --tools "list_dir,grep" --yolo &
wait
```

On 8GB / near-full disk: **prefer in-session explore subagents**, not multi-process shards.

---

## Hooks skeleton (SONIC)

| Event | Use |
|-------|-----|
| PreToolUse | deny dangerous rm/force-push patterns |
| PostToolUse | optional format after Write |

Templates: `~/.grok/hooks/` (global). See `GROK_BUILD_CLI.md` §hooks.

---

## Anti-patterns

- Renaming AKOS to “Kilo orchestration”
- Invented savings % without ledger
- Parent session sequential full-repo reads
- Implement in parent when worktree child is safer
- Spamming headless grok on full disk

---

## Related pistons (path-of-highest-power)

APEX microwave · corethink · bodybuilder · BLACK ghost/sonic · GREY viper/specter — operational names in `ring3_manifest` / specialists.json; use as **labels** for waves, not separate products.

---

*AZOP v1 · 2026-07-13 · Kilo A–Z ideas × Grok real tools × GlacierEQ truth protocol*
