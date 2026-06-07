---
name: graph-db
description: Use when building or querying a lightweight CogDB graph layer for agentic research, planning, provenance, citations, blockers, or markdown-derived relationship indexes.
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [cogdb, graph, research, planning, provenance]
    related_skills: [database-operations, obsidian]
---

# Graph DB

## Overview

Use CogDB as a **derived graph index** over durable notes/data. It is useful for agent planning and research because it makes relationships queryable without replacing the canonical archive.

Best stack:

```text
Markdown / JSONL / SQLite = canonical archive
CogDB = relationship traversal layer
```

## When to Use

Use for:

- claim/source/provenance graphs
- task blockers and plan dependencies
- citation/literature maps
- entity/topic/source relationships
- markdown-derived wikilink/tag indexes
- lightweight local graph prototypes for agents

Do not use for:

- the only source of truth
- production transactional storage
- concurrent multi-writer agent memory
- full-text search or vector ANN search
- data that needs mature backups, replication, ACLs, or operations tooling

## Setup

```bash
python3 -m pip install cogdb==3.8.2
```

Always configure a durable path. CogDB defaults under `/tmp`; these scripts default to `./.agent-state`, so pass `--state-dir` for project-specific state.

```python
from cog.torque import Graph
from cog.config import CogConfig

cfg = CogConfig(COG_HOME="cogdb", COG_PATH_PREFIX="/path/to/project/.agent-state")
g = Graph("research", config=cfg)
```

## Good Edge Shapes

```text
claim_x --supported_by--> source_y
task_a --blocked_by--> task_b
goal_g --requires--> task_t
paper_a --cites--> paper_b
note_n --mentions--> entity_e
answer_a --derived_from--> source_s
```

## Query Patterns

```python
# blockers
g.v("task:write-report").out("blocked_by").all()

# provenance
g.v("claim:graph-useful").out("supported_by").all()

# context expansion
g.v("goal:evaluate-cog").bfs(direction="out", max_depth=2).all()

# reverse lookup
g.v("source:README").inc("supported_by").all()
```

## Rules

- Keep source text/data canonical elsewhere; write graph edges as an index.
- Use stable IDs: `note:path-slug`, `source:url-sha`, `claim:slug`, `task:slug`.
- Prefer JSON/JSONL script output so agents can parse it reliably.
- Use one-shot traversal chains starting with `v()`; `Graph` keeps mutable traversal state.
- Do not share one mutable `Graph` object across concurrent writers.
- Avoid exposing `g.serve()` to untrusted users; remote query execution uses restricted `eval()`.
- Prefer SQLite/Postgres/NoSQL for transactions, concurrent writes, full-text search, backups, and production durability.

## Example Scripts

Linked scripts:

- `scripts/research_graph_demo.py` — seed/query a claim-task-source graph; emits JSON.
- `scripts/markdown_link_index.py` — index markdown wikilinks and tags into CogDB; supports `--dry-run` and emits JSON.

Run:

```bash
python scripts/research_graph_demo.py --state-dir /tmp/cogdb-demo-state

mkdir -p /tmp/cogdb-vault
printf '# A\n\nLinks to [[B]] and #research.\n' > /tmp/cogdb-vault/A.md
printf '# B\n' > /tmp/cogdb-vault/B.md
python scripts/markdown_link_index.py /tmp/cogdb-vault --state-dir /tmp/cogdb-demo-state --dry-run
python scripts/markdown_link_index.py /tmp/cogdb-vault --state-dir /tmp/cogdb-demo-state
```

Expected output shape:

```json
{"graph": "notes", "notes_indexed": 2, "edges": 3}
```

## Verification Checklist

- [ ] `python3 -m pip install cogdb==3.8.2` succeeds in the active environment.
- [ ] `research_graph_demo.py` emits valid JSON with requirements/blockers/provenance keys.
- [ ] `markdown_link_index.py --dry-run` emits valid JSON triples without writing.
- [ ] Persistent uses pass a non-`/tmp` `--state-dir`.
