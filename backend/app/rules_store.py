"""使用者題庫（規則覆寫題）：data/rules.json 儲存＋匹配。

memory-first：service 啟動即載入（init()），後續以 mtime 輪詢自動同步
（本地 admin 寫入、或他台 git pull 換檔都會在下次查詢時生效）。
probe() 是答案路由第一關：完全相符（identity）直接採用、近似命中交由 JEV 裁決、
無候選才進 RAG。全 fail-safe：任何 IO／格式錯誤都當「沒有規則」，絕不影響 query。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from pathlib import Path

from .common.text import squash

logger = logging.getLogger("ragdemo")

PATHS = [Path("data/rules/rules.json"), Path("/app/data/rules/rules.json")]

_lock = threading.Lock()
_cache_lock = threading.Lock()
_cache = {"path": None, "mtime": 0, "rules": []}


def _pick() -> Path:
    for p in PATHS:
        try:
            if p.exists():
                return p
        except OSError:
            continue
    return PATHS[0]


def _load_raw(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = data.get("rules", []) if isinstance(data, dict) else data
        return rules if isinstance(rules, list) else []
    except (OSError, ValueError):
        return []


def _stat(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _read_cached(path: Path) -> list[dict]:
    """mtime 相同回快取；不同（含首讀／他台換檔）重新讀檔。檔案不存在則回空。"""
    st = _stat(path)
    with _cache_lock:
        if _cache["path"] == path and _cache["mtime"] == st:
            return _cache["rules"]
    rules = _load_raw(path)
    with _cache_lock:
        _cache["path"] = path
        _cache["mtime"] = st
        _cache["rules"] = rules
    return rules


def load() -> list[dict]:
    """讀全部使用者規則（記憶體快取＋mtime 同步）。錯誤一律回空清單。"""
    try:
        with _lock:
            return _read_cached(_pick())
    except Exception as e:
        logger.warning("題庫讀取失敗（%s）", e)
        return []


def init() -> None:
    """service 啟動時把題庫預載進記憶體（後續 mtime 自動同步）。空／錯誤不擋啟動。"""
    try:
        rs = load()
        logger.info("題庫已載入 %d 條（來源 %s）", len(rs), _pick())
    except Exception as e:
        logger.warning("題庫啟動載入失敗（%s）", e)


def _save_atomic(path: Path, rules: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 2, "rules": rules}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)
    return True


def save(rules: list[dict]) -> bool:
    try:
        with _lock:
            _save_atomic(_pick(), rules)
        return True
    except OSError as e:
        logger.warning("題庫寫入失敗（%s）", e)
        return False


def validate_rule(d: dict) -> str | None:
    """回錯誤訊息；None＝可接受。"""
    m = (d.get("match") or "").strip()
    if not m:
        return "match 不能空白"
    kind = d.get("kind", "contains")
    if kind not in ("contains", "regex", "exact"):
        return "kind 僅能 contains/regex/exact"
    if kind == "regex":
        try:
            re.compile(m)
        except re.error as e:
            return f"regex 無法編譯：{e}"
    if not (d.get("answer") or "").strip():
        return "answer 不能空白"
    if len(d.get("answer", "")) > 4000:
        return "answer 過長（上限 4000 字）"
    law = (d.get("law") or "").strip()
    if len(law) > 100:
        return "law 過長（上限 100 字）"
    return None


def add(d: dict) -> dict | None:
    rule = {
        "id": uuid.uuid4().hex[:12],
        "kind": d.get("kind", "contains"),
        "match": (d.get("match") or "").strip(),
        "law": (d.get("law") or "").strip() or None,
        "answer": (d.get("answer") or "").strip(),
        "note": (d.get("note") or "").strip() or "",
        "enabled": bool(d.get("enabled", True)),
    }
    err = validate_rule(rule)
    if err:
        return {"error": err}
    with _lock:
        rules = _load_raw(_pick())
        rules.append(rule)
        if not _save_atomic(_pick(), rules):
            return {"error": "題庫檔案寫入失敗"}
    return {"rule": rule}


def toggle(rid: str) -> dict | None:
    with _lock:
        rules = _load_raw(_pick())
        found = next((r for r in rules if r.get("id") == rid), None)
        if not found:
            return {"error": "找不到該規則"}
        found["enabled"] = not found.get("enabled", True)
        if not _save_atomic(_pick(), rules):
            return {"error": "題庫檔案寫入失敗"}
    return {"rule": found}


def delete(rid: str) -> dict | None:
    with _lock:
        rules = _load_raw(_pick())
        n = len(rules)
        rules = [r for r in rules if r.get("id") != rid]
        if len(rules) == n:
            return {"error": "找不到該規則"}
        if not _save_atomic(_pick(), rules):
            return {"error": "題庫檔案寫入失敗"}
    return {"deleted": rid}


def match_rule(question: str, law: str | None, rules: list[dict] | None = None) -> dict | None:
    """回第一個命中的已啟用規則；無則 None。law 優先於辦法名限定的規則。"""
    if rules is None:
        rules = load()
    q = squash(question)
    for r in rules:
        if not r.get("enabled", True):
            continue
        if r.get("law") and law != r.get("law"):
            continue
        kind = r.get("kind", "contains")
        m = r.get("match", "")
        if kind == "exact":
            hit = q == squash(m)
        elif kind == "regex":
            try:
                hit = re.search(m, q) is not None
            except re.error:
                continue
        else:
            hit = m in q
        if hit:
            return r
    return None


_PROBE_FLOOR = 0.5  # 近似候選最低 token 覆蓋率（以下不送 JEV）


def _shingles(s: str) -> set[str]:
    """淺層 token：拉丁文切字＋CJK 雙字（不依賴外部分詞、無依賴）。"""
    s = squash(s or "")
    out: set[str] = set()
    for run in re.findall(r"[A-Za-z0-9]+|[\u3400-\u9fff]+", s):
        if run.isascii():
            out.add(run.lower())
        else:
            out.add(run)
            if len(run) >= 2:
                out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _overlap(q: str, m: str) -> float:
    """q 對 m 的 shingle 覆蓋率（0~1）：問題涵蓋 match 越多，語意越可能相近。"""
    a, b = _shingles(q), _shingles(m)
    if not b:
        return 0.0
    return len(a & b) / len(b)


def probe(question: str, law: str | None) -> dict | None:
    """題庫第一關候選：
    - identity=True：問題與 match 完全相符（exact 命中，或白話字串歸一相等）→ 直接採用、跳過 JEV。
    - identity=False：近似命中（contains/regex 命中但不相等，或 contains 的 token 覆蓋>=_PROBE_FLOOR）
      → 交由 JEV 裁決是否採用。
    法名限定規則須法名相符；停用規則跳過；無候選回 None。回最佳一筆。"""
    best: dict | None = None
    best_score = 0.0
    q = squash(question)
    for r in load():
        if not r.get("enabled", True):
            continue
        if r.get("law") and law != r.get("law"):
            continue
        kind = r.get("kind", "contains")
        m = r.get("match", "")
        mo = squash(m)
        score = 0.0
        ident = False
        if kind == "exact":
            if q == mo:  # exact 不當模糊候選：非相等就是故意不命中
                score, ident = 2.0, True
        elif kind == "regex":
            try:
                hit = re.search(m, q) is not None
            except re.error:
                hit = False
            if hit:
                ident = q == mo
                score = 2.0 if ident else 1.6
        else:  # contains
            if m in q:
                ident = q == mo
                score = 2.0 if ident else 1.5
            else:
                ov = _overlap(q, m)
                if ov >= _PROBE_FLOOR:
                    score = ov
        if score > best_score:
            best = {"rule": r, "identity": ident}
            best_score = score
    return best


def counts() -> dict:
    rules = load()
    return {"total": len(rules), "enabled": sum(1 for r in rules if r.get("enabled", True))}