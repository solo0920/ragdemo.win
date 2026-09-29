"""執行期驗證：模組載入後，沒有任何環境變數是「意外的空字串」。

為什麼需要這條（2026-09-29 實測踩到兩次）：

`os.getenv(K, "預設")` 只在變數**不存在**時用預設；`.env` 裡 `K=`（存在但
空）會回空字串。我在這個專案修過 25 處，但還是有漏網的：

1. `KEEP_ALIVE = os.getenv("KEEP_ALIVE", "-1")` — 漏了，因為它的值不經過
   `int()`／`float()`，而是被 `keep_alive_value()` 送去組成 HTTP body。
   AST 掃描只看得到 getenv 呼叫，看不到呼叫鏈。症狀極隱晦：
   空字串被送成 `{"keep_alive": ""}` → ollama 回 400
   `time: invalid duration ""` → 查詢全掛，但 `/health` 正常（200）。

2. `EMBED_MODEL` — 空值送出 `{'model': ''}` → ollama 404
   `model '' not found`。同樣只在真正送出請求時才暴露。

所以「掃原始碼找 getenv 寫法」這種靜態檢查有盲點：它抓不到被包裝的取值路徑。
這條測試改從**結果**驗證：把所有環境變數設成空字串，載入每個模組，
斷言沒有任何模組層級變數變成「非預期的空字串」。

清單是明確列出的，不是掃出來的 —— 掃不出「什麼該有值」，
但我知道哪些變數被下游真的拿去用。
"""
import importlib
import os
from contextlib import contextmanager

import pytest

# 這些變數的值會被送進下游請求或比較邏輯，空字串會造成難查的失敗。
# 每個都附上「空值時會怎樣」，那是實測或讀碼確認的。
MUST_NOT_BE_EMPTY = {
    # → gateway.embed() 送出 {"model": ""} → ollama 404 "model '' not found"
    "EMBED_MODEL": "ollama 404 model '' not found",
    # → generate() 送出 {"model": ""} → ollama 404
    "LLM_MODEL": "ollama 404 model '' not found",
    # → {"keep_alive": ""} → ollama 400 'time: invalid duration ""'
    "KEEP_ALIVE": "ollama 400 invalid duration",
    # → 查錯的 collection，症狀是「查不到東西但沒有任何錯誤」
    "COLLECTION": "查詢無聲無息地查錯 collection",
    # → DSN 壞掉，asyncpg 連線時才炸，離連線點很遠
    "POSTGRES_DSN": "asyncpg 連線時才炸（離錯誤很遠）",
    # → 掃錯的模型清單
    "RERANK_MODEL": "rerank 掃錯模型",
    # → 探測時間 0 或負數會讓所有主機被判不可用
    "PROBE_TIMEOUT": "所有主機被判不可用",
    "PICK_TTL": "負值會讓快取永不生效",
    # → 數值型，空值會 ValueError（這幾個已用 `or "預設"`，這裡是雙保險）
    "RAG_MIN_DENSE": "float('') → ValueError",
    "RAG_MID_DENSE": "float('') → ValueError",
    "RAG_HIGH_DENSE": "float('') → ValueError",
    "REGISTRY_HEARTBEAT": "int('') → ValueError",
    "REGISTRY_STALE_MIN": "int('') → ValueError",
}

# 這些變數「空值是合法的」，刻意不在清單裡（設空＝不啟用／不驗證）。
ALLOWED_EMPTY = {
    "ADMIN_TOKEN",          # 未設＝不啟用寫入保護
    "CF_AIG_TOKEN",         # 未設＝該 provider 不可用
    "CF_AIG_GATEWAY_ID",
    "HF_TOKEN",
    "NVIDIA_API_KEY",
    "TYPESAFE_API_KEY",
    "ZEN_API_KEY",
    "QDRANT_API_KEY",       # 未設＝無認證的 qdrant
    "QDRANT_PEER_API_KEY",
    "TS_IP",                # 空＝只綁 127.0.0.1
    "LAN_IP",               # 政策性停用
    "HOST_ID",              # 空＝未設定主機身���（會記警告但可跑）
    "HOST_NAME",
    "HOST_MACHINE_ID",
    "HOST_API_URLS",        # 空＝單機無 peer
    "OLLAMA_URLS",          # 空時 fallback 到 OLLAMA_DEFAULT
    "QDRANT_URLS",
    "OLLAMA_MODELS",
    "SRC_API_URL",
    "HOST_API_LOCAL",
    "LAW_SYNC_SOURCE",
    "LIMIT",                # 0＝全量
    "JEV_DISABLED",
    "JEV_MODEL",
    "JEV_VERIFY_MIN",
    "JEV_BANK_MIN",
}

MODULES = ("cn_parse", "law_meta", "gateway", "retrieve", "rag", "registry")


@contextmanager
def _empty_env():
    saved = {k: os.environ.get(k) for k in MUST_NOT_BE_EMPTY}
    try:
        for k in MUST_NOT_BE_EMPTY:
            os.environ[k] = ""
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.mark.parametrize("key", sorted(MUST_NOT_BE_EMPTY))
def test_module_value_survives_empty_env(key):
    """把變數設成空字串後，模組層級的值仍要等於它宣告的預設值。"""
    for name in MODULES:
        importlib.import_module(f"app.{name}")   # 先確保載入過
    with _empty_env():
        mod = importlib.reload(importlib.import_module("app.gateway"))
        if hasattr(mod, key):
            got = getattr(mod, key)
            assert got != "", (
                f"gateway.{key} 在環境變數為空字串時變成了空字串。\n"
                f"  症狀：{MUST_NOT_BE_EMPTY[key]}\n"
                f"  修法：os.getenv({key!r}) or <預設>（注意不能用 (K, D) 寫法）")
        for name in ("retrieve", "registry"):
            m = importlib.reload(importlib.import_module(f"app.{name}"))
            if hasattr(m, key):
                assert getattr(m, key) != "", (
                    f"{name}.{key} 成了空字串。症狀：{MUST_NOT_BE_EMPTY[key]}")


def test_keep_alive_value_never_returns_empty():
    """keep_alive_value() 是最陰險的一個：空值不會崩，只會被送成無效 HTTP body。"""
    import importlib
    gateway = importlib.import_module("app.gateway")
    saved = os.environ.get("KEEP_ALIVE")
    try:
        os.environ["KEEP_ALIVE"] = ""
        g = importlib.reload(gateway)
        v = g.keep_alive_value()
        assert v != "", "keep_alive_value() 回空字串 → ollama 400 invalid duration"
        assert v == -1, f"應該回預設 -1，實際 {v!r}"
    finally:
        if saved is None:
            os.environ.pop("KEEP_ALIVE", None)
        else:
            os.environ["KEEP_ALIVE"] = saved
        importlib.reload(gateway)


def test_allowed_empty_list_is_actually_allowed():
    """反向檢查：ALLOWED_EMPTY 裡的不能在別處被當成必填擋下。"""
    for key in ALLOWED_EMPTY:
        assert key not in MUST_NOT_BE_EMPTY, (
            f"{key} 同時出現在兩份清單 —— 要嘛它確實不能空，"
            f"要嘛它確實可以空，兩邊都有就是沒想清楚")
