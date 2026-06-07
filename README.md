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
