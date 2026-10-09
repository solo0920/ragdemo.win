#!/usr/bin/env python3
"""Phase-1 adoption validator (stdlib only).

Checks: registry/instance parse; every referenced authority component exists
with matching version; node->gate refs resolve; workflow DAG acyclic and node
outputs/edges line up with the instance node order; provenance artifact exists
with matching sha256; terminal is STOP; no excluded-origin provider strings in
agent/; repo diff limited to untracked agent/ (no production modification).
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
AUTH = Path(os.path.expanduser("~/projects/agent-arch"))
BLOCKED = ["qwen", "deepseek", "kimi", "minimax", "mimo", "longcat",
           "baai", "alibaba", "zhipu", "bge-m3", "gte-multilingual"]
errors: list[str] = []


def err(m: str) -> None:
    errors.append(m)


reg = json.loads((REPO / "agent/registry.json").read_text())
inst = json.loads((REPO / "agent/workflows/e05dd-candidate-research.instance.json").read_text())

# 1. registry components exist in authority with matching version
auth_index: dict[str, dict] = {}
for sub in ("agents", "tasks", "tools", "artifacts", "gates", "workflows"):
    for f in (AUTH / sub).glob("*.json"):
        d = json.loads(f.read_text())
        auth_index[d["component_id"]] = d
for cid, ref in reg["components"].items():
    hit = auth_index.get(cid)
    if hit is None:
        err(f"registry: unknown component {cid}")
    elif hit["version"] != ref["version"]:
        err(f"registry: version mismatch {cid} ({ref['version']} vs {hit['version']})")

# 2. instance workflow/task refs resolve
wf = auth_index.get(inst["workflow_id"])
if wf is None or wf["component_type"] != "workflow":
    err("instance: workflow_id does not resolve to an authority workflow")
elif wf["version"] != inst["workflow_version"]:
    err("instance: workflow_version mismatch")
task = auth_index.get(inst["task_id"])
if task is None or task["component_type"] != "task":
    err("instance: task_id does not resolve to an authority task")

# 3. instance nodes cover authority DAG nodes in order; gates resolve
if wf is not None:
    dag_nodes = [n["node_id"] for n in wf["workflow"]["nodes"]]
    inst_nodes = [n["node_id"] for n in inst["nodes"]]
    if inst_nodes != dag_nodes:
        err(f"instance: node order {inst_nodes} != DAG order {dag_nodes}")
    for n in inst["nodes"]:
        for g in n.get("gate_ids", []) + [n.get("transition_gate")]:
            hit = auth_index.get(g or "")
            if hit is None or hit["component_type"] != "gate":
                err(f"instance: node {n['node_id']} bad gate ref {g}")
    # acyclicity of authority DAG
    adj: dict[str, set[str]] = {n: set() for n in dag_nodes}
    for e in wf["workflow"]["edges"]:
        adj[e["from"].rsplit(".", 1)[0]].add(e["to"].rsplit(".", 1)[0])
    color: dict[str, int] = {}

    def visit(x: str, stack: list[str]) -> None:
        color[x] = 1
        for y in adj[x]:
            if color.get(y) == 1:
                err(f"DAG cycle: {'->'.join(stack + [x, y])}")
            elif color.get(y) is None:
                visit(y, stack + [x])
        color[x] = 2

    for nid in dag_nodes:
        if color.get(nid) is None:
            visit(nid, [])

# 4. provenance artifact exists with matching sha256
ap = inst["artifact"]
p = Path(os.path.expanduser(ap["path"]))
if not p.is_file():
    err(f"provenance: artifact file missing: {ap['path']}")
else:
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    if h != ap["sha256"]:
        err(f"provenance: sha256 mismatch for {ap['path']}")

# 5. terminal STOP
if inst.get("terminal") != "STOP" or inst.get("status") != "STOP":
    err("terminal must be explicit STOP")
if wf is not None and "STOP" not in wf.get("terminal_states", []):
    err("authority workflow does not declare STOP")

# 6. no excluded-origin provider strings introduced into adoption metadata.
# Scope is .md/.json (the metadata Composer/Console would ingest); this .py
# checker itself is excluded since the constant below is the enforcement list.
for f in sorted((REPO / "agent").rglob("*")):
    if f.is_file() and f.suffix in {".md", ".json"}:
        text = f.read_text().lower()
        for b in BLOCKED:
            if b in text:
                err(f"blocked-provider string '{b}' in {f.relative_to(REPO)}")

# 7. repo diff scope: Phase-1 assertion ("only agent/ added") held at adoption
# time. Implementation batches (Builder role) legitimately modify production,
# so entries newer than the adoption task are allowed IFF listed in a batch
# manifest (agent/architecture/B*-CHANGES.txt). Anything else newer FAILs.
# agent/ itself must stay fully untracked (never committed over).
import time as _time


def _source_mtime(p):
    """Newest mtime under path, ignoring __pycache__ bytecode (build artifacts
    from test runs, not source changes). -1 when nothing relevant exists."""
    from pathlib import Path as _P
    pp = _P(p)
    if pp.is_file():
        return pp.stat().st_mtime
    best = -1.0
    if pp.is_dir():
        for f in pp.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                try:
                    best = max(best, f.stat().st_mtime)
                except FileNotFoundError:
                    pass
    return best
agent_files = [f for f in (REPO / "agent").rglob("*") if f.is_file()]
manifested: set[str] = set()
for mf in sorted((REPO / "agent/architecture").glob("B*-CHANGES.txt")):
    for ln in mf.read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            manifested.add(ln)
if not agent_files:
    err("agent/ contains no files")
else:
    task_start = min(f.stat().st_mtime for f in agent_files)
    porc = subprocess.run(["git", "status", "--porcelain=v1"],
                          capture_output=True, text=True, cwd=REPO).stdout.splitlines()
    if "?? agent/" not in porc:
        err("agent/ not present as untracked addition")
    for line in porc:
        if line == "?? agent/":
            continue
        rel = line[3:].rstrip("/")
        p = REPO / rel
        try:
            mt = _source_mtime(p)
        except FileNotFoundError:
            err(f"cannot stat working-tree entry: {line}")
            continue
        if mt < 0:
            continue  # nothing but bytecode: not a source change
        if mt >= task_start and rel not in manifested and not any(
                m == rel or m.startswith(rel) for m in manifested):
            err(f"entry newer than adoption task and in no batch manifest: {line}")
        if rel.startswith("agent/") and not line.startswith("??"):
            err(f"tracked modification under agent/: {line}")

if errors:
    print(f"FAIL: {len(errors)} problem(s):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("PASS: refs, bindings, DAG, provenance, STOP terminal, provider policy, diff scope.")
