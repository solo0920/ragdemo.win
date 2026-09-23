#!/usr/bin/env python3
"""法規條文全量灌 Qdrant live 的 `laws`：dense(bge-m3)＋sparse(TF, modifier=idf) hybrid。

- 超長條文(>7900 字)切塊：chunk_idx 標記，text=該塊、full_content=完整條文
- 先備份現行 `laws` → `laws_demo_backup`，再重建 `laws`（dense+sparse，RRF 就緒）
- point id = {pcode}-{article_seq}[-c{chunk}]，冪等 upsert（重跑只覆蓋）
- 預設排除 is_repealed / is_abandoned（不灌進去）
用法：LIMIT=200 先試灌，ok 再全量。
"""
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sparse import sparse_vector  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "laws"
OLLAMA = os.getenv("OLLAMA", "http://100.119.83.111:11434").rstrip("/")
QDRANT = os.getenv("QDRANT", "http://localhost:6333").rstrip("/")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3:latest")
COLLECTION = "laws"
DENSE = 1024
MAX_DOC = 7900
EMB_BATCH, UP_BATCH = 256, 512

COLLECTION_CFG = {
    "vectors": {"dense": {"size": DENSE, "distance": "Cosine"}},
    "sparse_vectors": {"sparse": {"modifier": "idf"}},
    "on_disk_payload": True,
    "hnsw_config": {"m": 32, "ef_construct": 256},
}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def chunk_text(content: str, maxlen: int = MAX_DOC) -> list[str]:
    if len(content) <= maxlen:
        return [content]
    par = [ln for ln in content.split("\n") if ln.strip()]
    chunks, cur = [], ""
    for ln in par:
        if cur and len(cur) + len(ln) + 1 > maxlen:
            chunks.append(cur)
            cur = ""
        cur = (cur + "\n" + ln).strip("\n")
    if cur:
        chunks.append(cur)
    out = []
    for c in chunks:
        while len(c) > maxlen:
            cut = c.rfind("\n", 0, maxlen)
            if cut < maxlen // 2:
                cut = maxlen
            out.append(c[:cut])
            c = c[cut:]
        out.append(c)
    return out


async def _embed_one(c: httpx.AsyncClient, text: str) -> list[float]:
    """單筆 embed：超長/膨脹文本逐步縮短，直到 bge-m3 放得下；最終必定試到 ≤80 字。"""
    t = text
    while True:
        try:
            r = await c.post(f"{OLLAMA}/api/embed",
                             json={"model": EMBED_MODEL, "input": [t], "keep_alive": -1}, timeout=600)
            if r.status_code == 200:
                return r.json()["embeddings"][0]
            if r.status_code != 400:
                r.raise_for_status()
        except httpx.TransportError:
            await asyncio.sleep(2)
            continue
        if len(t) <= 80:
            break
        t = t[: max(80, len(t) * 3 // 4)]
    raise RuntimeError(f"embed 始終失敗：{text[:40]!r}")


async def embed(c: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), EMB_BATCH):
        batch = [t[:MAX_DOC] for t in texts[i:i + EMB_BATCH]]
        r = None
        for _ in range(3):  # ollama 忙/瞬斷重試
            try:
                r = await c.post(f"{OLLAMA}/api/embed",
                                 json={"model": EMBED_MODEL, "input": batch, "keep_alive": -1}, timeout=600)
                if r.status_code == 200:
                    break
            except httpx.TransportError:
                await asyncio.sleep(2)
        if r is not None and r.status_code == 200:
            out.extend(r.json()["embeddings"])
            continue
        # 整批 400（某筆超長/膨脹）→ 拆單筆連演
        out.extend([await _embed_one(c, t) for t in batch])
    return out


async def backup_old(c: httpx.AsyncClient) -> None:
    r = await c.get(f"{QDRANT}/collections/{COLLECTION}", timeout=60)
    if r.status_code != 200:
        return
    old = r.json()["result"]
    if old["config"]["params"].get("sparse_vectors"):
        print("laws 已是 hybrid，不需備份")
        return
    cfg = old["config"]["params"]
    vectors = cfg.get("vectors")
    if isinstance(vectors, dict) and "dense" in vectors:  # 已是命名 dense
        vcfg = {"dense": vectors["dense"]}
    else:
        vcfg = vectors
    name = "laws_demo_backup"
    r = await c.get(f"{QDRANT}/collections/{name}", timeout=60)
    if r.status_code == 200:
        print(f"備份已存在 {name}，跳過")
        return
    await c.put(f"{QDRANT}/collections/{name}",
                json={"vectors": vcfg, "on_disk_payload": True})
    offset = None
    pts = []
    while True:
        body = {"limit": 100, "with_vector": True, "with_payload": True}
        if offset:
            body["offset"] = offset
        r = await c.post(f"{QDRANT}/collections/{COLLECTION}/points/scroll", json=body, timeout=120)
        r.raise_for_status()
        res = r.json()["result"]
        pts.extend(res["points"])
        offset = res.get("next_page_offset")
        if not offset:
            break
    if pts:
        await c.put(f"{QDRANT}/collections/{name}/points",
                    json={"points": [{"id": p["id"], "vector": p["vector"], "payload": p.get("payload", {})}
                                     for p in pts]}, timeout=120)
    print(f"備份 {name}: {len(pts)} 點")


async def rebuild_collection(c: httpx.AsyncClient) -> None:
    r = await c.delete(f"{QDRANT}/collections/{COLLECTION}", timeout=120)
    if r.status_code not in (200, 404):
        r.raise_for_status()
    r = await c.put(f"{QDRANT}/collections/{COLLECTION}", json=COLLECTION_CFG, timeout=120)
    r.raise_for_status()
    print(f"重建 {COLLECTION}：dense(1024,cosine)＋sparse(idf modifier)")


def point_id(pcode: str, seq: int, ci: int) -> int:
    """法條穩定點 ID：md5("{pcode}-{seq}[-c{ci}]") 前 8 bytes → unsigned int（Qdrant 只收 u64/uuid）。"""
    return int.from_bytes(hashlib.md5(f"{pcode}-{seq}-c{ci}".encode()).digest()[:8], "big") if ci else \
        int.from_bytes(hashlib.md5(f"{pcode}-{seq}".encode()).digest()[:8], "big")


def build_points(flat: list[dict], limit: int) -> list[dict]:
    pts: list[dict] = []
    for a in flat:
        if a["is_repealed"] or a["is_abandoned"]:
            continue
        seq = a["article_seq"]
        chunks = chunk_text(a["article_content"])
        head = f"{a['article_no']} {a['chapter']} "
        for ci, text in enumerate(chunks):
            pid = point_id(a["pcode"], seq, ci)
            payload = {
                "pcode": a["pcode"],
                "law_name": a["law_name"],
                "law_category": a["law_category"],
                "article_seq": seq,
                "article_no": a["article_no"],
                "chapter": a["chapter"],
                "is_repealed": False,
                "is_abandoned": False,
                "char_len": len(a["article_content"]),
                "source": "moj",
                "text": text,
                "content_hash": hashlib.sha256(a["article_content"].encode("utf-8")).hexdigest(),
            }
            if len(chunks) > 1:
                payload["chunk_idx"] = ci
                payload["full_content"] = a["article_content"]
            pts.append({"id": pid, "payload": payload, "_text": f"{head}{text}"})
            if limit and len(pts) >= limit:
                return pts
    return pts


async def run(limit: int = 0) -> None:
    flat = load_jsonl(DATA / "laws_flat.jsonl")
    pts = build_points(flat, limit) if limit else build_points(flat, len(flat))
    print(f"待灌 {len(pts)} 條（排除已廢止/刪除）")

    async with httpx.AsyncClient() as c:
        await backup_old(c)
        await rebuild_collection(c)

        sp = [None] * len(pts)
        texts = [p["_text"] for p in pts]
        print("dense embed 開始（ollama bge-m3）…（185 批次，約 2-8 分鐘）")
        t0 = __import__("time").time()
        dense = await embed(c, texts)
        print(f"dense 完成 {len(dense)}，耗時 {int(__import__('time').time() - t0)}s，sparse 計算中…")
        for i, p in enumerate(pts):
            sp[i] = sparse_vector(p["_text"])

        for i in range(0, len(pts), UP_BATCH):
            batch = [
                {"id": pts[j]["id"], "vector": {"dense": dense[j], "sparse": sp[j]}, "payload": pts[j]["payload"]}
                for j in range(i, min(i + UP_BATCH, len(pts)))
            ]
            r = await c.put(f"{QDRANT}/collections/{COLLECTION}/points",
                            json={"points": batch}, timeout=600)
            r.raise_for_status()
            if i % (UP_BATCH * 2) == 0:
                print(f"已 upsert {min(i + UP_BATCH, len(pts))}/{len(pts)}")
        print("upsert 完成")

        r = await c.get(f"{QDRANT}/collections/{COLLECTION}", timeout=60)
        print("points_count =", r.json()["result"]["points_count"])


if __name__ == "__main__":
    lim = int(os.getenv("LIMIT", "0"))
    asyncio.run(run(lim))