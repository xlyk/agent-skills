# Agent Skills

Reusable agent skills and examples.

## Skills

### graph-db

Lightweight CogDB graph layer for agentic research, planning, provenance, citations, blockers, and markdown-derived relationship indexes.

Path: `skills/database-operations/graph-db/`

Use it when an agent needs to answer graph-shaped questions like:

- what supports this claim?
- what blocks this task?
- what does this note link to?
- what depends on this source?

Canonical data should stay in markdown, JSONL, SQLite, or another durable store. CogDB is the derived relationship index.

### skill-source-discovery

Discovers, inventories, dedupes, scores, and monitors public **sources** of agent skills — repos, directories, awesome-lists, mirrors — across GitHub, GitLab, Bitbucket, Codeberg/Forgejo, SourceHut, generic git, and websites. Discovery is driven by `parallel-cli` web search; the inventory lives in SQLite, where `UNIQUE`/`ON CONFLICT` constraints guarantee no duplicate sources and exactly one notification per change. Emits channel-agnostic events for Hermes to deliver.

Path: `skills/research/skill-source-discovery/`

Use it when you want a durable, triaged catalog of where agent skills live, refreshed on a schedule, with a heads-up when a new source appears:

- which public repos and sites publish agent skills?
- what's new since last week, and is it worth a look?
- is this source legit, a mirror, or noise?

It discovers and inventories only — it never installs, runs, or trusts the skills it finds. The only code is `scripts/normalize.py` (the tested canonical-key helper); everything else is the agent running SQL from `references/sql-cookbook.md`.

## Install locally

Copy or symlink a skill into your Hermes skills directory:

```bash
mkdir -p ~/.hermes/skills/database-operations
cp -R skills/database-operations/graph-db ~/.hermes/skills/database-operations/
```

Then start a new Hermes session or run `/reload-skills` and load:

```text
/skill graph-db
```

For `skill-source-discovery` (discovery needs `parallel-cli` and `PARALLEL_API_KEY`):

```bash
mkdir -p ~/.hermes/skills/research
cp -R skills/research/skill-source-discovery ~/.hermes/skills/research/
# initialize the inventory db (schema is idempotent)
mkdir -p ~/.hermes/skill-source-discovery
sqlite3 ~/.hermes/skill-source-discovery/inventory.db < skills/research/skill-source-discovery/schema.sql
```

## Example scripts

```bash
python skills/database-operations/graph-db/scripts/research_graph_demo.py --state-dir /tmp/cogdb-demo-state

mkdir -p /tmp/cogdb-vault
printf '# A\n\nLinks to [[B]] and #research.\n' > /tmp/cogdb-vault/A.md
printf '# B\n' > /tmp/cogdb-vault/B.md
python skills/database-operations/graph-db/scripts/markdown_link_index.py /tmp/cogdb-vault --state-dir /tmp/cogdb-demo-state --dry-run
```

The scripts require:

```bash
python3 -m pip install cogdb==3.8.2
```
