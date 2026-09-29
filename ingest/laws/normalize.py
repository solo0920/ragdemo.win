#!/usr/bin/env python3
"""法規 JSON 清洗→扁平化（moj 官方 API，全部法規）。

輸入：data/laws/ChLaw.json（BOM + CRLF，1 法規/物件，LawArticles 是章節+條文扁平陣列）
輸出：
  data/laws/laws_flat.jsonl  一條一列：章節標題就地作為「章」標籤帶進條文
  data/laws/laws_meta.jsonl  一部一列：法規層級中繼（沿革/前言/類別/異動）

清洗規則：
- utf-8-sig 解 BOM；\r\n → \n；每行去首尾空白
- pcode 取自 LawURL 末段（A0000001…）
- 已廢止（LawAbandonNote 非空）保留並加旗標，供檢索端決定吃不吃
- ArticleType=C 為 章/編 標題，記入後續條文的 chapter；A 為條文
- 條文長度以字元計（char_len）
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data", "laws")
SRC = os.path.join(DATA, "ChLaw.json")

ARTICLE_SPLIT_RE = re.compile(r"(?m)^　?\s*第\s*[一二三四五六七八九十百千0-9]+[-／之0-9]*\s*條\s*$")


def _clean(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _is_repealed(content: str) -> bool:
    """「（刪除）」等於條文已廢除，檢索端預設排除。"""
    c = content.replace(" ", "")
    return bool(c) and ("（刪除）" in c or "(刪除)" in c or c == "刪除")


def law_articles(law: dict) -> list[dict]:
    """章節標題與條文扁平化：回傳 (seq, type, no, content, chapter)。"""
    out: list[dict] = []
    chapter = ""
    for i, a in enumerate(law.get("LawArticles") or [], start=1):
        typ = a.get("ArticleType", "")
        content = _clean(a.get("ArticleContent", ""))
        if typ == "C" and content:
            chapter = content.strip("　 ")
            continue
        out.append({
            "pcode": law.get("_pcode") or pcode_of(law),
            "law_name": law.get("LawName", ""),
            "law_category": law.get("LawCategory", ""),
            "is_abandoned": bool(law.get("LawAbandonNote")),
            "is_repealed": _is_repealed(content),
            "article_seq": i,
            "article_type": typ,
            "article_no": (a.get("ArticleNo") or "").strip(),
            "chapter": chapter,
            "article_content": content,
            "char_len": len(content),
        })
    return out


def pcode_of(law: dict) -> str:
    """法規 pcode：LawURL 末段 query 值（例 A0000001）。唯一鍵。"""
    return (law.get("LawURL", "").rstrip("/").rsplit("=", 1)[-1])


def main() -> None:
    with open(SRC, encoding="utf-8-sig") as f:
        data = json.load(f)

    flat_path = os.path.join(DATA, "laws_flat.jsonl")
    meta_path = os.path.join(DATA, "laws_meta.jsonl")
    flat_n = meta_n = 0
    with open(flat_path, "w", encoding="utf-8") as ff, open(meta_path, "w", encoding="utf-8") as fm:
        for law in data["Laws"]:
            pcode = pcode_of(law)
            law["_pcode"] = pcode
            arts = law_articles(law)
            meta = {
                "pcode": pcode,
                "law_name": law.get("LawName", ""),
                "law_level": law.get("LawLevel", ""),
                "law_category": law.get("LawCategory", ""),
                "law_modified_date": law.get("LawModifiedDate", ""),
                "law_effective_date": law.get("LawEffectiveDate", ""),
                "law_effective_note": law.get("LawEffectiveNote", ""),
                "is_abandoned": bool(law.get("LawAbandonNote")),
                "law_abandon_note": law.get("LawAbandonNote", "").strip(),
                "has_eng": law.get("LawHasEngVersion", "") == "Y",
                "eng_name": law.get("EngLawName", ""),
                "attach_count": len(law.get("LawAttachements") or []),
                "law_histories": law.get("LawHistories", ""),
                "law_foreword": law.get("LawForeword", ""),
                "article_count": len(arts),
            }
            fm.write(json.dumps(meta, ensure_ascii=False) + "\n")
            meta_n += 1
            for r in arts:
                ff.write(json.dumps(r, ensure_ascii=False) + "\n")
                flat_n += 1
    print(f"laws 層級 {meta_n} 部 → {meta_path}")
    print(f"條文扁平 {flat_n} 列 → {flat_path}")


if __name__ == "__main__":
    # 透過 duckdb 若存在也能讀本 jsonl；此腳本無 third-party 依賴
    sys.exit(main())