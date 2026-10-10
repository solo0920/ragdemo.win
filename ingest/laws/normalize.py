#!/usr/bin/env python3
"""法規 JSON 清洗→扁平化（moj 官方 API，**法規＋命令**）。

輸入（兩個 API，2026-10-10 起）：
  data/laws/ChLaw.json     https://law.moj.gov.tw/api/ch/law/json
                           → 1,351 部（法律 1,342＋憲法 9）
  data/laws/ChOrder.json   https://law.moj.gov.tw/api/ch/order/json
                           → 10,452 部（命令）
  兩個 `Laws[]` 的 schema **完全相同**，故同一份 `law_articles()` 可直接套用。

⚠ **為什麼要加 order**（2026-10-10）：金融法規 99 部中有 52 部是「命令」
（`證券商管理規則`、`期貨商管理規則`、`保險代理人管理規則`…），
只抓 law API 會讓整個命令層缺席——那些法規**連條文全文都取不到**。
實測：law ∪ order 的 pcode 與 LawName **皆零重疊**，故可合併進同一張 `law` 表。

⚠ **`article_seq` 是位置序號，不是條號**（沿用既有約束）：司法院刪掉中間的條文時
後面所有條的 seq 往前挪一位 → `ON CONFLICT (pcode, article_seq) DO UPDATE`
會更新到「別條」。故 pg_load 必須做 stale 刪除（它已有）。

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

#: 來源清單。**兩個 API 都要**（見 docstring）。順序有意義：
#: 先 law 後 order，輸出因此是「法律在前、命令在後」，可重現。
SOURCES = (
    ("law", os.path.join(DATA, "ChLaw.json")),
    ("order", os.path.join(DATA, "ChOrder.json")),
)

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
            # ⚠ FR-017：`LawURL` 必須一路帶到 pg 與 qdrant——它是引用呈現的
            # 官方連結來源（spec 007 FR-018）。丟在這裡就沒地方再撿回來。
            "law_url": law.get("LawURL", ""),
            "source_api": law.get("_source", ""),
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
    flat_path = os.path.join(DATA, "laws_flat.jsonl")
    meta_path = os.path.join(DATA, "laws_meta.jsonl")
    flat_n = meta_n = 0
    seen_pcode: set[str] = set()
    dup: list[str] = []
    missing_src: list[str] = []

    # ⚠ `newline="\n"` 不可省（沿用 M1 的教訓）：預設的 universal newlines 會把
    # **條文內容裡的換行**寫成不同表示。條文含 `\n` 是常態（實測 26,890/47,284 筆），
    # 那是被 `json.dumps` 轉義成兩字元的 `\n`，**不是**真的斷行，所以預設安全；
    # 但顯式指定可杜絕將來有人改成 `ensure_ascii=False` + 真的換行的情形。
    with open(flat_path, "w", encoding="utf-8", newline="\n") as ff, \
            open(meta_path, "w", encoding="utf-8", newline="\n") as fm:
        for tag, src in SOURCES:
            if not os.path.isfile(src):
                missing_src.append(src)
                continue
            with open(src, encoding="utf-8-sig") as f:
                data = json.load(f)
            n_meta = n_flat = 0
            for law in data["Laws"]:
                pcode = pcode_of(law)
                # 兩個 API 的 pcode 實測零重疊，但**仍要擋**：
                # 若上游哪天改了 pcode 規則，重複主鍵會讓 ON CONFLICT 靜默覆寫另一部法。
                if pcode in seen_pcode:
                    dup.append(f"{tag}:{law.get('LawName', '')}({pcode})")
                    continue
                seen_pcode.add(pcode)
                law["_pcode"] = pcode
                law["_source"] = tag
                arts = law_articles(law)
                meta = {
                    "pcode": pcode,
                    "law_name": law.get("LawName", ""),
                    "law_level": law.get("LawLevel", ""),
                    "source_api": tag,
                    "law_url": law.get("LawURL", ""),
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
                n_meta += 1
                for r in arts:
                    ff.write(json.dumps(r, ensure_ascii=False) + "\n")
                    flat_n += 1
                    n_flat += 1
            print(f"[{tag}] {n_meta:,} 部法規 / {n_flat:,} 條文 ← {os.path.basename(src)}")

    if missing_src:
        # **不靜默跳過**：缺一份來源等於少一整層（命令層 10,452 部）。
        # 只印警告會讓人以為重建完整了。
        print("✗ 缺少來源檔：", file=sys.stderr)
        for s in missing_src:
            print(f"  {s}", file=sys.stderr)
        raise SystemExit(2)
    if dup:
        print(f"✗ pcode 重複，已跳過 {len(dup)} 部（否則會靜默覆寫）：", file=sys.stderr)
        for d in dup[:10]:
            print(f"  {d}", file=sys.stderr)
        raise SystemExit(3)

    print(f"laws 層級 {meta_n} 部 → {meta_path}")
    print(f"條文扁平 {flat_n} 列 → {flat_path}")


if __name__ == "__main__":
    # 透過 duckdb 若存在也能讀本 jsonl；此腳本無 third-party 依賴
    sys.exit(main())