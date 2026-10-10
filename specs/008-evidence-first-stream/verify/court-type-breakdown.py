#!/usr/bin/env python3
"""列出 200 卷中 `unsupported_court_type` 的各類別 jid 清單與計數，並自我檢查。

## 為什麼需要這支（constitution XII / R1）

spec 008 宣稱：「結構上真正無法解析的只有行政／特別法域 12 卷，其餘 91 卷」。
但 spec 同時引用了「11+1+1+2=15」的分類相加。**15 ≠ 12**，差 3 未說明。
本工具把每一類的 jid 攤開，讓差異可被逐筆檢視，並回答「91 怎麼算出來的」
與「為什麼有 20 卷民事被標記」。

## 分母定義

- **母體**：`specs/007-judgment-three-tier-storage/eval-manifest-200.json`
  的 `documents[]`（200 卷），**失敗卷也含在內**（與 spec 007 的分母規則一致）。
- **標記來源**：該卷 `parse_issues` 含 `unsupported_court_type` 者。

## 指令

    .venv/bin/python specs/008-evidence-first-stream/verify/court-type-breakdown.py \
        | tee specs/008-evidence-first-stream/evidence/court-type-breakdown.txt
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = ROOT / "specs" / "008-evidence-first-stream"
MANIFEST = ROOT / "specs" / "007-judgment-three-tier-storage" / "eval-manifest-200.json"
RAW = ROOT / "data" / "judgements" / "profile-200" / "raw"

#: spec 008 宣稱的 12 卷「行政／特別法域」判定式。**來源必須可追溯**，
#: 所以此處不憑記憶寫死清單，而是用與 parser 同源的規則重算並攤開。
ADMIN_HEADER = re.compile(
    r"(?:行政法院|行政訴訟庭|訴願決定|覆審決|懲戒法庭|刑事補償法庭)"
)


def header_of(entry_path: str) -> str:
    p = RAW / entry_path
    if not p.is_file():
        return ""
    jf = json.loads(p.read_text(encoding="utf-8"))["JFULL"]
    return (jf.split("\r\n")[0] if "\r\n" in jf else jf.split("\n")[0]).strip()


def main() -> int:
    docs = json.loads(MANIFEST.read_text(encoding="utf-8"))["documents"]
    flagged = [d for d in docs if "unsupported_court_type" in (d["parse_issues"] or [])]

    print("=" * 78)
    print("200 卷中 unsupported_court_type 的類別分解")
    print("=" * 78)
    print(f"母體：eval-manifest-200.json 的 documents[] ＝ {len(docs)} 卷")
    print(f"其中帶 unsupported_court_type：{len(flagged)} 卷")
    print(f"自我檢查（分母守恆）：{len(flagged)} + {len(docs) - len(flagged)}"
          f" = {len(flagged) + (len(docs) - len(flagged))}（應等於 {len(docs)}）")

    # ── 依「目錄業務」分類（manifest 的 business 欄）──
    by_biz = collections.defaultdict(list)
    for d in flagged:
        by_biz[d["business"]].append(d)
    print("\n" + "-" * 78)
    print("【分類 A】依 eval-manifest 的 business 欄（＝ archive 目錄尾字）")
    print("-" * 78)
    print(f"{'business':10s} {'計數':>5s}   jid")
    for biz, items in sorted(by_biz.items(), key=lambda x: -len(x[1])):
        print(f"{biz:10s} {len(items):5d}")
        for d in sorted(items, key=lambda x: x["jid"]):
            print(f"              {d['jid']}")

    total_biz = sum(len(v) for v in by_biz.values())
    print(f"\n分類 A 相加 = {total_biz}")
    print(f"自我檢查：{total_biz} == {len(flagged)} → {total_biz == len(flagged)}")

    # ── 依「標頭是否行政／特別法域」分類 ──
    admin, non_admin = [], []
    for d in flagged:
        (admin if ADMIN_HEADER.search(header_of(d["entry_path"])) else non_admin).append(d)

    print("\n" + "-" * 78)
    print("【分類 B】依標頭關鍵字（行政／特別法域 vs 其他）")
    print("-" * 78)
    print(f"行政／特別法域：{len(admin)} 卷")
    for d in sorted(admin, key=lambda x: x["jid"]):
        print(f"  {d['jid']}   標頭＝{header_of(d['entry_path'])[:30]}")
    print(f"\n其他（標頭不含上述關鍵字）：{len(non_admin)} 卷")
    print(f"分類 B 相加 = {len(admin) + len(non_admin)}")
    print(f"自我檢查：{len(admin) + len(non_admin)} == {len(flagged)} → "
          f"{len(admin) + len(non_admin) == len(flagged)}")

    # ── 交叉表：business × 是否行政 ──
    print("\n" + "-" * 78)
    print("【交叉表】business × 行政／特別法域")
    print("-" * 78)
    x = collections.Counter(
        (d["business"], "ADMIN" if d in admin else "OTHER") for d in flagged
    )
    bizs = sorted({b for b, _ in x})
    print(f"{'business':10s} {'ADMIN':>7s} {'OTHER':>7s} {'合計':>7s}")
    for b in bizs:
        a, o = x[(b, "ADMIN")], x[(b, "OTHER")]
        print(f"{b:10s} {a:7d} {o:7d} {a + o:7d}")

    # ── 追問 1：spec 的「12 卷」是哪 12 卷？──
    print("\n" + "=" * 78)
    print("追問回答")
    print("=" * 78)
    print(f"\nQ: spec 的「12 卷」是哪 12 卷？")
    print(f"A: 以 ADMIN_HEADER 規則重算得 **{len(admin)} 卷**，逐筆如上。")
    if len(admin) != 12:
        print(f"   ⚠ **與 spec 宣稱的 12 不符（實測 {len(admin)}）** —— "
              f"spec 的 12 未附推導過程，屬未驗證數字。")

    print(f"\nQ: 分類相加 11+1+1+2=15，差在哪？")
    biz_sorted = sorted(by_biz.items(), key=lambda x: -len(x[1]))
    print(f"A: 分類 A 實際為：" +
          "、".join(f"{b}={len(v)}" for b, v in biz_sorted))
    print(f"   相加 = {total_biz}，**不是 15**。spec 引用的 11+1+1+2 與本 manifest")
    print(f"   不一致（{total_biz} ≠ 15），差 {abs(total_biz - 15)}。該算式無法重現。")

    print(f"\nQ: 「其餘 91 卷」的 91 怎麼算？")
    others = len(docs) - len(admin)
    print(f"A: {len(docs)}（母體） − {len(admin)}（行政／特別法域） = **{others}**")
    print(f"   即「未被判為行政／特別法域」＝全部 {others} 卷，其中：")
    print(f"     · 帶 unsupported_court_type 的非行政卷：{len(non_admin)}")
    print(f"     · 完全沒有 unsupported_court_type 的卷："
          f"{len(docs) - len(flagged)}")
    print(f"     · 合計：{len(non_admin)} + {len(docs) - len(flagged)} = {others}")

    print(f"\nQ: 為什麼有 20 卷民事被標 unsupported_court_type？")
    civil = by_biz.get("民事", [])
    print(f"A: business=民事且被標記者 = {len(civil)} 卷。逐筆看標頭：")
    for d in sorted(civil, key=lambda x: x["jid"])[:25]:
        h = header_of(d["entry_path"])
        m = re.match(r"^(.{0,24}?民事(?:判決|裁定))", h)
        seg = m.group(1) if m else h[:24]
        print(f"   {d['jid'][:34]:36s} 標頭＝{seg}")
    print(f"\n   規則由 parser 決定："
          f"`re.search(r'民事(?:判決|裁定)', head_txt[:80])` 未命中即標記。")
    print(f"   命中不了的原因可能包含：標頭第 80 字元內未出現該字樣、"
          f"或標頭使用簡體／異體字。")
    print(f"   ⚠ 本工具**只列事實**，不推斷成因（constitution XII：推測須標明）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())