<p align="center">
  <a href="./README.md">[中文]</a> | <strong>[English]</strong>
</p>

# OpenClaw Memory & Knowledge Restructuring Toolkit

## Before You Start: Do You Have This Problem?

After using OpenClaw for a while, your workspace often starts looking like this:

```text
memory/
  2026-03-01.md    <- chronological diary notes
  2026-03-02.md
  2026-03-10.md
  user/
    memories/      <- manually created, structure varies by person
  ...(keeps getting messier)
MEMORY.md          <- keeps growing until it gets truncated
```

Then the problems show up:

- `memory_search` recall gets worse. The same fact is scattered across three files, and each search only finds one piece.
- `MEMORY.md` hits the size limit. Newly written content can get truncated and become unreadable.
- Dozens of diary files pile up, so every agent load gets slower and harder to understand.
- The `knowledge/` directory has no unified structure, so relationships between pieces of knowledge are missing and retrieval becomes inaccurate.
- Starting a new session means context reconstruction depends on luck.

The real issue is that `memory` and `knowledge` lack structure. Scattered content, weak classification, and missing relationships are what make the memory system decay over time.

This tool exists to solve that.

---

## What Is This?

This is a **deep restructuring engine for `memory/` and `knowledge/`**, delivered as an OpenClaw Hook + Skill.

Its focus is restructuring. The migration is part of that process, but the goal is to rebuild your existing memory and knowledge into a unified layered architecture:

- Bucket content by type: profile, preferences, events, skills, knowledge, and more, across 8 buckets
- Build L0/L1/L2 indexes so the agent can jump from summaries to atomic records quickly
- Extract entities and relations to form a queryable knowledge graph
- Use version-replacement semantics for incremental updates instead of infinite append-only growth
- Run periodic self-healing: deduplication, consistency checks, TTL aging, and graph maintenance

The entire flow is **zero-config and automatic**. After the hook is installed, every new session triggers it automatically. The first run performs a full restructure, and later runs only process changed files.

---

## Start Here: Is This a Good Fit for Me?

| Your Situation | Recommendation |
|---|---|
| You just started using OpenClaw and have very little memory data | Safe to install. It builds a clean baseline structure. |
| You have been using OpenClaw for a while and `memory/knowledge` is getting messy | ✅ Strongly recommended. This is the primary target scenario. |
| You maintain a large handwritten knowledge base under `knowledge/` | ✅ Recommended. The knowledge base will also be restructured and graph-indexed. |
| You worry about damaging existing data | Safe. The first full restructure automatically backs up the old data to a sibling directory. |
| You want manual control over when restructuring runs | Supported. See the "Manual Mode" section. |
| You do not want to install the hook and only want a one-time restructure | Supported. See the "Manual Mode" section. |

---

## 1. Installation (One-Time Setup)

### Steps

1. Send `memory-knowledge-auto-migrate-hook.zip` to OpenClaw.
2. Say: "Help me install this hook."

OpenClaw will read `INSTALL.md` inside the package and complete the installation automatically. The whole process usually finishes within 30 seconds.

### Verify After Installation

Open a **new session** and wait 1 to 3 minutes. Starting with v6, the hook writes a persistent status file into the workspace after it finishes, so you can verify the result even if your client does not support bootstrap injection.

**Method 1 (recommended): check the persisted status file**. Use `summary.target_root` in `last_status.json` to locate it. The path may be `memory/.adaptr-v1` or `workspace/.adaptr-v1`, depending on your directory layout. Ask OpenClaw:

> Help me find `.adaptr-v1/state/last_status.json`, then read `summary.target_root` and `summary.status`.

If `summary.status == "ok"`, the run succeeded. If `summary.status == "warning"`, open `last_status.md` for details.

**Method 2: bootstrap injected file (if your client supports it)**. If bootstrap injection is available, the new session context may include `MEMORY_KNOWLEDGE_AUTO_MIGRATE.md`. Seeing `Memory Migration Done` means the run was successful. ⚠️ This is a virtual file. It does not exist on disk, and not finding it through a full-disk search is expected. Method 1 always works.

### Seeing `Memory Migration Warning`?

Do not panic. In most cases this is a safe skip, not a crash. Confirm `summary.status` in `last_status.json`, then read `Error code / Error detail / Hints` in `last_status.md` and follow the suggestions. The most common reason is that the bootstrap event did not carry a trusted workspace path.

---

## 2. Architecture Overview After Restructuring

<img width="4518" height="522" alt="adaptr_architecture" src="https://github.com/user-attachments/assets/9048c11d-b57c-475a-b136-57c38c063913" />

After installation, a hidden `.adaptr-v1/` directory appears under `memory/`. This is where the restructured data is stored:

```text
memory/.adaptr-v1/
├── viking/
│   ├── user/
│   │   ├── memories/
│   │   │   ├── profile.jsonl        <- identity, user ID, profile traits
│   │   │   ├── preferences.jsonl    <- behavioral preferences
│   │   │   ├── events.jsonl         <- event records
│   │   │   ├── entities.jsonl       <- entity graph (people / objects / concepts)
│   │   │   ├── relations.jsonl      <- relationship graph
│   │   │   └── relation_decisions.jsonl  <- canonical relation decisions
│   │   └── knowledge/
│   │       ├── facts.jsonl          <- factual knowledge
│   │       ├── procedures.jsonl     <- procedures / workflows
│   │       └── references.jsonl     <- references
│   ├── agent/
│   │   └── skills/buffer.jsonl      <- agent skill and tool records
│   ├── session/
│   │   └── working/buffer.jsonl     <- current working state
│   └── archive/
│       └── records.jsonl            <- archived expired content
├── layers/
│   ├── l0_abstract.json             <- global summary (entry point)
│   ├── l1_overview.jsonl            <- per-bucket summaries
│   ├── l2_records.jsonl             <- full atomic records
│   ├── retrieval-hints.json         <- retrieval hot hints
│   ├── profile_snapshot.json        <- current profile snapshot (added in v5)
│   ├── preferences_snapshot.json    <- current preferences snapshot (added in v5)
│   └── retrieval_protocol.json      <- retrieval protocol description (added in v5)
├── state/
│   ├── record_hashes.txt            <- incremental dedup fingerprints
│   └── processed_files.json         <- processed-file state
└── reports/
    ├── bootstrap_*.json             <- restructure reports
    ├── ingest_*.json                <- incremental reports
    ├── rejected_*.jsonl             <- quality-gate rejections (added in v5)
    ├── policy_skipped_*.jsonl       <- memory_policy interception audit (added in v5)
    ├── dedup_audit_*.jsonl          <- dedup decision audit logs
    └── self_evolve_*.json           <- self-healing reports
```

Your original `memory/*.md` files remain untouched. `.adaptr-v1/` is an additional structured layer.

---

## Technical Background: What Research Is This Built On?

The architecture of `openclaw-memory-knowledge` did not come out of nowhere. It is an engineering-focused adaptation of recent research on LLM-agent memory systems, tuned for OpenClaw's real constraints: no vector service, local files only, and personal-workspace scale. The sections below cover the major theoretical sources and explain what this tool reuses, simplifies, or extends.

### AdaMem: User-Centric Adaptive Layered Memory

**Source**: AdaMem: Adaptive User-Centric Memory for Long-Horizon Dialogue Agents ([https://arxiv.org/abs/2603.16496](https://arxiv.org/abs/2603.16496), 2026)

AdaMem argues that one generic semantic-similarity retrieval path cannot serve every question type. Some questions need persona-level information, some need recent working context, some need event chains across time, and some need graph relationships. Its core contribution is to separate these memory types into independent stores, then assemble the retrieval path dynamically based on the question type.

**Core ideas borrowed here**:

- The separation of memory types maps directly to the 8-bucket system: `memory.profile` (persona), `memory.working` (working memory), `memory.events` (episodic memory), and the graph layer each serve a distinct role instead of being mixed in one vector space.
- The user-centric principle shows up in the separation between `viking/user/` and `viking/agent/`. Personal knowledge, preferences, and relations are isolated from the agent's own skills and tool configuration, which reduces context contamination.
- AdaMem's insight that static granularity does not fit every problem inspired per-bucket TTL strategies: `memory.profile` is `permanent`, `memory.working` is `7d`, and `memory.events` is `90d`.

**What this tool simplifies**: AdaMem routes retrieval at inference time with LLM judgment. This tool finishes classification during bootstrap, so retrieval-time routing has effectively zero overhead.

### OpenViking: A Filesystem-Native Context Database for Agents

**Source**: volcengine/OpenViking ([https://github.com/volcengine/OpenViking](https://github.com/volcengine/OpenViking), open-sourced by Volcano Engine, 2026)

OpenViking is an open-source context database for AI agents such as OpenClaw. Its two most important ideas are:

1. A **filesystem-native paradigm** instead of flat vector storage. Memory, resources, and skills are all managed through `viking://` URIs, and directory hierarchy itself becomes part of the index.
2. **On-demand L0/L1/L2 loading**. L0 is a global summary of roughly 100 tokens, L1 is a bucket-level summary of roughly 2k tokens, and L2 holds the full atomic records. Retrieval drills down from L0 as needed, which greatly reduces average token load.

**Core ideas borrowed here**:

- The three-layer index structure (`l0_abstract.json` -> `l1_overview.jsonl` -> `l2_records.jsonl`) is directly reused. L0 is the global statistical entry point, L1 summarizes each bucket, and L2 contains the atomic records for precise reads.
- The "directory as index" philosophy appears in paths like `viking/user/memories/`, `viking/user/knowledge/`, and `viking/agent/skills/`. The path itself carries classification information, which lowers the agent's cognitive burden.
- The self-evolving loop in OpenViking inspired `self_evolve.py` and its probe -> verify -> repair cycle.

**What this tool simplifies**: OpenViking depends on embedding models and a Go/Rust toolchain. This tool cuts that down to **pure Python standard library code** and reuses OpenClaw's native `memory search` for retrieval-enhanced deduplication, so there is no external HTTP service to run.

### A-MEM: Zettelkasten-Inspired Knowledge Networks and Memory Evolution

**Source**: A-MEM: Agentic Memory for LLM Agents ([https://arxiv.org/abs/2502.12110](https://arxiv.org/abs/2502.12110))

A-MEM brings the Zettelkasten method into agent memory systems. When a new memory is written, the system stores more than raw text. It creates a structured note with keywords, tags, and context, links it to related memories, and updates existing memories when new evidence arrives. This lets an agent navigate memory links instead of relying only on nearest-neighbor retrieval.

**Core ideas borrowed here**:

- Structured-note writing shows up in each L2 record's metadata: `tags`, `entities`, `section_title`, `heat`, and `ttl`.
- The `⚡` flash-marking mechanism is a lightweight version of A-MEM's "prioritize key memories" principle. Lines prefixed with `⚡` are tagged as flash records and pushed into `retrieval-hints.json` for fast access.
- The idea of memory evolution maps to incremental ingestion with file-level replacement semantics. Old versions are removed, new versions increment `version`, and `last_accessed` changes with usage, providing a lightweight form of heat evolution.

**What this tool simplifies**: A-MEM relies on LLM reasoning for memory linking and evolution. This tool replaces that with a **rule-driven graph-construction path**: predicate normalization, key-value extraction, and `(subject, predicate)` canonical-value decisions all run deterministically without calling an LLM.

### Memory Survey: Classification Frameworks and Known Gaps

**Source**: Memory in the Age of AI Agents: A Survey ([https://arxiv.org/abs/2512.13564](https://arxiv.org/abs/2512.13564))

This survey is one of the most systematic overviews of agent memory to date. It proposes five categories: Sensory, Working, Episodic, Semantic, and Procedural, and frames the problem as **storage × operation × management**. The management dimension covers priority adjustment, cross-source conflict resolution, and active forgetting, which remain common gaps in real implementations.

The 8-bucket design in this tool maps to the survey like this: Working -> `memory.working` (7d TTL); Episodic -> `memory.events`; Semantic -> `knowledge.facts` + `knowledge.references`; Procedural -> `knowledge.procedures` + `memory.agent_skill`. Sensory memory, meaning immediate context within the current window, is out of scope here.

**What this tool simplifies**: active forgetting through LLM-based merging/compression and chronological episodic traversal are still unimplemented. The v5 LLM enhancements improved part of the write-quality gate, but cross-source conflict resolution is still an acknowledged gap.

### LangMem and Claude-Mem: Same Direction, Different Constraints

**Source**: LangMem ([https://github.com/langchain-ai/langmem](https://github.com/langchain-ai/langmem)); Claude-Mem ([https://github.com/thedotmack/claude-mem](https://github.com/thedotmack/claude-mem))

LangMem is one of the most complete long-term memory SDKs for agents. It provides pluggable storage backends, a memory manager, and namespace isolation across sessions. Its two most important abilities are **conflict resolution** and **cross-thread namespaces**. Claude-Mem takes a different path: after each conversation turn, an LLM extracts what should be remembered into `CLAUDE.md`, and the next conversation injects it back as context. That approach works very well for single-agent setups.

**What this tool simplifies**: this project is constrained by zero `pip` dependencies, no external vector service, local files only, and OpenClaw native `memory search` as the only retrieval backend. Those constraints make both SDKs a poor direct fit. Compared with LangMem, this tool lacks conflict resolution. Compared with Claude-Mem, it lacks real-time LLM extraction as a core path. On the other hand, it is very good at something both of them do not target: **batch migration of historical files**. It can turn hundreds of diary files into structured memory in one pass, which is the original reason it was built.

### Design Decision Summary

| Problem | Research Direction | This Tool | Why This Trade-Off |
|---|---|---|---|
| Memory classification | LLM-based routing (AdaMem) | Rule-based bucketing at write time + low-confidence LLM fallback (v7.2) | Rules first; LLM only intervenes when classification is uncertain |
| Index structure | L0/L1/L2 layering (OpenViking) | Reused directly | Fits the OpenClaw scenario very well |
| Deduplication | Vector similarity (OpenViking) | OpenClaw native `memory search` | No external embedding dependency |
| Knowledge graph | LLM relation extraction (A-MEM) | LLM joint extraction + rule supplement (from v7.2), capped at 200 calls per session | Rules provide the base; LLM enhancement stays bounded |
| Memory evolution | LLM-triggered attribute updates (A-MEM) | File-level version replacement + TTL aging | Deterministic, auditable behavior |
| Self-healing | Missing in many designs | probe -> verify -> repair loop | Inspired by OpenViking's self-evolving concept |

The core argument behind this design is simple: **at personal-workspace scale, a deterministic rule-driven structure is more reliable, more auditable, and cheaper than a fully LLM-adaptive memory architecture**. Once the data scale drops from millions of records to tens of thousands, precise structure tends to matter more than adaptive flexibility.

---

## 3. Core Mechanisms: One Read to Understand the Whole System

### 3.1 Bucketed Restructuring (8 Content Buckets)

The first step of restructuring is **content classification**. Raw content from `memory/` and `knowledge/` files is chunked and automatically routed into 8 buckets:

| Bucket | What It Stores | Typical Sources |
|---|---|---|
| `memory.profile` | names, user IDs, identity traits | user-info sections in `MEMORY.md` |
| `memory.preferences` | behavioral preferences, communication style | lines like "the user prefers concise replies" |
| `memory.events` | events, milestones, decisions | diary files |
| `memory.agent_skill` | agent skills and tool configuration | `skills/`, `TOOLS.md` |
| `memory.working` | current task state | `SESSION-STATE.md`, temporary work logs |
| `knowledge.facts` | factual knowledge | fact files under `knowledge/` |
| `knowledge.procedures` | procedures, SOPs | operational docs under `knowledge/` |
| `knowledge.references` | references, specs | reference files under `knowledge/` |

**Classification is fully automatic**. The priority order is: path naming -> frontmatter tags -> keyword scoring fallback.

Starting with v7.1, the LLM fallback threshold for classification was raised from 0.70 to 0.80. That means more uncertain records are handed to the LLM for a second pass, which reduces misclassification between facts, procedures, and events. Users without LLM support are unaffected.

### 3.2 Three-Layer Index (L0 / L1 / L2)

After restructuring, the system automatically builds three layers of indexes so OpenClaw can locate content quickly at different granularities:

```text
L0  l0_abstract.json     <- global statistics (total volume, hot buckets, flash-record summary)
     ↓
L1  l1_overview.jsonl    <- per-bucket summaries (top tags, representative text)
     ↓
L2  l2_records.jsonl     <- full atomic records (read only when needed)
```

`retrieval-hints.json` is an extra acceleration layer. It records hot buckets, keywords, and `⚡` flash records so high-frequency retrieval reaches useful content faster.

Starting with v7, `retrieval-hints.json` also includes `recent_events`: the 15 most recent event summaries in reverse chronological order, including `id`, `text_preview`, `timestamp`, and `tags`. For questions like "What happened recently?", the agent can read this field directly instead of scanning all event records.

### 3.3 Knowledge Graph Construction

During restructuring, the system automatically extracts **entities** and **relations** from text to build a lightweight knowledge graph:

- Entity extraction identifies people, tools, systems, concepts, and similar items.
- Relation extraction recognizes predicates such as `uses / manages / belongs_to / likes`, and also supports key-value semantics such as `Name: Alice`.
- Relation normalization maps multiple phrasings to canonical predicates and removes redundancy.
- Canonical-value decisions automatically select the primary relation under the same `(subject, predicate)` pair and write it into `relation_decisions.jsonl`.

This lets OpenClaw answer questions like "What is the relationship between X and Y?" or "Who used which tool?" through graph data instead of relying only on text similarity.

### 3.4 Incremental Version Replacement (No Duplicate Pile-Up)

Naive append-only memory grows forever and stores multiple versions of the same fact. This tool switches to **file-level replacement semantics**:

1. Detect that `memory/2026-03-20.md` has changed.
2. Find and remove all previous L2 records that came from that file.
3. Re-extract the file's new content.
4. Increment the version using `(source_path, locator)` as the key and write the new records.

If the content changes, the record gets updated. Multiple active versions of the same item do not pile up.

### 3.5 Retrieval-Enhanced Deduplication

During incremental ingestion, every new record runs through OpenClaw's native `memory search` before it is written:

- Content above the similarity threshold is rejected as a duplicate.
- Gray-zone matches (score 0.55 to 0.85) are sent to the LLM for a `duplicate / distinct` decision to reduce false deletion risk. This was added in v3 and falls back to rules when LLM support is unavailable.
- Quality-gate rejections are written to `reports/rejected_*.jsonl`, and dedup decisions are written to `reports/dedup_audit_*.jsonl`, so both paths remain auditable.
- v7.2 adds fault tolerance: semantic dedup now tolerates single search failures such as timeouts. It is only disabled after 3 consecutive failures. Any successful search resets the counter. Reports expose the reason through `retrieval_disabled_reason`, for example `search_failed_consecutively:3:timeout`.
- v7.2 also adds LLM review for suspicious duplicates in the 0.55 to 0.85 band. Dedup only happens when the LLM explicitly answers `duplicate`. Without an LLM, the original rule path is used.

This design reuses the memory backend that ships with OpenClaw itself. No external embedding service is required.

After calling `scripts/native_memory_search.py`, the returned JSON may include a `rerank` field:

```json
{
  "result": [...],
  "rerank": {
    "summary": "a concise context summary within 200 words",
    "used_indices": [0, 2],
    "filtered_indices": [1, 3],
    "ok": true
  }
}
```

### 3.6 TTL + Heat (Automatic Aging)

Each record gets three lifecycle fields when written:

| Field | Meaning | Example |
|---|---|---|
| `ttl` | time to live | `7d / 90d / permanent` |
| `heat` | access heat | `hot / warm / cold` |
| `last_accessed` | latest access time | `2026-03-20T10:00:00Z` |

`self_evolve --repair` checks TTL as follows:

- Past TTL -> downgrade to `cold`
- Past 2 × TTL -> move into `archive/` automatically so it no longer occupies the main index

### 3.7 Self-Healing Loop (probe -> verify -> repair)

Whenever a meaningful change happens, `self_evolve.py` runs automatically:

- Cross-layer consistency checks for L0 / L1 / L2
- Duplicate detection and cleanup
- Weak-relation ratio checks in the knowledge graph (`cooccurs_with` overuse triggers recalculation)
- TTL and heat hygiene checks
- Memory fragmentation detection: records in the same bucket with term overlap >= 45% are marked as merge candidates, and merged automatically when LLM support is available (Probe 9)
- Preference/profile contradiction detection: conflicting memories under the same entity are resolved by the LLM, and the newer one is kept (Probe 10)
- Repair strategy: rebuild all materialized views from L2 as the source of truth
- Report hygiene: older reports are cleaned up automatically, and only the latest 20 are kept (added in v7.2)
- v7.2.1 enhancement: graph rebuilding now prefers LLM-based joint extraction of entities and relations such as `Alice -> owns -> search-recommendation`. The rule engine remains as a supplement. LLM calls during one repair run count toward the global limit.

---

### 3.8 P0 Governance Fields and Memory Policy (Added in v5)

If you declare `memory_policy: private` in file frontmatter, or wrap content in `<private>...</private>` inside the body, the agent will keep that content out of retrieval layers.

- `persist`: normal long-term storage (default)
- `ephemeral`: extraction is allowed, but the record is forced into `memory.working` with a 7-day TTL and does not enter the long-term layer
- `private`: the content does not enter L2, the graph, or retrieval layers, and the interception is written to `reports/policy_skipped_*.jsonl`

v7.2 also strengthens privacy: content wrapped in `<private>...</private>` no longer keeps any preview text after redaction. Earlier versions kept the first 48 characters, which could still leak sensitive information.

| Field | Example Values | Meaning |
|---|---|---|
| `memory_function` | `factual / experiential / working` | functional type of the memory |
| `formation_mode` | `bootstrap / ingest / runtime / manual` | how the record was created |
| `trust_tier` | `curated / extracted / generated` | trust level (`curated` is highest) |
| `memory_policy` | `persist / private / ephemeral` | persistence policy |

Starting with v5, every L2 record carries these 4 governance fields in addition to the original bucket classification. They add four extra dimensions: functional type, formation mode, trust tier, and persistence policy.

### 3.9 Progressive Disclosure Retrieval Protocol (Added in v5)

**Rerank priority rule:** after `native_memory_search.py` returns, if `rerank.ok == true`, the agent should **prefer `rerank.summary` as context** and ignore the raw `result` list. The summary has already filtered irrelevant fragments and merged semantic duplicates. Only fall back to raw results when `rerank.ok == false`.

The machine-readable protocol lives in `layers/retrieval_protocol.json`. A human-readable reference is also available in `references/retrieval-protocol.md`.

- **Step 1** -> `layers/profile_snapshot.json` + `preferences_snapshot.json`: current-state user profile and preference snapshots. If the question is "Who is this user?" or "What do they prefer?", stop here when matched.
- **Step 2** -> `layers/retrieval-hints.json`: hot buckets, keywords, and `⚡` flash records used for bucket routing decisions.
- **Step 3** -> `layers/l1_overview.jsonl`: bucket summaries. Stop here if the answer can be produced at bucket level.
- **Step 4** (only when needed) -> `layers/l2_records.jsonl`: full atomic records. Open this layer only for source evidence, `source_path`, or version verification.

Starting with v5, L0/L1/L2 are organized into an explicit progressive-disclosure read order. The goal is to let the agent answer from the lightest layer possible and only open full L2 records when necessary, saving roughly 95% of token load.

## 4. How to Confirm Runtime Status

### Method 1: Check the Persisted Status File (Recommended, Added in v6)

Starting with v6, every bootstrap run writes a persistent status file into the workspace, so verification no longer depends on bootstrap injection. The exact path comes from `last_status.json.summary.target_root` and varies depending on whether a `memory/` directory exists.

Ask OpenClaw:

> Help me find `.adaptr-v1/state/last_status.json`, then read `summary.target_root` and `summary.status`.

- `summary.status == "ok"`: the run succeeded
- `summary.status == "warning"`: open `last_status.md` and inspect `Error code / Error detail / Hints`
- `last_status.md` is the human-readable version. Machine-readable checks should always use `last_status.json`

### Method 2: Bootstrap Injected File (If Supported by the Client)

If your client supports bootstrap injection, the new session context may show `MEMORY_KNOWLEDGE_AUTO_MIGRATE.md`, with content like this:

⚠️ This is a **virtual file**. It does not exist on disk, and not finding it through a full search is expected. Method 1 is always available.

```markdown
## Memory Migration Done

Workspace (event): /root/.openclaw/workspace
Skill sync: ok - skill already synced: ~/.openclaw/skills/openclaw-memory-knowledge
Migration: ok - memory/knowledge migration executed
Run mode: ingest
Meaningful changes: yes
Migrate report: .adaptr-v1/reports/ingest_20260325T050000Z.json
Self-evolve report: .adaptr-v1/reports/self_evolve_20260325T050012Z.json
```

### Method 3: Read the Report Files (For Detailed Analysis)

```bash
# Use last_status.json.summary.target_root as target_root
# Ask OpenClaw directly, or compute it yourself:
# python3 -c "import json,os; [print(json.load(open(p))['summary']['target_root']) for p in [os.path.expanduser('~/.openclaw/workspace/memory/.adaptr-v1/state/last_status.json'), os.path.expanduser('~/.openclaw/workspace/.adaptr-v1/state/last_status.json')] if os.path.exists(p)]"

# Latest bootstrap / ingest reports
ls -lt <target_root>/reports/ | head -5

# Global statistics (total volume, hot buckets)
cat <target_root>/layers/l0_abstract.json

# Retrieval hot hints (flash records, hot-bucket keywords)
cat <target_root>/layers/retrieval-hints.json
```

## 5. Manual Mode (Advanced Usage)

Only read this section if you do not want to rely on the hook's automatic mode.

### Preview Without Writing

```bash
cd ~/.openclaw/workspace
python ~/.openclaw/skills/openclaw-memory-knowledge/scripts/auto_migrate.py --no-apply
```

### Force a Full Restructure

```bash
python ~/.openclaw/skills/openclaw-memory-knowledge/scripts/auto_migrate.py --mode bootstrap
```

### Force Incremental Ingestion

```bash
python ~/.openclaw/skills/openclaw-memory-knowledge/scripts/auto_migrate.py --mode ingest
```

### Manually Specify the Workspace (If Auto Discovery Fails)

```bash
python ~/.openclaw/skills/openclaw-memory-knowledge/scripts/auto_migrate.py \
  --workspace-root /path/to/openclaw-or-workspace
```

### Run Only Self-Healing

```bash
python ~/.openclaw/skills/openclaw-memory-knowledge/scripts/self_evolve.py \
  --workspace-root /root/.openclaw/workspace \
  --repair
```

### Directly Specify Source Paths (Completely Skip Auto Discovery)

```bash
python ~/.openclaw/skills/openclaw-memory-knowledge/scripts/bootstrap_restructure.py \
  --memory-path /path/to/memory \
  --knowledge-path /path/to/knowledge \
  --target-root /path/to/arch_root \
  --apply
```

### Search Result Post-Processing (Added in v7.2.1)

After calling OpenClaw native search, `native_memory_search.py` automatically uses the LLM to post-process the results:

1. **Filter**: remove fragments unrelated to the query
2. **Deduplicate**: merge semantically repeated content
3. **Summarize**: produce a context summary within 200 words

The `rerank` field in the output JSON contains:

- `summary`: the LLM-generated context summary
- `used_indices`: indices of the results that were kept
- `filtered_indices`: indices of the results that were filtered out
- `ok`: whether the LLM call succeeded. On failure, all raw results are returned.

---

## 6. FAQ

| Q | A |
|---|---|
| **Why not use a vector database?** | Vector databases such as Chroma and Milvus provide millisecond-level semantic retrieval and are common in RAG systems. This direction was evaluated during design, but rejected for three reasons. First, **deployment complexity**: a vector database needs a separate process and an embedding model service. That is too heavy for a lightweight personal assistant setup such as OpenClaw. Second, **scale mismatch**: the real advantage of vector retrieval appears at millions of records. Personal workspaces usually hold thousands, where structured directories plus keyword retrieval are already enough. Third, **auditability**: vector results are difficult to explain. Why did one record score 0.87 and another 0.83? Debugging and tuning become much harder. This tool keeps the retrieval path rule-driven and traceable. |
| **Why exactly 8 buckets? Why not fewer or more?** | The number comes from a practical merge of AdaMem's four-layer classification and the five-layer scheme in the memory survey. Fewer buckets, such as 3, mix too many memory types together and still require LLM post-routing at retrieval time. Too many buckets, such as 20, increase user cognitive load and make the rules harder to maintain. Eight buckets cover the major types clearly: profile, preference, events, skills, working state, and three knowledge categories. |
| **Why does the persona layer require at least two independent pieces of evidence?** | This is a guardrail against overconfident writes. If the agent infers "the user dislikes emojis" from one conversation and writes it into persona immediately, that mistake can affect every later conversation. Persona traits are supposed to be stable and expensive to correct. Requiring at least two independent confirmations sharply reduces the risk that a one-off behavior is mistaken for a lasting trait. |
| **Will this damage my existing memory files?** | No. Existing `.md` files stay untouched. `.adaptr-v1/` is a new directory. The initial bootstrap pass also backs up old data automatically. |
| **Is it normal that the first run is slow?** | Yes. The first run is a full `bootstrap` scan, so runtime depends on how many memory files you have. In most cases it takes 1 to 3 minutes. Later `ingest` runs only process changed files and are much faster. |
| **I see `Memory Migration Warning`. What should I do?** | Check `summary.status` in `last_status.json`, then inspect `Error code / Error detail / Hints` in `last_status.md`. The most common reason is that the bootstrap event did not contain a trusted workspace path. `MEMORY_KNOWLEDGE_AUTO_MIGRATE.md` is a virtual injected file, so you should not search the entire disk for it. |
| **Does this depend on a localhost embedding service?** | No. The Python side uses only the standard library. Retrieval-enhanced deduplication calls OpenClaw native `memory search` directly and does not require any external embedding HTTP service. |
| **How does deduplication work? Can I audit it?** | During incremental ingestion, every new record runs through OpenClaw `memory search`. Content above the similarity threshold is rejected. Each ingest run produces `reports/dedup_audit_*.jsonl`, which you can inspect directly. |
| **Is the hook always running? Will it hurt performance?** | No. It only runs on `agent:bootstrap`, meaning once when you open a new session. Subagent sessions are skipped automatically, so frequent subagent creation does not trigger repeated runs. |
| **Can archived content still be found?** | Yes. `viking/archive/records.jsonl` keeps all archived records. They are not deleted. They are only removed from the main retrieval index to reduce noise during normal recall. |
| **Which file formats are supported?** | `.md / .txt / .log / .yaml / .yml / .json / .jsonl / .csv / .db / .sqlite`. Files such as `.abstract.md` and `.overview.md` are detected as index files and skipped automatically to avoid duplicate ingestion. |
| **A new session says "another migration process is running". What now?** | Before v7, a crashed or force-terminated script could leave behind an orphan lock file (`.auto_migrate.lock`), which then had to be removed manually. This is fixed in v7.2. The lock file now records a PID, checks whether the process is still alive, and auto-releases if it is stale for over 300 seconds. Manual cleanup should no longer be necessary in normal cases. In v7.1 and earlier, you may still need to remove `.auto_migrate.lock` by hand. |
| **Why are there two copies of `scripts/`?** | `handler.js` loads scripts from `__dirname/scripts/auto_migrate.py` in the hook path, while the skill system loads from `skills/openclaw-memory-knowledge/scripts/` in the skill path. Both entry points must exist, and their contents must stay aligned. Starting with v7.2, packaging uses hard links so the zip size is much smaller. |
| **How many LLM-enhanced stages are enabled?** | Starting with v7.1 there are 8 optional LLM-enhanced stages: classification fallback, semantic chunking, gray-zone dedup decisions, retrieval post-processing, write-quality gating, joint entity+relation extraction, memory merging during self-heal, and contradiction detection. All of them fall back silently to rules when no LLM is available. |
| **Will memory keep growing forever?** | Before v7, the system only replaced versions when the same file changed, so different files describing the same fact could still accumulate. v7 adds memory merging during self-healing: highly similar records in the same bucket are detected and merged into one refined memory entry, which actively reduces fragmentation. |
| **I got `ModuleNotFoundError` right after installation.** | Fixed in v7.2. In v7.1 and earlier, if the hook execution directory was not `scripts/`, Python could fail to find `mk_arch_core`. Upgrade to v7.2. If the issue remains, check whether `handler.js` contains `cwd: path.dirname(scriptPath)`. |

---

## 7. Dependencies

| Item | Requirement |
|---|---|
| Python | Standard library only, no `pip install` |
| OpenClaw | Requires working `memory search` |
| External embedding service | ❌ Not required |
| Special system privileges | ❌ Not required |

---

## 8. File Structure at a Glance

```text
memory-knowledge-auto-migrate-hook-v7.zip
└── memory-knowledge-auto-migrate/
    ├── HOOK.md                    # hook metadata (trigger event, description)
    ├── INSTALL.md                 # installation guide (authoritative entry point)
    ├── SKILL.md                   # skill overview (root copy)
    ├── handler.js                 # hook entrypoint (triggered by agent:bootstrap)
    ├── scripts/                   # handler.js loads from here first
    │   ├── auto_migrate.py           # zero-config entrypoint (bootstrap / ingest auto-switch)
    │   ├── bootstrap_restructure.py  # full restructure
    │   ├── incremental_ingest.py     # incremental ingestion
    │   ├── self_evolve.py            # self-healing
    │   ├── mk_arch_core.py           # core library
    │   ├── native_memory_search.py   # native search wrapper
    │   ├── llm_backend.py            # LLM enhancement backend (added in v3)
    │   └── install_hook_from_zip.sh  # convenience installer
    └── skills/
        └── openclaw-memory-knowledge/
            ├── SKILL.md           # skill overview
            ├── INSTALL.md
            ├── requirements.txt
            ├── scripts/           # skill system loads from here (same contents as root scripts/)
            │   └── (same as above, full copy)
            └── references/
                ├── architecture.md           # detailed architecture notes
                ├── mapping.yaml              # bucket-path mapping config
                ├── zero-config.md            # zero-config usage guide
                └── retrieval-protocol.md     # retrieval protocol notes (added in v5)
```

## 9. LLM Enhancements (Optional)

Starting with v3.0, the tool includes built-in LLM enhancement support through `llm_backend.py`. After installation, no extra configuration is required. It automatically discovers the OpenClaw model configuration from `~/.openclaw/agents/main/agent/models.json`.

**Important fix in v7.0**: v3 through v6.1 had a P1-level bug. In automatic hook mode, `_get_llm()` always returned `NoopLLMBackend`, which meant the five enhancement stages below never actually ran during automatic execution. They only worked when the scripts were run manually. v7 fixed this by making each child process lazily initialize `OpenclawLLMBackend` from `llm_backend` on first use. The five enhancement stages now work correctly in hook automatic mode.

**Source sync fix in v7.2**: in v7.1, the LLM-enhancement code existed only in the dist package and was missing from the source repository. Re-packaging from source silently disabled the five enhancement stages. v7.2 merged all LLM integration code back into source so `package_hook.sh` now produces an artifact that matches source exactly.

### Enhancement Stages

| Stage | Trigger Condition | Effect |
|---|---|---|
| Classification fallback | rule confidence < 0.7 | semantic classification replaces keyword guessing, which prevents lines like "Qiushi likes coding late at night" from being misrouted into `working` |
| Joint entity + relation extraction | text length >= 60 chars and LLM available | the LLM extracts named entities and relation triples in one pass, replacing pure regex extraction. Rule extraction remains as a supplement. This greatly improves graph quality. |
| Semantic chunking | no heading structure + text > 100 chars | unstructured diary text is split into memory units by semantic boundaries instead of mechanical paragraph cuts |
| Gray-zone dedup decision | similarity score between 0.55 and 0.85 | the model decides `duplicate` or `distinct`, which reduces accidental deletion of similar-but-different records |
| Retrieval post-processing | after each `memory search` result | filters irrelevant fragments, merges semantic duplicates, and generates a summary so the agent receives cleaned context |
| Write-quality gate | text length 50 to 200 chars and rules did not reject it | the model decides whether the text is worth long-term storage, filtering system logs and low-value output |
| Memory merge (self-evolve) | highly similar fragmented records found in the same bucket | during self-heal, the LLM merges multiple related memories into one refined memory entry. Without an LLM, the system reports the issue but does not merge. |
| Preference/profile contradiction detection (self-evolve) | medium-similarity memory pairs exist in preferences/profile buckets | the LLM decides whether two memories contradict each other. If so, the newer record is kept and the older one is removed. Without an LLM, heuristics are used instead. |

### Fallback Guarantee

If the LLM is unavailable because `models.json` is missing, the network times out, or the API returns an error, every stage silently falls back to the rule-based path. Functionality remains complete, but quality is reduced. `stderr` prints a notice:

```text
LLM backend not configured, falling back to rule mode
```

### Call Limit (Added in v7.2)

Within one session, meaning one bootstrap or ingest run, the LLM can be called at most 200 times. After that limit is reached, all enhancement stages silently fall back to rule mode without affecting core functionality. In practice, this means:

- Small workspaces (< 50 files): every record can benefit from LLM enhancement
- Large workspaces (> 200 files): early records use the LLM, and later ones fall back to rules automatically

If you want a different cap, call `mk_arch_core.set_llm_call_limit(N)` in the scripts.

Reports expose the backend used for the run through the `llm_backend` field, which lets you verify whether LLM enhancement was active.

### Environment Variable Overrides

```bash
export CATCLAW_LLM_BASE_URL="https://your-endpoint/v1"
export CATCLAW_LLM_API_KEY="your-key"
export CATCLAW_LLM_MODEL="your-model"
```

## Appendix: Version History

| Version | Date | Changes |
|---|---|---|
| v1.0 | 2026-03-24 | Initial release: bootstrap restructure + automatic hook trigger |
| v2.0 | 2026-03-25 | Incremental UPDATE-style version replacement, dedup audit logs, TTL/heat archival, better relation-graph quality (predicate normalization, `relation_decisions`, weak-relation self-heal), and safer workspace auto-discovery |
| v3.0 | 2026-03-27 | Added `llm_backend.py` with zero-`pip` LLM enhancements, automatic OpenClaw config discovery from `models.json`, five enhancement stages (classification fallback, semantic chunking, gray-zone dedup decisions, retrieval post-processing, write-quality gating), and full fallback guarantees |
| v4.0 | 2026-03-28 | Integrated rerank into `native_memory_search.py`: retrieval post-processing (filter irrelevant, merge duplicates, summarize) is now in the main retrieval chain; `auto_migrate.py` summary adds `llm_backend` |
| v5.0 | 2026-03-29 | 1) `SKILL.md` now warns users not to run scripts manually in hook automatic mode. 2) Added retrieval-result usage rules to `SKILL.md` (`rerank.ok==true` means prefer `rerank.summary`). 3) `auto_migrate.py` summary adds `duration_seconds` and `processed_files`. 4) `INSTALL.md` verification expanded to 4 steps. 5) `llm_backend.py` clears `default_base_url` and degrades immediately with a `stderr` warning when not configured. 6) Added P0 governance fields (`memory_function/formation_mode/trust_tier/memory_policy`). 7) Added the Progressive Disclosure retrieval protocol plus `profile_snapshot` / `preferences_snapshot` snapshot layers. |
| v6.0 | 2026-03-31 | 1) `handler.js` adds `persistLastStatus()`, which always writes `last_status.md` and `last_status.json` after bootstrap, even if the client does not support injection. 2) `handler.js` adds a `bootstrapFiles = []` fallback. 3) `processed_files` is now split by run mode. 4) All docs now use `last_status.json.summary.target_root` as the source of truth. |
| v6.1 | 2026-04-01 | 1) Fixed path offset in `handler.js getOpenclawHome()`: when `OPENCLAW_HOME` points to the home directory and `HOME/.openclaw` exists, that path is preferred so skill sync lands under `~/.openclaw/skills/`. 2) Fixed hard-coded paths in INSTALL steps 3 and 5 by switching to dynamic Python-based discovery. 3) Added troubleshooting guidance for skill-sync path offset when `SKILL_DIR=NOT_FOUND`. 4) Convenience installation now uses the `$SKILL_DIR` variable. |
| v7.0 | 2026-04-03 | **LLM pipeline fix**: 1) `mk_arch_core._get_llm()` and `incremental_ingest._get_ingest_llm()` now use lazy auto-discovery, so each child process initializes `OpenclawLLMBackend` independently on first call. This fixes the broken LLM-enhancement path in hook automatic mode from v3 through v6.1. 2) `bootstrap_restructure` and `incremental_ingest` reports now include `llm_backend`, and `auto_migrate.py` trusts child-process reports first, so the summary field is now accurate. **Stability**: 3) `auto_migrate.py` adds `_is_stale_lock()` with PID liveness checks plus a 300-second mtime timeout, so orphan `.auto_migrate.lock` files are cleaned automatically. 4) `handler.js` now validates `summary.target_root` before fallback and prefers probing `memory/` to decide the `.adaptr-v1` location. **Performance and cleanup**: 5) `should_reingest` adds an mtime+size fast path to avoid SHA256 reads for unchanged files. 6) `write_jsonl` correctly clears files on `append=False` instead of silently skipping empty lists. 7) Removed 16 duplicate `semantic*` stats fields, a redundant `import json as _json`, and an inline `import('time')` in favor of standard imports. |
| v7.1 | 2026-04-03 | Deeper LLM use: 1) added `_llm_extract_entities_and_relations()` for joint entity+relation extraction, with rule results merged as supplements. 2) Raised the classification fallback threshold from 0.70 to 0.80. 3) Added Probe 9 for memory-fragmentation detection plus automatic LLM merge, and Probe 10 for preference/profile contradiction detection plus automatic keep-the-newer-value behavior. 4) Added `recent_events` time-order index to `retrieval-hints.json`. |
| v7.2 | 2026-04-04 | 1) Added `llm_backend.py` to the source repository and synced `package_hook.sh`, so source packaging now matches the dist package. 2) `self_evolve.py` now supports LLM-based memory merge, contradiction detection, and joint entity+relation extraction during graph rebuild. 3) `incremental_ingest.py` semantic dedup now tolerates single failures and only disables after 3 consecutive failures. 4) Gray-zone dedup now calls the LLM for `duplicate / distinct` decisions. 5) `<private>` segments no longer retain preview text after redaction. 6) `auto_migrate.py` now groups report paths, warnings, and changed files by mode. 7) Old report cleanup keeps only the latest 20. 8) `INSTALL.md` explicitly requires starting a new session after installation. |
| v7.2.1 | 2026-04-05 | 1) Added `native_memory_search.py` post-processing output with the `rerank` field, including `summary`, `used_indices`, `filtered_indices`, and `ok`. 2) `self_evolve.py` graph rebuild now prefers LLM-based joint extraction and uses rules as supplements. 3) Added OpenClaw native search post-processing to the README and docs. |
