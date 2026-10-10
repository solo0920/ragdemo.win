#!/usr/bin/env python3
"""產出 11 卷行政／特別法域的 mention 核對表（jid、法條字樣、前後 30 字、offset）。

## 母體與分母

- 母體：`eval-manifest-200.json` 的 200 卷中，`unsupported_court_type`
  **且**標頭命中行政／特別法域關鍵字者（實測 11 卷，與 court-type-breakdown.txt
  同一規則 —— spec 008 宣稱的「12 卷」無法重現，詳見該檔）
- corpus：`data/laws/laws_flat.jsonl` 全量 11,803 部／222,109 條

## offset 的定義（不可含糊）

spec 008 的 FR-005 要求 evidence 帶「在判決中的 offset」。本工具的 offset 是
`extract_statute_mentions_with_spans()` 回傳的 **code-point 座標**（不是 bytes），
起訖皆指向**未 collapse 的原始 JFULL 字串**。這是實際回傳給呼叫端的值，
不是本工具自己重算的。

## 用法

    .venv/bin/python specs/008-evidence-first-stream/verify/admin-volume-mention-table.py \
        | tee specs/008-evidence-first-stream/evidence/admin-volume-mention-table.txt

**只回報，不改 Q2。**
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend" / "app"))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "specs" / "007-judgment-three-tier-storage" / "eval-manifest-200.json"
RAW = ROOT / "data" / "judgements" / "profile-200" / "raw"
LAWS_FLAT = ROOT / "data" / "laws" / "laws_flat.jsonl"

ADMIN_HEADER = re.compile(r"(?:行政法院|行政訴訟庭|訴願決定|覆審決|懲戒法庭|刑事補償法庭)")
CTX = 30  # 前後各 30 字


def head_of(entry_path: str) -> str:
    p = RAW / entry_path
    jf = json.loads(p.read_text(encoding="utf-8"))["JFULL"]
    return (jf.split("\r\n")[0] if "\r\n" in jf else jf.split("\n")[0]).strip()


def main() -> int:
    from app.b1_serve import StatuteCorpus, extract_statute_mentions_with_spans

    docs = json.loads(MANIFEST.read_text(encoding="utf-8"))["documents"]
    flagged = [d for d in docs if "unsupported_court_type" in (d["parse_issues"] or [])]
    admin = sorted((d for d in flagged if ADMIN_HEADER.search(head_of(d["entry_path"]))),
                   key=lambda x: x["jid"])

    corpus = StatuteCorpus.from_jsonl(LAWS_FLAT)

    print("=" * 100)
    print("11 卷行政／特別法域的 mention 核對表")
    print("=" * 100)
    print(f"母體：200 卷中 unsupported_court_type 且標頭命中行政關鍵字 → {len(admin)} 卷")
    print(f"corpus：{len(corpus.rows):,} 條文／{len(corpus.law_names):,} 部法規")
    print(f"offset 定義：extract_statute_mentions_with_spans 回傳的 code-point 座標")
    print(f"             指向**未 collapse 的原始 JFULL**（非 bytes 偏移）")
    print(f"上下文：mention 前後各 {CTX} 字")
    print()

    grand = 0
    grand_scoped = 0
    for d in admin:
        jf = json.loads((RAW / d["entry_path"]).read_text(encoding="utf-8"))["JFULL"]
        rows = extract_statute_mentions_with_spans(jf, corpus)
        nscoped = sum(1 for m, *_ in rows if m.scoped)
        grand += len(rows)
        grand_scoped += nscoped
        print("=" * 100)
        print(f"jid      : {d['jid']}")
        print(f"標頭     : {head_of(d['entry_path'])}")
        print(f"JFULL    : {len(jf):,} 字元")
        print(f"mention  : {len(rows)} 筆（其中 scoped {nscoped} 筆）")
        print("=" * 100)
        if not rows:
            print("  （無 mention）")
            print()
            continue
        for m, s, e, sent in rows:
            before = jf[max(0, s - CTX):s]
            after = jf[e:e + CTX]
            print(f"  offset [{s:>6}, {e:>6})  {m.law_name}{m.article}{m.detail or ''}")
            print(f"    surface     : {m.surface!r}")
            print(f"    scoped      : {m.scoped}"
                  + (f"  scoped_from={m.scoped_from!r}" if m.scoped else ""))
            print(f"    collapsed   : {m.surface_collapsed}")
            print(f"    前 {CTX} 字  : …{before}[{m.surface}]{after}…")
            print()
    print("=" * 100)
    print(f"合計：{len(admin)} 卷／{grand} 筆 mention（scoped {grand_scoped} 筆"
          f" = {grand_scoped / grand * 100:.1f}%）")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())