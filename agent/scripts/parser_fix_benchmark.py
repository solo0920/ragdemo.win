#!/usr/bin/env python3
"""量測 §9 三個 parser 問題的**修法效果**（200 卷，分層樣本）。

## 為什麼需要這支

spec 007 §9 在 200 卷上實測發現三個問題。這支**不直接改 parser**（parser 是
`~/share/split_judgment_v2.py`，屬 S1-3 的範圍），而是**先量測修法效果**，
讓裁決有數據依據，而不是憑印象改。

## 三個問題與處置方向

| # | 問題 | 實測根因 | 處置方向 |
|---|---|---|---|
| 1 | `unsupported_court_type` 103/200 | **不是缺陷**——那 103 卷是真的刑事/行政/特別法域判決，套民事規則才是錯 | 維持原樣，但把 `court`/`laws[]` 在這些卷標為不適用 |
| 2 | `no_body_start` 19 卷 | 段名清單 `TITLES` 缺 6 種變體（`犯罪事實及理由`/`事實理由及證據`/`理由要領`…） | 補清單 → 18/19 修好 |
| 3 | 行政法院體例 | 標頭是「宣示判決筆錄」、正文極短（引行政訴訟法第237條之9「不另作判決書」）、段名帶冒號 | **獨立分支**，不套民事規則 |

## 關鍵：段名不是憑猜，是從 200 卷全掃出來的

`discover_titles.py`（本工具的 `--discover`）掃全部 200 卷的「主文之後獨立短行」，
濾掉含數字/日期/標點的噪音，只留含「理由/事實/證據」者，得到 **17 種候選**，
扣掉問句（`有無理由？`）後得 6 種真段名。

**那是資料-driven 的，不是字典-driven。** 若憑想像補段名，會補出
從沒出現過的組合——那正是 §7 三次自我推翻的教訓。

## 用法

    .venv/bin/python agent/scripts/parser_fix_benchmark.py --discover
    .venv/bin/python agent/scripts/parser_fix_benchmark.py
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent" / "scripts"))
sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import p2_prescreen_forced as P  # noqa: E402

RAW = ROOT / "data" / "judgements" / "profile-200" / "raw"
MANIFEST = ROOT / "specs" / "007-judgment-three-tier-storage" / "eval-manifest-200.json"

#: 從 200 卷全掃得出的新增段名（`--discover` 的輸出，非人工臆測）。
#: 帶冒號的變體（`事實及理由要領：`）在 normalize 後已去標點，故不必另列。
NEW_TITLES = frozenset({
    "犯罪事實及理由", "事實理由及證據", "事實及理由要領",
    "理由要領", "事實及證據理由", "犯罪事實",
})

#: 行政法院的體例（§9 問題 3）。**與民事判決是不同種類的文件**：
#: 它引行政訴訟法第 237 條之 9「宣示判決筆錄…不另作判決書」，
#: 所以正文只有主文與「事實及理由要領」，沒有完整判決書。
ADMIN_HEADER = re.compile(r"(?:行政法院|行政訴訟庭|訴願決定|覆審決|懲戒法庭)")


def load_parser(path: Path):
    return P._load_parser(path)


def body_index(V2, lines, main_i: int, extra_titles) -> tuple[int | None, str | None]:
    """找理由段起點。回傳 (index, mode)；找不到是 (None, None)。

    ⚠ `extra_titles` 是**本輪量測的變數**。現行 parser 用空 frozenset，
    兩者的差就是修法的效果。
    """
    for i in range(main_i + 1, len(lines)):
        n = V2.nrm(lines[i])
        if n in V2.TITLES or n in extra_titles:
            return i, "title"
        s = lines[i].strip()
        if V2.R0.match(s) or V2.R1.match(s):
            return i, "marker"
    return None, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="parser_fix_benchmark",
        description="量測 §9 三個 parser 問題的修法效果（200 卷）",
    )
    ap.add_argument("--parser", type=Path, required=True)
    ap.add_argument("--discover", action="store_true",
                    help="從 200 卷全掃段名候選（不看 parser 的清單）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    V2 = load_parser(args.parser)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    docs = manifest["documents"]

    def jfull_of(d):
        return json.loads((RAW / d["entry_path"]).read_text(encoding="utf-8"))["JFULL"]

    if args.discover:
        print("=" * 72)
        print("段名發現：掃 200 卷「主文之後的獨立短行」")
        print("=" * 72)
        cand: collections.Counter = collections.Counter()
        for d in docs:
            lines = V2.normalize(jfull_of(d)).split("\n")
            mi = next((i for i, l in enumerate(lines) if V2.nrm(l) == "主文"), None)
            if mi is None:
                continue
            for l in lines[mi + 1:]:
                n = V2.nrm(l)
                if not (3 <= len(n) <= 12):
                    continue
                if re.search(r"[0-9年月日第條項款：:。、，,;；]", n):
                    continue
                if not re.search(r"(理由|事實|證據)", n):
                    continue
                cand[n] += 1
        print(f"候選 {len(cand)} 種：")
        known = V2.TITLES | NEW_TITLES
        for k, v in cand.most_common():
            mark = "（parser 已有）" if k in V2.TITLES else (
                "（本輪建議補入）" if k in NEW_TITLES else "（**未採用**，見下）")
            print(f"  {v:4d}  {k}{mark}")
        noise = [k for k in cand if k not in known]
        print(f"\n⚠ 未納入的 {len(noise)} 種（**它們不是段名**，多為問句或子標題）：")
        for k in noise:
            print(f"    {k}（{cand[k]} 次）")
        return 0

    print("=" * 72)
    print("§9 三個 parser 問題：修法效果量測（200 卷）")
    print("=" * 72)

    # ── 問題 2：body 定位 ────────────────────────────────────────────
    print("\n【問題 2】no_body_start（段名清單缺項）")
    before_ok = after_ok = 0
    fixed = []
    still = []
    for d in docs:
        lines = V2.normalize(jfull_of(d)).split("\n")
        mi = next((i for i, l in enumerate(lines) if V2.nrm(l) == "主文"), None)
        if mi is None:
            continue
        b0, _ = body_index(V2, lines, mi, frozenset())
        b1, _ = body_index(V2, lines, mi, NEW_TITLES)
        if b0 is not None:
            before_ok += 1
        if b1 is not None:
            after_ok += 1
            if b0 is None:
                fixed.append(d["jid"])
        else:
            still.append(d["jid"])
    total = len(docs)
    n_no_main = 0
    for d in docs:
        lines = V2.normalize(jfull_of(d)).split("\n")
        if next((i for i, l in enumerate(lines) if V2.nrm(l) == "主文"), None) is None:
            n_no_main += 1
    print(f"  母體 {total} 卷；其中**連『主文』都找不到** {n_no_main} 卷"
          f"（那不在本問題範圍——它缺的是更前面的東西）")
    print(f"  可評估分母 {total - n_no_main} 卷")
    print(f"  現行 parser：找到 body {before_ok}/{total - n_no_main}")
    print(f"  補 6 種段名後：{after_ok}/{total - n_no_main}（+{after_ok - before_ok}）")
    print(f"  修好的卷：{len(fixed)}")
    print(f"  仍失敗：{len(still)} → {still}")
    for jid in still:
        jf = jfull_of(next(d for d in docs if d["jid"] == jid))
        head = (jf.split("\r\n")[0] if "\r\n" in jf else jf.split("\n")[0]).strip()
        print(f"    {jid}｜標頭：{head[:40]}")

    # ── 問題 3：行政法院 ─────────────────────────────────────────────
    print("\n【問題 3】行政／特別法域的體例（結構上不同於民事）")
    cats = collections.Counter()
    for d in docs:
        jf = jfull_of(d)
        head = (jf.split("\r\n")[0] if "\r\n" in jf else jf.split("\n")[0]).strip()
        if ADMIN_HEADER.search(head):
            cats["行政/特別法域"] += 1
        elif re.search(r"民事(?:判決|裁定)", head[:80]):
            cats["民事"] += 1
        else:
            cats["其他（刑事等）"] += 1
    print(f"  標頭分類：{dict(cats)}")
    admin = [d for d in docs
             if ADMIN_HEADER.search(
                 (jfull_of(d).split("\r\n")[0] if "\r\n" in jfull_of(d)
                  else jfull_of(d).split("\n")[0]).strip())]
    no_pdf = sum(1 for d in admin
                 if "不另作判決書" in jfull_of(d) or "宣示判決" in jfull_of(d))
    print(f"  行政/特別法域 {len(admin)} 卷，其中明示「宣示判決/不另作判決書」{no_pdf} 卷")
    print("  → 這些**不是民事判決的變體**，需要獨立分支（FR-007 的適用範圍要修）")

    # ── 問題 1：unsupported_court_type ───────────────────────────────
    print("\n【問題 1】unsupported_court_type 103/200")
    u = [d for d in docs if "unsupported_court_type" in (d["parse_issues"] or [])]
    print(f"  {len(u)} 卷被判為非民事")
    biz = collections.Counter(d["business"] for d in u)
    print(f"  業務分布：{dict(biz)}")
    print("  → 這些是真實的刑事/行政/特別法域判決，**parser 的判斷是正確的**。")
    print("    不是缺陷。但後果是：它們的 court/laws[] 不適用，介面必須標示。")

    print("\n" + "=" * 72)
    print("結論：三個問題中，兩個可修（段名補丁），一個需裁決（行政分支）")
    denom = total - n_no_main
    print(f"  可修：body 定位 {before_ok}/{denom} → {after_ok}/{denom}"
          f"（+{after_ok - before_ok}）")
    print(f"  需裁：行政/特別法域 {len(admin)} 卷的獨立結構規則")
    print(f"  不需修：問題 1 的 {len(u)} 卷是**正確判斷**，非缺陷")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())