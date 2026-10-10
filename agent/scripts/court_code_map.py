#!/usr/bin/env python3
"""從 archive 目錄結構建立 **`jid` 第一欄代碼 ↔ 法院目錄**的對照表。

## 為什麼這件事能只靠目錄就做完

S1 的 SC-002 ③ 要求「`jid` 第一欄法院代碼與 `court` 對應一致」，而
`normalize_district()` 的規則（SC-002 ② 的實作前提）需要知道**法院有哪幾個層級**
——那是規則的骨架。但規則草案若只在 M1 的 100 卷上做，會得到一個只含
4 個地院民事的骨架（那份樣本全庫只佔 108,409 檔中的 583 卷）。

本工具改從 **archive 目錄**（不解壓內容，只讀 RAR 目錄 entry）取得
**全庫 108,409 檔、137 個法院目錄**的完整樣本。規則草案因此涵蓋
最高／行政／高等／地院／簡易庭／少年家事／智財法院**七個層級**。

## 三個實測結論（這是規則草案的依據）

1. **代碼 ↔ 目錄是嚴格 1:1**：137 個代碼對 137 個目錄，
   **一碼多目錄 = 0**。故 `jid` 第一欄**足以唯一決定**法院目錄，
   兩者互為驗證（不符就是資料損毀，應失敗而非猜測）。
2. **`jid` 有兩種形狀**：6-field 108,270 筆、5-field 139 筆。
   5-field 全部是 `憲法法庭憲法`（代碼 `JCCC`）——**那個層級沒有民事/刑事之分**，
   `normalize_district()` 必須能處理它（否則 139 檔全部掉進未分類）。
3. **代碼末字 → 業務 幾乎是完整對應，只 1 個例外**（實測 137/137）：

   | 末字 | 業務 | 命中數 |
   |---|---|---:|
   | `V` | 民事 | 67 |
   | `M` | 刑事 | 57 |
   | `A` | 行政 | 8 |
   | `C` | 憲法 | 1 |
   | `P` | 懲戒 | 2 |
   | `U` | 家事 | 1 |
   | `A` | **訴願** | **1（`TPHA`／`臺灣高等法院--訴願決定`）** |

   即**唯一例外是 `TPHA`**（末字 `A` 卻是訴願決定，非行政）。
   ⚠ 但這**不足以讓規則依賴代碼**——因為：
   - **`TPHM`/`TPHV`（臺灣高等法院刑事/民事）的末字是 `M`/`V`，
     而 `TCHM`/`TCHV`（臺中分院）同樣是 `M`/`V`**：
     **同一首 3 字存在兩種業務**，所以**首 3 字不能反推業務**（78 種前綴裡 56 種多對一）。
   - `TPHA` 證明末字對應有破口，而 `TPHA` 只有 7 檔——**用 7 檔的破口去否定
     129 檔的規律不划算，但用它來決定「不依賴代碼」是划算的**。
   故結論是：**代碼可作為 `court_dir` 的**交叉驗證**（1:1 已實測），
   **不可作為 `court`／業務的來源**——那必須從目錄字串取，見規則草案。

## 用法

    .venv/bin/python agent/scripts/court_code_map.py
    .venv/bin/python agent/scripts/court_code_map.py --out specs/007-judgment-three-tier-storage/court-code-map.json
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import inventory  # noqa: E402

#: 層級判定。**順序有意義**：先判專門法院再判通則，因為
#: 「臺北高等行政法院 地方庭行政」同時含「高等」與「行政」，
#: 必須先被行政法院吃掉，否則會被歸成高等法院。
#: 最後的 `簡易庭` 是 fallback——它是「XX簡易庭」這種**沒有法院全名**的形式。
LEVELS: list[tuple[str, object]] = [
    ("最高", lambda d: d.startswith(("最高", "憲法法庭", "懲戒法院", "司法院"))),
    ("行政法院", lambda d: "行政法院" in d),
    ("高等法院", lambda d: "高等法院" in d),
    ("地方法院", lambda d: "地方法院" in d),
    ("少年家事", lambda d: "少年及家事" in d),
    ("智財法院", lambda d: "智慧財產及商業法院" in d),
    ("簡易庭", lambda d: "簡易庭" in d),
]

#: 目錄尾字 → 業務。**注意這是從目錄字串讀出來的，不是從代碼反推的**
#: （見 docstring 結論 3：代碼尾字不可反推業務）。
BUSINESS_SUFFIX = ("民事", "刑事", "行政", "憲法", "家事", "訴願", "懲戒", "補償")


def classify_level(court_dir: str) -> str:
    for name, fn in LEVELS:
        if fn(court_dir):
            return str(name)
    return "★未分類★"


def business_of(court_dir: str) -> str:
    """從**目錄字串**取業務。取不到就回 `unknown`——不猜。"""
    for suf in BUSINESS_SUFFIX:
        if court_dir.endswith(suf):
            return suf
    for suf in BUSINESS_SUFFIX:  # 「臺北高等行政法院 地方庭行政」有前綴
        if suf in court_dir:
            return suf
    return "unknown"


def collect() -> dict:
    pairs: collections.Counter = collections.Counter()
    code_dirs: dict[str, set[str]] = collections.defaultdict(set)
    nfields: collections.Counter = collections.Counter()
    depth_bad = 0

    for e in inventory.inventory():
        if e.is_dir:
            continue
        parts = e.path.split("\\")
        if len(parts) != 3 or not parts[2].endswith(".json"):
            depth_bad += 1
            continue
        court_dir, jid = parts[1], parts[2][:-5]
        code = jid.split(",")[0]
        nfields[len(jid.split(","))] += 1
        pairs[(court_dir, code)] += 1
        code_dirs[code].add(court_dir)

    return {
        "pairs": pairs,
        "code_dirs": code_dirs,
        "nfields": nfields,
        "depth_bad": depth_bad,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="court_code_map",
        description="由 archive 目錄建立 jid 第一欄代碼 ↔ 法院目錄對照表",
    )
    ap.add_argument("--out", type=Path, default=None, help="寫出 JSON（預設印 stdout）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    d = collect()
    pairs: collections.Counter = d["pairs"]
    code_dirs: dict[str, set[str]] = d["code_dirs"]
    total = sum(pairs.values())
    multi = {c: sorted(v) for c, v in code_dirs.items() if len(v) > 1}

    print(f"# `jid` 第一欄代碼 ↔ 法院目錄（archive 目錄，不解壓內容）\n")
    print(f"檔案總數 **{total:,}**｜法院目錄 **{len(pairs)}**｜代碼 **{len(code_dirs)}**")
    print(f"深度不是 3 層而跳過的 entry：{d['depth_bad']}")
    print(f"`jid` 欄數分布：{dict(sorted(d['nfields'].items()))}")
    print(f"\n**一碼多目錄：{len(multi)}**" + (f" → {multi}" if multi else "（嚴格 1:1）"))

    print(f"\n⚠ 5-field 的 `jid`：{d['nfields'].get(5, 0):,} 筆（總數 {total:,}）"
          "，全部落在 `憲法法庭憲法`（代碼 `JCCC`）——**該層級無民事/刑事之分**。")

    rows = []
    for (court, code), n in sorted(pairs.items(), key=lambda x: -x[1]):
        rows.append({
            "court_code": code,
            "court_dir": court,
            "level": classify_level(court),
            "business": business_of(court),
            "documents": n,
            "distinct_dirs": len(code_dirs[code]),
        })

    by_level: dict[str, dict] = collections.defaultdict(
        lambda: {"dirs": 0, "documents": 0}
    )
    for r in rows:
        b = by_level[r["level"]]
        b["dirs"] += 1
        b["documents"] += r["documents"]

    print("\n## 層級分布\n")
    print("| 層級 | 目錄數 | 檔案數 |")
    print("|---|---:|---:|")
    for lv, b in sorted(by_level.items(), key=lambda x: -x[1]["documents"]):
        print(f"| {lv} | {b['dirs']} | {b['documents']:,} |")
    print(f"| **合計** | **{len(rows)}** | **{total:,}** |")

    payload = {
        "source": "archive 目錄（RAR directory entries，不解壓內容）",
        "documents": total,
        "directories": len(rows),
        "codes": len(code_dirs),
        "codes_with_multiple_dirs": multi,
        "jid_field_count_histogram": dict(sorted(d["nfields"].items())),
        "level_histogram": {k: v for k, v in by_level.items()},
        "rows": rows,
    }
    if args.out:
        args.out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"\n對照表寫入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())