#!/usr/bin/env python3
"""Index markdown wikilinks and tags into CogDB.

Creates edges:
  note:<relative-path-slug> --path--> /absolute/file.md
  note:<relative-path-slug> --links_to--> note:<target-slug>
  note:<relative-path-slug> --tagged--> tag:<tag>

Example:
  python markdown_link_index.py ~/wiki --state-dir ./.agent-state --dry-run
"""
import argparse
import json
import re
import sys
from pathlib import Path

from cog.config import CogConfig
from cog.torque import Graph

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
TAG_RE = re.compile(r"(?<![\w:/])#([A-Za-z0-9_/-]+)")
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return s or "untitled"


def note_id_for_path(path: Path, vault: Path) -> str:
    rel = path.relative_to(vault).with_suffix("").as_posix()
    return f"note:{slug(rel)}"


def clean_markdown(text: str) -> str:
    text = FRONTMATTER_RE.sub("", text)
    return FENCE_RE.sub("", text)


def open_graph(state_dir: str, name: str) -> Graph:
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    cfg = CogConfig(COG_HOME="cogdb", COG_PATH_PREFIX=state_dir)
    return Graph(name, config=cfg)


def resolve_link(link: str, basename_index: dict[str, list[str]]) -> str:
    target = link.strip().removesuffix(".md")
    if "/" in target or "\\" in target:
        normalized = target.replace("\\", "/")
        return f"note:{slug(normalized)}"
    matches = basename_index.get(slug(target), [])
    if len(matches) == 1:
        return matches[0]
    return f"note:{slug(target)}"


def build_triples(vault: Path) -> list[tuple[str, str, str]]:
    md_files = sorted(vault.rglob("*.md"))
    basename_index: dict[str, list[str]] = {}
    for path in md_files:
        basename_index.setdefault(slug(path.stem), []).append(note_id_for_path(path, vault))

    triples: list[tuple[str, str, str]] = []
    for path in md_files:
        note_id = note_id_for_path(path, vault)
        text = clean_markdown(path.read_text(encoding="utf-8", errors="ignore"))
        triples.append((note_id, "path", str(path.resolve())))
        for link in WIKILINK_RE.findall(text):
            triples.append((note_id, "links_to", resolve_link(link, basename_index)))
        for tag in TAG_RE.findall(text):
            triples.append((note_id, "tagged", f"tag:{slug(tag)}"))
    return triples


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("vault", help="Markdown/Obsidian vault path")
    ap.add_argument("--state-dir", default="./.agent-state", help="Durable CogDB state root")
    ap.add_argument("--graph", default="notes", help="CogDB graph name")
    ap.add_argument("--dry-run", action="store_true", help="Print triples without writing")
    args = ap.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    if not vault.exists() or not vault.is_dir():
        print(json.dumps({"error": f"vault is not a directory: {vault}"}), file=sys.stderr)
        raise SystemExit(2)

    triples = build_triples(vault)
    result = {
        "graph": args.graph,
        "vault": str(vault),
        "state_dir": str(Path(args.state_dir).resolve()),
        "dry_run": args.dry_run,
        "notes_indexed": len({t[0] for t in triples}),
        "edges": len(triples),
    }

    if args.dry_run:
        result["triples"] = [{"s": s, "p": p, "o": o} for s, p, o in triples]
    elif triples:
        open_graph(args.state_dir, args.graph).put_batch(triples)

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
