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
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
# sparse tokenizer 只有一份，在 backend/app/common/。ingest 與 backend 共用同一個
# 實作（過去這裡另有一份 byte-identical 的 sparse.py，兩邊各自演化已經分叉的風險）。
sys.path.insert(0, str(ROOT / "backend"))
from app.common.jsonl import load_jsonl  # noqa: E402  # pg_load.py 共用同一份
import _hostenv  # noqa: E402  # 主機端環境變數（載入 .env＋改寫容器主機名）
from app.common.sparse import sparse_vector  # noqa: E402

DATA = ROOT / "data" / "laws"
# ingest 跑在 host 端（不是容器內），所以預設就是本機 ollama。
# 舊預設是 x570 的 tailscale IP：一台沒設 OLLAMA 的新機器會去戳別台機器的 ollama，
# 然後把「連不上」誤認成「嵌入失敗」，除錯方向整個跑掉。
# ⚠️ 2026-10-02：**顯式**載入，不要靠行序。
# 下面 `QDRANT_API_KEY`／`EMBED_MODEL` 是用 os.getenv 讀的，而 .env 是在
# `_hostenv.host_*()` 裡才載入的 —— 第一版靠「33/34 行剛好先呼叫過」這個**行序巧合**
# 才讀得到值。誰把那些行往上挪或往下移，就會**靜默**退回空字串（→ 不帶 key → 401），
# 而且沒有任何錯誤。所以這裡明寫一次。
_hostenv.load_host_env()

# ⚠️ 2026-10-02：這三個 fallback 在主機上全是壞的（見 _hostenv.py 的說明）。
# qdrant 只綁 ${TS_IP}:6333、API key 沒帶會 401，所以「安靜地退回 localhost」
# 只會讓腳本看起來跑完了，實際上什麼都沒寫進去。
OLLAMA = _hostenv.host_ollama_url().rstrip("/")
QDRANT = _hostenv.host_qdrant_url().rstrip("/")
# qdrant 啟用 QDRANT__SERVICE__API_KEY 後，所有請求都要帶 api-key header，
# 否則 401（實測）。compose.yaml:15 有設那個 key，所以這支腳本一定要帶。
# 讀不到值時不帶 header —— 讓無認證的 qdrant（本機測試）仍能用。
QDRANT_API_KEY = (os.getenv("QDRANT_API_KEY") or "").strip()
QDRANT_HEADERS = {"api-key": QDRANT_API_KEY} if QDRANT_API_KEY else {}
# `or` 不是多餘的：.env 裡 `EMBED_MODEL=`（存在但空）會讓 os.getenv 回空字串，
# 送出 {'model': ''} → ollama 回 404 "model '' not found"（實測踩到）。
# 與 tests/test_env_empty_values.py 鎖的是同一件事。
EMBED_MODEL = (os.getenv("EMBED_MODEL") or "").strip() or "bge-m3:latest"
COLLECTION = "laws"
DENSE = 1024
MAX_DOC = 7900

# ollama 0.34.4（2026-09-29 實測）對**單次請求的總字元數**有上限，超過就回
# 400 且錯誤訊息是誤導性的：
#     Post "http://127.0.0.1:55765/tokenize": dial tcp ...: connection refused
# 那個 port 是 ollama 內部 worker 的隨機臨時埠，看起來像「連不上 ollama」，
# 實際是它自己把請求送進去時爆掉。實測臨界點（bge-m3，中文法規文本）：
#     256 筆 × 短文本（總 5,120 字元）  → 200
#     320 筆 × 短文本（總 6,400 字元）  → 400
#     256 筆 × 法規文本（總 18,537 字元）→ 200
#     512 筆 × 法規文本（總 41,584 字元）→ 400
# 所以上限大約在 **1 萬～2 萬字元**之間，與筆數無關（1,024 筆短文本也會炸）。
#
# 原先的 EMB_BATCH=256 只是碰巧安全 —— 法規文本平均 70 字元時 256 筆約 1.8 萬
# 字元，剛好沒超過。換個資料集（平均更長）就會炸，而且症狀是整條管線在最後
# 一階段崩，前面 5 萬條都算好了。改成**依字元數**切分，這才對齊真正的限制。
#
# 1.2 萬字元留一點餘裕：實測 1 萬 2000 可過，但 ollama 版本升級可能收緊。
EMB_CHARS = 12_000
EMB_BATCH, UP_BATCH = 256, 512


def chunk_by_chars(texts: list[str], limit: int = EMB_CHARS) -> list[list[str]]:
    """依**總字元數**切批次，不是依筆數。

    單筆可能接近 MAX_DOC=7900，所以要保證「累積到 limit 就切」而不是
    「每 limit 筆切一批」—— 後者會讓一批的總字元數遠超 ollama 的上限。
    超長單筆（> limit）自己成一批，因為 ollama 的限制是總量不是單筆。
    """
    out: list[list[str]] = []
    cur: list[str] = []
    total = 0
    for t in texts:
        n = len(t)
        if cur and total + n > limit:
            out.append(cur)
            cur, total = [], 0
        cur.append(t)
        total += n
    if cur:
        out.append(cur)
    return out

COLLECTION_CFG = {
    "vectors": {"dense": {"size": DENSE, "distance": "Cosine"}},
    "sparse_vectors": {"sparse": {"modifier": "idf"}},
    "on_disk_payload": True,
    "hnsw_config": {"m": 32, "ef_construct": 256},
}


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
    for batch in chunk_by_chars([t[:MAX_DOC] for t in texts]):
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
        # 整批失敗（400：總字元超 ollama 上限；或 ollama 內部 worker 掛掉）
        # → 拆單筆連演。單筆永不超過 EMB_CHARS，所以這條路一定走得通。
        # 印出原因：ollama 那個 "connection refused" 誤導性很強，不印出來
        # 會被誤判成「連不上 ollama」（實際 ollama 好好地回應了這個 400）。
        print(f"  批次失敗（{len(batch)} 筆 / {sum(len(t) for t in batch)} 字元，"
              f"HTTP {r.status_code if r is not None else 'n/a'}）→ 拆單筆重試"
              f"{'：' + (r.text[:80] if r is not None else '') if r is not None else ''}")
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

    async with httpx.AsyncClient(headers=QDRANT_HEADERS) as c:
        await backup_old(c)
        await rebuild_collection(c)

        sp = [None] * len(pts)
        texts = [p["_text"] for p in pts]
        n_batches = len(chunk_by_chars([p["_text"][:MAX_DOC] for p in pts]))
        print(f"dense embed 開始（ollama bge-m3）…（{n_batches} 批次，"
              f"每批 ≤{EMB_CHARS} 字元，約 2-8 分鐘）")
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
    # `os.getenv(K, default)` 只在變數**不存在**時用 default；.env 裡寫
    # `LIMIT=`（空值）會原樣回傳空字串 → int("") 拋 ValueError。
    # .env 有 dozens 個空鍵是合法寫法（表示「用預設」），所以讀整數型
    # 環境變數都要容忍空字串。
    lim = int(os.getenv("LIMIT") or 0)
    asyncio.run(run(lim))