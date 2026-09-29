"""法規後設資料：法名／條號／後設卡片的解析與格式化，以及內建規則題庫。

從 rag.py 切出來的理由：這一層**完全不碰 I/O**（沒有 gateway、沒有 Qdrant），
是純粹的「把字串變成法規資訊」。它獨立之後好處是可以在沒有任何服務的情況下
測試與改動 —— 條號怎麼抽、法名怎麼認、簡稱怎麼對應全都在這裡。

依賴方向：law_meta 不 import 任何其他模組。`_LAW_NAMES`／`_LAW_COUNTS`／
`_LAW_SUBS` 這三個 corpus 統計住在這裡（是「法規有哪些」的知识），由
`retrieve._ensure_law_names()` 從 Qdrant scroll 進來填。
"""
import json
import logging
import re
from pathlib import Path

from . import law_struct as _law
from .common.text import collapse_ws

logger = logging.getLogger("ragdemo")

# corpus 法名清單（啟動時快取）：供「裸法名查詢」走法名分支（例:「證券交易法」→ 列出該法來源）。
# _LAW_COUNTS＝各法「現行有效條文單元數」（主條＋增訂子條，不含刪除空號、不含拆段）。
# _LAW_SUBS＝其中帶 '-' 的增訂子條數。_LAW_ALIASES＝常見簡稱 → 全名。
# _LAW_META＝各法靜態後設資料（位階/分類/日期/沿革），規則題庫用。
_LAW_NAMES: list[str] = []
_LAW_COUNTS: dict[str, int] = {}
_LAW_SUBS: dict[str, int] = {}
_LAW_META: dict[str, dict] = {}
_LAW_ALIASES = {
    "證交法": "證券交易法",
    "證交稅": "證券交易稅條例",
    "勞基法": "勞動基準法",
    "消保法": "消費者保護法",
    "個資法": "個人資料保護法",
    "民訴": "民事訴訟法",
    "刑訴": "刑事訴訟法",
    "行訴": "行政訴訟法",
    "道交條例": "道路交通管理處罰條例",
    "遺贈稅法": "遺產及贈與稅法",
    "強執法": "強制執行法",
    "公司法": "公司法",
}
_COUNT_RE = re.compile(r"(多少條|幾條|條文數|幾個條文|有多少條|共有?)")
def _is_count_question(q: str) -> bool:
    """「證交法有多少條？」這類條數問句 → 直接回答條文數，不必走 LLM。"""
    return bool(_COUNT_RE.search(q)) and "條文內容" not in q
def _law_count_line(law: str) -> str | None:
    """現行有效條文數的一句話（例：「《證券交易法》現行有效條文共 209 條…」）。"""
    n = _LAW_COUNTS.get(law)
    if not n:
        return None
    sub = _LAW_SUBS.get(law, 0)
    s = f"《{law}》現行有效條文共 {n} 條。"
    if sub:
        s += f"（主條 {n - sub} 則＋增訂子條 {sub} 則；主條編號依原序，號碼間含已刪除空號，因此最大值未滿 {n}）"
    return s
def _try_load_law_meta() -> None:
    """載入 laws_meta.jsonl（容器 /app/data/laws 或 repo data/laws）；失敗留空＝題庫退守 LLM。"""
    global _LAW_META
    if _LAW_META:
        return
    cands = [Path("data/laws/laws_meta.jsonl"), Path("/app/data/laws/laws_meta.jsonl")]
    for p in cands:
        try:
            if not p.exists():
                continue
            with p.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    m = json.loads(line)
                    n = m.get("law_name")
                    if n:
                        _LAW_META[n] = m
            logger.info("題庫後設資料載入 %d 部法（%s）", len(_LAW_META), p)
            return
        except Exception:
            _LAW_META = {}
    logger.warning("laws_meta 未找到，規則題庫拿不到後設資料")
def _fmt_rm_date(s: str | None) -> str | None:
    """YYYYMMDD → 「民國 Y年 M月 D 日（西元 YYYY）」。非該格式原樣回。"""
    if not s:
        return None
    m = re.match(r"^(\d{4})(\d{2})?(\d{2})?$", s.strip())
    if not m:
        return s
    y, mo, d = m.group(1), m.group(2), m.group(3)
    rm = int(y) - 1911
    seg = f"民國 {rm} 年"
    if mo:
        seg += f" {int(mo)} 月"
    if d:
        seg += f" {int(d)} 日"
    return f"{seg}（西元 {y}）"
def _meta_authority(m: dict) -> str | None:
    """主管機關：法規分類開頭為「行政＞…」取第二段，否則回整個分類。"""
    cat = collapse_ws(m.get("law_category") or "")
    if cat.startswith("行政") and "＞" in cat:
        return cat.split("＞")[1] or (cat or None)
    return cat or None
def _meta_history(m: dict) -> str | None:
    h = (m.get("law_histories") or "").strip()
    if not h:
        return None
    return collapse_ws(h.split("\r\n")[0].split("\n")[0]) or None
def _meta_card(law: str, m: dict) -> str:
    """「什麼是X法」的規則卡：位階＋分類＋條數＋沿革首行（不進 LLM，零編造）。"""
    lv = m.get("law_level") or "法規"
    cat = collapse_ws(m.get("law_category") or "")
    n = _LAW_COUNTS.get(law)
    cnt = f"現行有效條文 {n} 條" if n else "條文數不明"
    hist = _meta_history(m)
    s = f"{lv}《{law}》：{cat}；{cnt}。"
    if hist:
        s += f" 沿革：{hist}。"
    return s
_RULE_INTENTS = [
    ("count", re.compile(r"(多少條|幾條|條文數|幾個條文|有多少條|共有?)")),
    ("authority", re.compile(r"(主管機關|主責機關|管轄機關|哪個機關|哪個單位|何機關)")),
    ("effective", re.compile(r"(何時施行|施行日期|生效日期|何時生效|何時實施|哪時施行|何時公布|公布日期)")),
    ("revised", re.compile(r"(何時修正|什麼時候修正|修正日期|最近修正|修改日期)")),
    ("rev_count", re.compile(r"(幾次修正|修正幾次|修正次數|共修正|改過幾次|修過幾次|修改幾次|修正過幾次)")),
    ("level", re.compile(r"(法律還是|還是法律|法規命令|位階|中央法規|地方自治還是|屬於.{0,8}法規?)")),
    ("active", re.compile(r"(是否廢止|已廢止|還有在用|還有效|仍然有效|是否有效)")),
    ("brief", re.compile(r"(什麼是|是什麼|介紹一下|簡介)")),
]
def _route_law_intent(question: str) -> str | None:
    """規則題庫路由：法名校準後（由呼叫端保證），此處判斷問句該由哪條規則直接答。"""
    q = "".join(question.split())
    for intent, pat in _RULE_INTENTS:
        if pat.search(q):
            return intent
    return None
def _rule_answer(intent: str, law: str) -> str | None:
    """給定法名與題庫 intent，回規則答案；無資料回 None（呼叫端退守 LLM）。"""
    if intent == "count":
        return _law_count_line(law)
    m = _LAW_META.get(law)
    if not m:
        return None
    if intent == "authority":
        a = _meta_authority(m)
        return f"《{law}》的主管機關是 {a}。" if a else None
    if intent == "effective":
        sd = _fmt_rm_date((m.get("law_effective_date") or "").strip() or None)
        note = (m.get("law_effective_note") or "").strip()
        parts = []
        if sd:
            parts.append(f"自 {sd} 起施行")
        if note:
            parts.append(f"（{collapse_ws(note[:80])}）")
        return f"《{law}》{''.join(parts)}。" if parts else None
    if intent == "revised":
        md = _fmt_rm_date((m.get("law_modified_date") or "").strip() or None)
        return f"《{law}》最近一次修正公布：{md}。" if md else None
    if intent == "rev_count":
        c = len(re.findall(r"(?:^|\r?\n)\s*\d+\.", m.get("law_histories") or ""))
        md = _fmt_rm_date((m.get("law_modified_date") or "").strip() or None)
        if not c:
            return None
        return f"《{law}》歷來共修正 {c} 次" + (f"（最近：{md}）" if md else "") + "。"
    if intent == "level":
        lv = m.get("law_level")
        return f"《{law}》位階屬「{lv}」。" if lv else None
    if intent == "active":
        if not m.get("is_abandoned"):
            return f"《{law}》現行有效（未廢止）。"
        return f"《{law}》已廢止：{m.get('law_abandon_note') or '（無說明）'}。"
    if intent == "brief":
        return _meta_card(law, m)
    return None
_RULE_INTENT_LABELS = {
    "count": "條數問句（主條＋子條、現行有效；不含刪除空號）",
    "authority": "主管機關（法規分類第二段）",
    "effective": "施行／生效日期",
    "revised": "最近修正公布日期",
    "rev_count": "歷來修正次數（沿革編號計數）",
    "level": "位階（法律／法規命令…）",
    "active": "現行有效或已廢止",
    "brief": "什麼是X法 → 位階＋分類＋條數＋沿革卡",
}
def builtin_catalog(sample_law: str = "證券交易法") -> list[dict]:
    """題庫頁顯示用：內建 intent → 觸發關鍵字／範例／規則作答範本。"""
    _try_load_law_meta()
    out = []
    for intent, pat in _RULE_INTENTS:
        line = _rule_answer(intent, sample_law) or ""
        out.append({
            "id": intent,
            "category": "內建",
            "label": _RULE_INTENT_LABELS.get(intent, intent),
            "pattern": getattr(pat, "pattern", ""),
            "sample_answer": line,
        })
    return out
def _alias_to_law(qq: str) -> str | None:
    """簡稱 → 全名：簡稱精準等於查詢 → 或簡稱嵌在查詢內（長度>=3）。"""
    for alias, name in _LAW_ALIASES.items():
        if qq == alias:
            return name
    for alias, name in _LAW_ALIASES.items():
        if len(alias) >= 3 and alias in qq:
            # 結尾為「第N條」= 具體條號查詢，推給條號分支，不當法名簡稱；
            # 結尾只是「幾條/多少條」仍算法名問句（如「勞基法共有幾條」）。
            if qq.endswith("條") and _ART_REF_RE.search(qq):
                continue
            return name
    return None
def _detect_law(question: str) -> str | None:
    """查詢是否對應 corpus 某部法名：簡稱 → 法名精準等於 → 法名以此開頭 → 法名包含 → 法名嵌於問句。"""
    qq = "".join(question.split())
    if not qq:
        return None
    law = _alias_to_law(qq)
    if law:
        return law
    for name in _LAW_NAMES:
        if qq == name.replace(" ", ""):
            return name
    for name in _LAW_NAMES:
        n = name.replace(" ", "")
        if n.startswith(qq) and len(qq) >= 3:
            return name
    for name in _LAW_NAMES:
        n = name.replace(" ", "")
        if qq in n and len(qq) >= 4 and not qq.endswith("條"):
            return name
    for name in _LAW_NAMES:  # 法名嵌在問句內（例:「什麼是證券交易法」）
        n = name.replace(" ", "")
        # >= 2 而非 >= 3：門檻原本是 3，但 corpus 1025 部法裡「民法」只有 2 字，
        # 于是「民法第184條…」永遠偵測不到法名 → 走不到條號精準分支 → 只剩 dense
        # 0.55 < 門檻 0.58 → no_match（2026-09-26 實測）。全 corpus 只有「民法」
        # 一個 2 字法名，放寬只影響它。
        if len(n) >= 2 and n in qq:
            return name
    return None
def _law_brief(law: str) -> str:
    """法規基本敘述（不含條號）：《法名》（共N條）。"""
    n = _LAW_COUNTS.get(law)
    return f"《{law}》（共{n}條）" if n else f"《{law}》"
_ART_REF_RE = re.compile(r"第\s*[0-9]+(?:\s*-\s*[0-9]+)?\s*條")
def _strip_article_refs(text: str) -> str:
    """去掉含「第X條」的句子（法名查詢的基本敘述不該指名任何條號）。"""
    parts = [s for s in text.split("。") if s and not _ART_REF_RE.search(s)]
    return "。".join(parts) + ("。" if parts else "")
_ART_HEAD_RE = re.compile(r"^第\s*(\d+)")
def _art_flno(article_no: str) -> str | None:
    """條號 → moj 單條文 flno 參數（例:「第 10-1 條」→「10-1」）。"""
    m = re.match(r"^第\s*([0-9]+(?:\s*-\s*[0-9]+)?)", article_no or "")
    return m.group(1).replace(" ", "") if m else None
def _law_url(pcode: str | None, article_no: str | None = None) -> str | None:
    """法規來源連結（全國法規資料庫 law.moj.gov.tw）：給條號→單條文頁；否則→整部法頁。"""
    if not pcode:
        return None
    base = "https://law.moj.gov.tw/LawClass/"
    if article_no:
        flno = _art_flno(article_no)
        if flno:
            return f"{base}LawSingle.aspx?pcode={pcode}&flno={flno}"
    return f"{base}LawAll.aspx?pcode={pcode}"
def _art_sort_key(article_no: str) -> tuple[int, int]:
    """條號排序鍵：主號（負數排最前，讓「第1條」先於其他）＋子號。"""
    m = _ART_HEAD_RE.match(article_no or "")
    head = int(m.group(1)) if m else 10 ** 9
    return (head, 0) if m else (10 ** 9, 0)
