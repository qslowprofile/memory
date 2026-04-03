# openclaw-memory-knowledge

Automatic memory/knowledge restructuring, incremental ingestion, and self-healing for [OpenClaw](https://github.com/anthropics/claude-code).

---

## What It Does

OpenClaw users accumulate scattered `memory/` and `knowledge/` data over time — mixed formats, no versioning, no dedup, no quality control. This skill fixes that with a closed-loop pipeline:

1. **Bootstrap** — one-time restructuring of existing data into a unified layered architecture (AdaMem-lite + OpenViking style)
2. **Ingest** — continuous incremental ingestion with UPDATE semantics, retrieval-enhanced dedup, and version tracking
3. **Self-Evolve** — automated probes, consistency checks, TTL/heat management, and repair actions

Zero external dependencies. No embedding server required. Works with OpenClaw's native `memory search`.

---

## Features

- **8-bucket classification** with 4-level priority chain (path rules > frontmatter > filename patterns > keyword scoring)
- **L0/L1/L2 layered storage** with progressive disclosure retrieval protocol
- **UPDATE semantics** — file-level replace + record-level version increment, no duplicate accumulation
- **Retrieval-enhanced dedup** — uses OpenClaw native `memory search` scores, per-bucket thresholds
- **Quality gates** — confidence, text length, symbol-only rejection, with audit trails
- **Memory policy** — `persist` / `private` / `ephemeral` controls for long-term retention
- **TTL / heat / archive** — automatic aging, cold demotion, and archival
- **Knowledge graph** — predicate normalization, KV extraction, table relation extraction, conflict resolution
- **Profile & preferences snapshots** — derived current-state views for fast retrieval
- **Self-healing** — 10+ probes covering L0/L1/L2 consistency, dedup, graph quality, TTL hygiene
- **Single-zip distribution** — install via one hook package, auto-triggers on new sessions
- **Full auditability** — reports for every operation, dedup audit logs, rejection logs

---

## Quick Start

### Option 1: Hook Install (Recommended)

1. Download the latest `memory-knowledge-auto-migrate-hook.zip` from [Releases](../../releases)
2. Send the zip to your OpenClaw Agent and say "install this hook"
3. Start a new conversation — the hook triggers automatically on `agent:bootstrap`

First bootstrap takes 1-3 minutes. Subsequent sessions run incremental ingestion (much faster).

### Option 2: Manual Install

```bash
# Clone this repo
git clone https://github.com/anthropics/openclaw-memory-knowledge.git

# Run directly (auto-detects your OpenClaw workspace)
python scripts/auto_migrate.py
```

### Build the Hook Package

```bash
bash scripts/package_hook.sh
# Output: dist/memory-knowledge-auto-migrate-hook.zip
```

---

## How It Works

### Architecture

```
<workspace>/memory/.adaptr-v1/
  viking/                        # Business data layer (by bucket)
    user/memories/               # profile, preferences, events, entities, relations
    user/knowledge/              # facts, procedures, references
    agent/skills/                # agent_skill buffer
    session/working/             # working memory buffer
    archive/                     # TTL-archived records
  layers/                        # Derived views
    l0_abstract.json             # Global statistics
    l1_overview.jsonl            # Bucket summaries
    l2_records.jsonl             # Atomic record layer
    profile_snapshot.json        # Current-state profile
    preferences_snapshot.json    # Current-state preferences
    retrieval-hints.json         # Hot buckets, keywords, flash records
    retrieval_protocol.json      # Progressive disclosure protocol
  state/                         # Operational state
    record_hashes.txt
    processed_files.json
  reports/                       # Audit & diagnostics
    bootstrap_*.json
    ingest_*.json
    self_evolve_*.json
    dedup_audit_*.jsonl
    rejected_*.jsonl
```

### 8 Buckets

| Category | Buckets |
|----------|---------|
| Memory | `memory.profile`, `memory.preferences`, `memory.events`, `memory.agent_skill`, `memory.working` |
| Knowledge | `knowledge.facts`, `knowledge.procedures`, `knowledge.references` |

### Classification Chain

1. **P1** — Path rules (high confidence)
2. **P2** — Frontmatter / `Category:` tags
3. **P2.5** — Knowledge filename/path patterns
4. **P3** — Keyword scoring with dynamic confidence

### Retrieval Protocol

Progressive disclosure — read the minimum needed:

1. `profile_snapshot` / `preferences_snapshot` (current-state queries stop here)
2. `retrieval-hints.json` (bucket routing queries stop here)
3. `l1_overview.jsonl` (summary queries stop here)
4. `l2_records.jsonl` (only for evidence, source tracing, or conflict resolution)

---

## Usage

### Auto Mode (Default)

```bash
# Zero-config: auto-detects workspace, chooses bootstrap or ingest
python scripts/auto_migrate.py
```

### Explicit Modes

```bash
# Force full bootstrap
python scripts/auto_migrate.py --mode bootstrap

# Force incremental ingest
python scripts/auto_migrate.py --mode ingest

# Preview without writing
python scripts/auto_migrate.py --no-apply

# Specify workspace manually
python scripts/auto_migrate.py --workspace-root /path/to/workspace
```

### Self-Healing

```bash
python scripts/self_evolve.py --workspace-root /path/to/workspace --repair
```

### Dedup Tuning

```bash
python scripts/incremental_ingest.py \
  --workspace-root /path/to/workspace \
  --apply \
  --retrieval-dedup on \
  --retrieval-threshold 0.94 \
  --retrieval-search-min-score 0.25 \
  --retrieval-limit 8 \
  --retrieval-max-calls 80
```

---

## Observability

After install or migration, check:

| What to Check | Where |
|---|---|
| Quick status | `MEMORY_KNOWLEDGE_AUTO_MIGRATE.md` (injected into session context) |
| Migration reports | `.adaptr-v1/reports/bootstrap_*.json` or `ingest_*.json` |
| Self-evolve results | `.adaptr-v1/reports/self_evolve_*.json` |
| Dedup decisions | `.adaptr-v1/reports/dedup_audit_*.jsonl` |
| Rejected records | `.adaptr-v1/reports/rejected_*.jsonl` |
| Current-state views | `layers/profile_snapshot.json`, `layers/preferences_snapshot.json` |

Status indicators:
- **Memory Migration Done** — all good
- **Memory Migration Warning** — check `Error code / Error detail / Hints` in the status
- **Slow first run** — normal, full-scan bootstrap in progress

---

## Data Model

### L2 Record Fields

Each atomic record tracks:

| Field | Purpose |
|---|---|
| `id`, `content_hash` | Identity & dedup |
| `source_kind`, `source_path`, `locator` | Source tracing |
| `bucket`, `confidence` | Classification |
| `entities`, `relations`, `tags` | Knowledge graph |
| `version`, `ttl`, `heat`, `last_accessed` | Lifecycle management |
| `memory_function` | `factual` / `experiential` / `working` |
| `formation_mode` | `bootstrap` / `ingest` / `runtime` / `manual` |
| `trust_tier` | `curated` / `extracted` / `generated` |
| `memory_policy` | `persist` / `private` / `ephemeral` |

### Memory Policy

- `persist` — normal long-term storage
- `private` — excluded from L2, graph, and retrieval layers
- `ephemeral` — extracted but routed to `memory.working`
- `<private>...</private>` inline tags also supported

---

## FAQ

**Do I need an external embedding service?**
No. Dedup uses OpenClaw's native `memory search`. Python side uses only stdlib.

**Will it break my existing data?**
Bootstrap has backup + rollback on failure. Risk is controlled.

**Can I distribute it to others with just a zip?**
Yes. The hook zip is self-contained. Supports `openclaw hooks install <zip>` or manual fallback.

**Why does self-evolve sometimes report failures after repair?**
Some repairs trigger new probe failures. The system reports this explicitly and suggests a second repair pass rather than silently masking issues.

**What file formats are supported?**
`.txt`, `.md`, `.log`, `.yaml`, `.yml`, `.json`, `.jsonl`, `.csv`, `.db`, `.sqlite`, `.sqlite3`

---

## Tuning Guide

### Memory-Heavy Workspaces

- Keep `retrieval-threshold` high (0.94)
- `retrieval-search-min-score`: 0.2-0.3
- `retrieval-max-calls`: 50-80

### Knowledge-Heavy Workspaces

- Focus on per-bucket classification thresholds
- Increase `dedup-audit-max-records` for visibility
- Lower global threshold cautiously, prefer per-bucket tuning

### Large Repositories

- Start with `--no-apply` to preview scale
- Control `--max-file-size-mb` and `--max-records-per-file`
- Verify value before using `--include-hidden`

---

## Project Structure

```
scripts/
  auto_migrate.py          # Main entry point (auto workspace detection)
  bootstrap_restructure.py # Full restructuring
  incremental_ingest.py    # Incremental ingestion with dedup
  self_evolve.py           # Probe + verify + repair
  native_memory_search.py  # OpenClaw native search wrapper
  mk_arch_core.py          # Architecture core utilities
  package_hook.sh          # Build hook zip
  package_skill.sh         # Build skill zip
  install_hook_from_zip.sh # Local install helper
  install_openclaw_autorun.sh
hooks/
  openclaw/
    handler.js             # Bootstrap event handler
    HOOK.md                # Hook metadata
references/
  architecture.md          # Architecture spec
  retrieval-protocol.md    # Retrieval protocol spec
  mapping.yaml             # Bucket-to-path mapping
  zero-config.md           # Zero-config usage guide
tests/
  test_handler_workspace_detection.js
  test_workspace_discovery.py
```

---

## Detailed Documentation

- [Architecture Reference](references/architecture.md)
- [Retrieval Protocol](references/retrieval-protocol.md)
- [Zero-Config Guide](references/zero-config.md)
- [Deep Dive (Chinese)](DEEP_DIVE.zh-CN.md) — full technical deep-dive with design rationale and evolution history
- [Hook Install Guide](INSTALL.md)
- [Release Process](RELEASING.md) (maintainers only)

---

## Requirements

- Python 3.8+ (stdlib only, no pip dependencies)
- OpenClaw with working `memory search`

---

## License

MIT
