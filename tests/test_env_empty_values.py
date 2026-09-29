"""數值型環境變數必須容忍「空值」。

`os.getenv(KEY, "30")` 只在變數**不存在**時回傳 `"30"`；`.env` 裡寫
`KEY=`（存在但為空）會原樣回傳空字串，於是 `int("")` / `float("")` 拋
ValueError。

這個差別在 `.env.example` 有 65 個鍵、其中 39 個刻意留空（「留空＝用預設」）
的設計下是**必然踩到**的，不是罕見邊界。實測：
- `ingest/laws/qdrant_load.py` `int(os.getenv("LIMIT", "0"))` → sync_daily 管線
  在最後一階段崩，前面 51343 條條文都已算好
- `backend/app/registry.py` `int(os.getenv("REGISTRY_HEARTBEAT", "30"))` → 任何人
  清掉那個值，容器就起不來（lifespan 裡 NameError/ValueError）

修法統一是 `os.getenv(KEY) or "default"` —— 空字串與 None 都走預設。

這條測試掃描原始碼，任何人寫回 `os.getenv(K, "數字")` 就會紅。
"""
import pathlib
import re

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend" / "app"
INGEST = pathlib.Path(__file__).resolve().parents[1] / "ingest" / "laws"

# 數值型 getenv：int(...) 或 float(...) 包住 os.getenv，且帶字串預設值。
# 「有預設值的純字串 getenv」不在此列（空字串對它們是合法值）。
NUMERIC_GETENV = re.compile(
    r"""\b(?:int|float)\(\s*os\.getenv\(\s*"""
    r"""(?P<q>["'])(?P<key>[A-Za-z_][A-Za-z_0-9]*)(?P=q)\s*,"""          # getenv("K", ...
    r"""(?P<q2>["'])(?P<default>[^"']*?)(?P=q2)\s*\)\s*\)""", re.M)


def _targets():
    for p in sorted(BACKEND.rglob("*.py")):
        yield p
    for p in sorted(INGEST.glob("*.py")):
        yield p


def test_no_numeric_getenv_with_literal_default():
    """int/float(getenv(K, "預設")) 在 .env 留空時會崩 —— 一律要用 `or "預設"`。"""
    offenders = []
    for p in _targets():
        src = p.read_text(encoding="utf-8")
        for m in NUMERIC_GETENV.finditer(src):
            offenders.append(
                f"{p.relative_to(p.parents[2])}:{src[:m.start()].count(chr(10)) + 1} "
                f'{m.group("key")} → getenv("{m.group("key")}", "{m.group("default")}")'
            )
    assert not offenders, (
        "以下數值型 getenv 在 .env 留空時會拋 ValueError，改用 "
        "`os.getenv(K) or \"預設\"`：\n  " + "\n  ".join(offenders))


# 預設值「非空」的字串型 getenv：空值會被原樣帶進程式，症狀依變數而異 ——
#   EMBED_MODEL → 送出 {'model': ''} → ollama 404 "model '' not found"（實測）
#   COLLECTION → 查詢錯的 collection，症狀是「查不到東西但沒有錯誤」
#   *_BASE_URL → 拿空字串去組 URL
# 預設值是空字串的不在此列 —— 那是刻意允許留空的（如 ADMIN_TOKEN）。
# 刻意排除的兩個：gateway.py 的 OLLAMA_DEFAULT / QDRANT_DEFAULT —— 那一行的
# docstring 說明 env-audit pass 3 是逐行字面比對，改寫法會讓「低優先來源」
# 提示安靜消失（原作者實測踩到兩次）。它們留空是安全的：空值會被
# _split_endpoints 的 fallback 邏輯吸收。
STRING_GETENV = re.compile(
    r"""\bos\.getenv\(\s*"""
    r"""(?P<q>["'])(?P<key>[A-Za-z_][A-Za-z_0-9]*)(?P=q)\s*,"""
    r"""(?P<q2>["'])(?P<default>[^"']+?)(?P=q2)\s*\)""", re.M)
EXEMPT = {("gateway.py", "OLLAMA_BASE_URL"), ("gateway.py", "QDRANT_URL")}


def test_no_string_getenv_with_nonempty_default():
    """字串型 getenv 帶非空預設時，空值會被帶進程式 —— 一律要用 `or "預設"`。"""
    offenders = []
    for p in _targets():
        src = p.read_text(encoding="utf-8")
        for m in STRING_GETENV.finditer(src):
            key, default = m.group("key"), m.group("default")
            if (p.name, key) in EXEMPT:
                continue
            line = src[:m.start()].count("\n") + 1
            line_text = src.splitlines()[line - 1]
            if "or " in line_text:      # 已用 or 修過
                continue
            offenders.append(
                f"{p.relative_to(p.parents[2])}:{line} {key} "
                f'→ getenv("{key}", "{default}")')
    assert not offenders, (
        "以下 getenv 在 .env 留空時會把空字串帶進程式（EMBED_MODEL 會讓 ollama "
        "回 404），改用 `os.getenv(K) or \"預設\"`：\n  " + "\n  ".join(offenders))


def test_embed_model_never_empty():
    """專門盯 EMBED_MODEL：它空掉時的症狀最難查（404 不是 500）。"""
    import os
    for mod_name in ("app.gateway",):
        saved = os.environ.get("EMBED_MODEL")
        try:
            os.environ["EMBED_MODEL"] = ""
            import importlib
            m = importlib.reload(importlib.import_module(mod_name))
            assert m.EMBED_MODEL, f"{mod_name}.EMBED_MODEL 成了空字串"
            assert m.EMBED_MODEL == "bge-m3:latest"
        finally:
            if saved is None:
                os.environ.pop("EMBED_MODEL", None)
            else:
                os.environ["EMBED_MODEL"] = saved


def test_shipped_env_never_breaks_numeric_parsing():
    """實際驗證：把每個數值型變數設成空字串，import 與轉換都不該拋。"""
    import importlib
    import os
    numeric = [
        "REGISTRY_HEARTBEAT", "REGISTRY_STALE_MIN", "PICK_TTL", "PROBE_TIMEOUT",
        "RAG_MIN_DENSE", "RAG_MID_DENSE", "RAG_HIGH_DENSE", "JEV_VERIFY_MIN",
    ]
    saved = {k: os.environ.get(k) for k in numeric}
    try:
        for k in numeric:
            os.environ[k] = ""  # 模擬 .env 裡 `KEY=`
        for mod in ("gateway", "retrieve", "rag", "registry", "cn_parse", "law_meta"):
            importlib.import_module(f"app.{mod}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
