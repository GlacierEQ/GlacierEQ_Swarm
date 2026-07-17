# Grok Build CLI — Architecture & Optimization Guide

**Source:** `~/.grok/docs/user-guide/` + live `~/.grok/config.toml`  
**Supersedes:** flash chat dump (blocked by disk-full)  
**Related:** `TOOLBELT.md` · `toolbelt-doctor.py` · AGENTS L0–L5

---

## 1. Built-in tools (agent harness)

| Capability | Tools / notes |
|------------|----------------|
| Terminal | `run_terminal_command` — timeouts, output caps, background |
| Files | `read_file`, `write` / `search_replace` — incremental edits preferred |
| Search | `list_dir`, `grep` |
| Web | `web_search`, `web_fetch` / `open_page` |
| Orchestration | `spawn_subagent`, task output, `todo_write` |
| Memory | `memory_search`, `memory_get` |
| MCP | `search_tool`, `use_tool` |
| Goals | `update_goal`, plan mode |
| Media | image_gen / image_edit / video (when available) |

Permission-gated; YOLO / allow-lists change friction, not inventory.

---

## 2. Execution workflows

| Mode | How | Use |
|------|-----|-----|
| **Interactive TUI** | default `grok` | Daily agent work |
| **Headless** | `grok -p "…" --yolo` | CI / scripts |
| **Agent / ACP** | `grok agent stdio` | IDE bind |

**TUI:** `simple_mode` (prompt edit); `vim_mode` (scrollback); modals `/plugins` `/hooks` `/agents` `/personas` `/mcps`; Ctrl+B tasks; Ctrl+T todos.

**Headless:** `--tools` / `--disallowed-tools` · `--allow` / `--deny` · `--sandbox`.

---

## 3. Lifecycle hooks

| Location | Path |
|----------|------|
| Global | `~/.grok/hooks/*.json` |
| Project | `<project>/.grok/hooks/*.json` |
| Plugin | inside plugins |

| Event | Role |
|-------|------|
| SessionStart / SessionEnd | boundaries |
| UserPromptSubmit | pre-eval |
| PreToolUse | **blocking** allow/deny |
| PostToolUse / PostToolUseFailure | post-tool |
| PermissionDenied | react to rejects |
| Stop / StopFailure | turn complete |

PreToolUse stdout: `{"decision":"allow"}` or `{"decision":"deny","reason":"…"}`.

---

## 4. Plugins & marketplace

| Scope | Path | Trust |
|-------|------|-------|
| User | `~/.grok/plugins/` | auto-trusted |
| Project | `.grok/plugins/` | folder trust |

Marketplace: `[[marketplace.sources]]` in config.toml  
Install: `grok plugin install owner/repo --trust`

---

## 5. Editable configuration

### `~/.grok/config.toml`

| Section | Key knobs |
|---------|-----------|
| `[ui]` | `permission_mode`, `yolo`, `remember_tool_approvals`, `simple_mode`, `vim_mode` |
| `[session]` | `auto_compact_threshold_percent` |
| `[memory]` | `enabled` + search / initial_injection |
| `[subagents]` | roles, personas, isolation |
| `[agent]` / `[agent.goal]` | adaptive, goal classifier |
| `[mcp_servers.*]` | sequential-thinking, ai-humanizer, apple-mcp, … |
| `[marketplace]` | plugin sources |

### `~/.grok/pager.toml`

`alt_screen` · `respect_manual_folds` · `sticky_headers`

**Precedence:** CLI flags > env > config.toml > remote > defaults.

---

## 6. Optimization blueprint (GlacierEQ)

| Setting | Target | Why |
|---------|--------|-----|
| `remember_tool_approvals` | true | less friction |
| `auto_compact_threshold_percent` | 70 | earlier compact |
| `memory.enabled` | true | cross-session |
| `memory.search` | max 8 / min ~0.35 | denser recall |
| `agent.yolo` | true | goal autonomy |
| `ui.yolo` | **optional** | full bypass — risk |
| sequential-thinking + ai-humanizer | enabled | L0b + L0c |

Apply:

```bash
python3 ~/GlacierEQ_Swarm/automations/apply_grok_config_perf.py
# optional full UI yolo:
# python3 …/apply_grok_config_perf.py --force-ui-yolo
```

**Headless:**

```bash
grok -p "Build and fix formatting" \
  --yolo \
  --allow "Bash(npm run*)" \
  --allow "Write(src/**/*)" \
  --sandbox workspace
```

---

## 7. GlacierEQ integration

| Layer | Ptr |
|-------|-----|
| AGENTS | `~/AGENTS.md` |
| Toolbelt | `toolbelt/TOOLBELT.md` |
| Doctor | `automations/toolbelt-doctor.py` |
| This guide | `toolbelt/GROK_BUILD_CLI.md` |
| Config fragment | `toolbelt/config_performance.toml` |

**Disk full:** clear rebuildable caches (bun/npm/pip); `device-stability-flipper.py` (never kill Comet/Neon).

---

## 8. Local docs

`~/.grok/docs/user-guide/`: `05-configuration` · `08-skills` · `09-plugins` · `10-hooks` · `14-headless` · `16-subagents`

---

*Durable 2026-07-13 · truth > flash dump*
