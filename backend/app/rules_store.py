"""使用者題庫（規則覆寫題）：data/rules.json 儲存＋匹配。

與內建 intent 題庫關係：使用者規則優先全文匹配（高度明確、手動校正），
命中就直接回覆、不進 LLM；內建 intent 仍需法名偵測＋metadata，兩者互補。
全 fail-safe：任何 IO／格式錯誤都當「沒有規則」，絕不影響 query。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from pathlib import Path

logger = logging.getLogger("ragdemo")

PATHS = [Path("data/rules/rules.json"), Path("/app/data/rules/rules.json")]

_lock = threading.Lock()


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


def load() -> list[dict]:
    """讀全部使用者規則。錯誤一律回空清單。"""
    try:
        with _lock:
            return _load_raw(_pick())
    except Exception as e:
        logger.warning("題庫讀取失敗（%s）", e)
        return []


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
    q = _cw(question)
    for r in rules:
        if not r.get("enabled", True):
            continue
        if r.get("law") and law != r.get("law"):
            continue
        kind = r.get("kind", "contains")
        m = r.get("match", "")
        if kind == "exact":
            hit = q == _cw(m)
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


def _cw(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def counts() -> dict:
    rules = load()
    return {"total": len(rules), "enabled": sum(1 for r in rules if r.get("enabled", True))}