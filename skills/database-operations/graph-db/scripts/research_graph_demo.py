#!/usr/bin/env python3
"""Seed and query a tiny CogDB research/planning graph.

Example:
  python research_graph_demo.py --state-dir /tmp/cogdb-demo-state
"""
import argparse
import json
from pathlib import Path

from cog.config import CogConfig
from cog.torque import Graph


def open_graph(state_dir: str, name: str) -> Graph:
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    cfg = CogConfig(COG_HOME="cogdb", COG_PATH_PREFIX=state_dir)
    return Graph(name, config=cfg)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default="./.agent-state", help="Durable CogDB state root")
    ap.add_argument("--graph", default="research_demo", help="CogDB graph name")
    args = ap.parse_args()

    g = open_graph(args.state_dir, args.graph)
    g.put_batch([
        ("goal:evaluate-cog", "requires", "task:inspect-readme"),
        ("goal:evaluate-cog", "requires", "task:run-tests"),
        ("task:write-recommendation", "blocked_by", "task:run-tests"),
        ("task:write-recommendation", "blocked_by", "task:inspect-code"),
        ("claim:cogdb-useful-as-graph-overlay", "supported_by", "source:README"),
        ("claim:concurrency-risk", "supported_by", "source:database.py:update_edge"),
    ])

    out = {
        "graph": args.graph,
        "state_dir": str(Path(args.state_dir).resolve()),
        "queries": {
            "requirements": g.v("goal:evaluate-cog").out("requires").all()["result"],
            "blockers": g.v("task:write-recommendation").out("blocked_by").all()["result"],
            "risk_claims": g.v("source:database.py:update_edge").inc("supported_by").all()["result"],
        },
    }
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
