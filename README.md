# GlacierEQ Swarm — Master Swarm Orchestrator 🐝

> **Central multi-agent swarm orchestration and task routing framework for GlacierEQ.**

[![Python](https://img.shields.io/badge/Python-3.9+-blue)]()
[![Domain](https://img.shields.io/badge/Domain-Swarm%20Orchestration-purple)]()

---

## 🎯 For Recruiters & Hiring Managers

This repository implements **GlacierEQ Swarm** — the central multi-agent swarm orchestrator that manages agent discovery, task distribution, and load balancing across all portfolio components. It demonstrates:

- **Swarm topology management** tracking active worker nodes and capabilities
- **Dynamic task distribution** routing incoming user goals to optimal subagents
- **Fault-tolerant swarm recovery** automatically re-assigning failed agent tasks
- **Unified telemetry stream** aggregating agent logs and completion events

**Why this matters**: Large-scale agentic engineering requires centralized swarm orchestration to coordinate specialized subagents towards complex multi-phase goals.

---

## 🔬 For Engineers & Technical Reviewers

### Core Components

| Component | Language | Purpose |
|---|---|---|
| `swarm_master.py` | Python | Master swarm orchestrator, worker pool, task distributor |
| `tests/` | Python | Swarm routing and failure recovery test suite |

---

## 🤖 ML/AI & Programmatic Mesh Integration

- **MCP Tool**: `swarm_status()` — global swarm capacity and worker health status
- **Mastermind Sidecar**: Primary control node for APEX Highway mesh
- **SHA-256 Integrity**: Tracked in `.integrity/file_hashes.json`

---

## ⚡ Quick Start

```bash
python3 swarm_master.py
```
