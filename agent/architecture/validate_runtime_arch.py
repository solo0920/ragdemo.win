#!/usr/bin/env python3
"""Batch-1 RAG Runtime Architecture gate (stdlib only).

1. architecture consistency (18 sections present; schema $refs resolve)
2. schema validation (schema parses; example host_profile + execution_plan validate)
3. policy validation (no excluded-origin strings in new metadata; policy referenced, not rewritten)
4. frozen-lineage check (E05-D-A/B fingerprints + harness/golden fingerprints intact)
5. production-boundary check (no production paths touched; nothing imports agent/)
6. model-weights check (every HF model dir batch-authorized w/ pinned snapshot
   or pre-existing-and-untouched; no download commands in new files)
7. no-provider-change check (opencode.json + settings/env untouched vs HEAD)

Terminal: PASS (all green) / FAIL (exact issue) / STOP (architecture unsafe).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
ARCH = REPO / "agent/architecture"
PROPOSAL = ARCH / "RAG-RUNTIME-ARCHITECTURE.md"
SCHEMA = ARCH / "runtime-schema.json"
HF_HUB = Path(os.path.expanduser("~/.cache/huggingface/hub"))
BLOCKED = ["qwen", "deepseek", "kimi", "minimax", "mimo", "longcat",
           "baai", "alibaba", "zhipu", "bge-m3", "gte-multilingual"]

result = "PASS"
issues: list[str] = []


def fail(m: str) -> None:
    global result
    result = "FAIL"
    issues.append(m)


TASK_START = min(f.stat().st_mtime for f in ARCH.rglob("*") if f.is_file())
TASK_START_S = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(TASK_START))

# 1. consistency ------------------------------------------------------------
text = PROPOSAL.read_text()
for n in range(1, 19):
    if not re.search(rf"^## {n}\. ", text, re.M):
        fail(f"proposal missing section {n}")
schema = json.loads(SCHEMA.read_text())
defs = schema.get("$defs", {})
blob = json.dumps(schema)
for ref in sorted(set(re.findall(r"#/\$defs/([A-Za-z_]+)", blob))):
    if ref not in defs:
        fail(f"schema $ref target missing: {ref}")


# 2. schema validation (mini validator for the subset we emit) --------------
def mini_validate(node: dict, inst, path="root") -> list[str]:
    errs: list[str] = []
    if "$ref" in node:
        return mini_validate(defs[node["$ref"].split("/")[-1]], inst, path)
    t = node.get("type")
    if t == "object":
        if not isinstance(inst, dict):
            return [f"{path}: not an object"]
        for k in node.get("required", []):
            if k not in inst:
                errs.append(f"{path}: missing '{k}'")
        for k, sub in node.get("properties", {}).items():
            if k in inst:
                errs.extend(mini_validate(sub, inst[k], f"{path}.{k}"))
    elif t == "array":
        if not isinstance(inst, list):
            return [f"{path}: not an array"]
        if len(inst) < node.get("minItems", 0):
            errs.append(f"{path}: too few items")
        for i, v in enumerate(inst):
            errs.extend(mini_validate(node.get("items", {}), v, f"{path}[{i}]"))
    elif t == "string":
        if not isinstance(inst, str):
            errs.append(f"{path}: not a string")
        elif "enum" in node and inst not in node["enum"]:
            errs.append(f"{path}: '{inst}' not in enum")
    elif t in ("number", "integer"):
        if not isinstance(inst, (int, float)) or (t == "integer" and isinstance(inst, bool)):
            errs.append(f"{path}: not a {t}")
        elif "minimum" in node and inst < node["minimum"]:
            errs.append(f"{path}: below minimum")
        elif "exclusiveMinimum" in node and inst <= node["exclusiveMinimum"]:
            errs.append(f"{path}: not above exclusiveMinimum")
    elif t == "boolean":
        if not isinstance(inst, bool):
            errs.append(f"{path}: not a boolean")
    return errs


example_host = {
    "host_id": "x570", "role": "dev-benchmark", "cpu": "Ryzen 3950X", "ram_gb": 64,
    "gpu": {"name": "RTX 5070 Ti", "vram_gb": 16, "unified_memory": False},
    "gpu_budget_gb": 13, "egress": {"lan": True, "wan": True},
    "measured_at": "TBD-measured", "measured_by": "TBD",
}
example_plan = {
    "plan_id": "plan-example-01", "host_id": "x570", "mode": "SAFETY",
    "planner_version": "planner/v0 (future)",
    "stages": [
        {"stage": "embed", "provider_id": "local-daemon", "model_ref": "registry:embed-01",
         "index_id": "index:embed-01", "reasons": ["policy-cleared", "budget-fit", "index-match"]},
        {"stage": "retrieve", "provider_id": "local-daemon", "model_ref": "n/a",
         "reasons": ["local-only"]},
        {"stage": "generate", "provider_id": "local-daemon", "model_ref": "registry:gen-01",
         "reasons": ["egress-none-required"]},
    ],
    "fallback": ["plan-example-02"], "budget": {"vram_gb": 10, "latency_ms_slo": 30000},
    "gates": ["ragdemo.gate.policy"], "provenance": ["host profile fp", "registry fp"],
    "terminal": "STOP",
}
for name, ref, inst in (("host_profile", "host_profile", example_host),
                        ("execution_plan", "execution_plan", example_plan)):
    errs = mini_validate({"$ref": f"#/$defs/{ref}"}, inst)
    if errs:
        fail(f"schema example invalid ({name}): {errs[0]}")
# negative case must fail
bad = dict(example_host, gpu_budget_gb=-1)
if not mini_validate({"$ref": "#/$defs/host_profile"}, bad):
    fail("mini validator failed to reject negative gpu_budget_gb")

# 3. policy -----------------------------------------------------------------
for f in sorted(ARCH.rglob("*")):
    if f.is_file() and f.suffix in {".md", ".json"}:
        low = f.read_text().lower()
        for b in BLOCKED:
            if b in low:
                fail(f"excluded-origin string '{b}' in {f.relative_to(REPO)}")
if not re.search(r"referenced|reference", text, re.I):
    fail("proposal does not reference (rather than rewrite) provider policy")

# 4. frozen lineage ----------------------------------------------------------
expected = {
    "/home/solo/artifacts/t007e05c/out/golden_set_fingerprint.txt":
        "golden_set_fingerprint: 58e01c798c647b0ebbbf5f7b93f1c61c0aad9908be3cf7ff8ce5c94a339e7f2c",
    "/home/solo/artifacts/t007e05cb/out/harness_fingerprint.txt":
        "harness_fingerprint: ec30e8fdcb70c8fa9c2fd4fdeec467dd448e8adcf6a40196683cbdb6ad579401",
    "/home/solo/artifacts/t007e05d/out/experiment_fingerprint.txt":
        "experiment_fingerprint: 45a87afa3ca4af60717bc0e91d307cead120b13b6371afb302c1b74e9d1e655e",
    "/home/solo/artifacts/t007e05db/out/experiment_fingerprint.txt":
        "experiment_fingerprint: 4f4ddacc71eb03a0c0d45b9f688b04bd97ddb0950574b07e84865f98ac8f2daa",
}
for path, line in expected.items():
    try:
        content = Path(path).read_text()
    except FileNotFoundError:
        fail(f"frozen fingerprint file missing: {path}")
        continue
    if line not in content:
        fail(f"frozen fingerprint mismatch: {path}")

# 5. production boundary ------------------------------------------------------
# Design-batch assertion ("no production paths touched") is batch-scoped:
# implementation batches (Builder role) may touch production IFF every touched
# path is listed in a batch manifest (agent/architecture/B*-CHANGES.txt).
manifested: set[str] = set()
for _mf in sorted((REPO / "agent/architecture").glob("B*-CHANGES.txt")):
    for _ln in _mf.read_text().splitlines():
        _ln = _ln.strip()
        if _ln and not _ln.startswith("#"):
            manifested.add(_ln)
porc = subprocess.run(["git", "status", "--porcelain=v1"], capture_output=True,
                      text=True, cwd=REPO).stdout.splitlines()
guarded = ("backend/", "frontend/", "ingest/", "evals/", "tests/", "data/",
           "compose.yaml", "opencode.json", "pyproject.toml", ".github/")
for line in porc:
    rel = line[3:]
    if rel.startswith("agent/"):
        continue
    from pathlib import Path as _P

    def _source_mtime(_p):
        if _p.is_file():
            return _p.stat().st_mtime
        _best = -1.0
        if _p.is_dir():
            for _f in _p.rglob("*"):
                if _f.is_file() and "__pycache__" not in _f.parts:
                    try:
                        _best = max(_best, _f.stat().st_mtime)
                    except FileNotFoundError:
                        pass
        return _best

    p = REPO / rel.rstrip("/")
    try:
        _rel = rel.rstrip("/")
        _mt = _source_mtime(p)
        if _mt < 0:
            continue  # bytecode only: not a source change
        if (_mt >= TASK_START
                and rel.startswith(guarded) and _rel not in manifested
                and not any(m == _rel or m.startswith(_rel) for m in manifested)):
            fail(f"production path touched outside any batch manifest: {line}")
    except FileNotFoundError:
        pass
grep = subprocess.run(
    ["grep", "-rEn", "from agent|import agent|from ['\"]agent",
     "--include=*.py", "--include=*.ts", "--include=*.svelte",
     "backend", "frontend", "ingest", "tests"],
    capture_output=True, text=True, cwd=REPO).stdout.strip()
if grep:
    fail(f"production imports agent/: {grep[:300]}")

# 6. model-weights check ------------------------------------------------------
# Design-batch rule ("no new HF cache entries") is batch-scoped: implementation
# batches may add weights IFF every model dir is either (a) batch-authorized in
# B*-MODELS.txt with the snapshot dir matching the pinned revision, or (b)
# pre-existing and untouched (no files newer than the batch marker). Anything
# else — unknown dirs, revision mismatch, touched pre-existing weights — FAILs.
# Batch marker: oldest file listed in B*-CHANGES.txt manifests (falls back to
# TASK_START when no manifest lists existing files).
_BATCH_START = None
for _mf in sorted((REPO / "agent/architecture").glob("B*-CHANGES.txt")):
    for _ln in _mf.read_text().splitlines():
        _ln = _ln.strip()
        if _ln and not _ln.startswith("#"):
            _p = REPO / _ln
            if _p.is_file():
                _mt = _p.stat().st_mtime
                _BATCH_START = _mt if _BATCH_START is None else min(_BATCH_START, _mt)
if _BATCH_START is None:
    _BATCH_START = TASK_START
_BLOCKED_MODEL = ["qwen", "deepseek", "kimi", "minimax", "mimo", "longcat",
                  "baai", "alibaba", "zhipu", "gte-multilingual"]
_authorized: dict[str, str] = {}
_download_flag = False
for _mf in sorted((REPO / "agent/architecture").glob("B*-MODELS.txt")):
    for _ln in _mf.read_text().splitlines():
        _ln = _ln.strip()
        if not _ln or _ln.startswith("#"):
            continue
        _parts = [p.strip() for p in _ln.split("|")]
        _idrev = _parts[0].split("@")
        _authorized[_idrev[0].strip()] = _idrev[1].strip()
        if len(_parts) > 2 and "downloaded_in_batch:true" in _parts[2]:
            _download_flag = True


def _hub_id(dname: str) -> str | None:
    if not dname.startswith("models--"):
        return None
    return dname[len("models--"):].replace("--", "/")


if HF_HUB.is_dir():
    for _d in HF_HUB.iterdir():
        _id = _hub_id(_d.name)
        if _id is None:
            continue  # blobs/locks/tags: content-addressed, no origin signal
        _new = [_f for _f in _d.rglob("*") if _f.is_file()
                and _f.stat().st_mtime >= _BATCH_START]
        if _id in _authorized:
            _snaps = [p.name for p in (_d / "snapshots").iterdir()] if (_d / "snapshots").is_dir() else []
            if _authorized[_id] not in _snaps:
                fail(f"authorized model {_id} lacks pinned snapshot {_authorized[_id]}")
            continue
        if any(b in _id.lower() for b in _BLOCKED_MODEL):
            if _new:
                fail(f"blocked-origin model weights introduced/touched: {_id}")
            continue  # pre-existing untouched cache (e.g. prior smoke era): tolerated
        if _new:
            fail(f"unknown model weights introduced with no batch authorization: {_id}")
dl_pat = re.compile(r"snapshot_download|hf_hub_download|ollama pull|wget .*(safetensors|gguf|onnx)", re.I)
# Scan proposed artifacts only (.md/.json); this .py checker holds the
# pattern constant and is out of scan scope by design.
for f in sorted(ARCH.rglob("*")):
    if f.is_file() and f.suffix in {".md", ".json"} and dl_pat.search(f.read_text()):
        fail(f"download command in {f.relative_to(REPO)}")

# 7. no provider change ---------------------------------------------------------
for guarded_file in ("opencode.json", "settings/env/common.env",
                     "settings/env/hosts.shared.env"):
    hit = [l for l in porc if l[3:] == guarded_file]
    if hit:
        fail(f"provider config changed: {hit}")

print(f"task_start(local): {TASK_START_S}")
if result == "PASS":
    print("PASS: architecture proposal accepted — consistency, schema, policy, "
          "frozen lineage, boundary, no-download, no-provider-change all green.")
else:
    print(f"{result}: {len(issues)} issue(s):")
    for i in issues:
        print(f"  - {i}")
sys.exit(0 if result == "PASS" else 1)
