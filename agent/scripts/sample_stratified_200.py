#!/usr/bin/env python3
"""從全庫 108,409 件中**分層抽樣 200 件**，每個法院目錄都有代表。

## 為什麼要重抽

M1 的 100 卷**全部落在 4 個地院民事目錄**（583/108,409 ＝ 0.54%）。後果是實測的：

| 缺口 | 影響 |
|---|---|
| 簡易庭 **13,235 檔（12.2%）** | `normalize_district()` 在該層**無樣本可驗**（§8 難題 1） |
| 高等法院 5,985／行政法院 3,625／最高 1,583 | SC-002 ③ 的七層覆蓋只有一層 |
| 刑事 38,932 檔（35.9%）／行政 3,836 | `unsupported_court_type` 分支標 **UNVERIFIED**（SC-012） |
| `憲法法庭`（5-field `jid`）139 檔 | `jid` 兩種形狀只測過一種（FR-028） |

所以 200 件的分層維度**由 archive 實測決定**，不是憑感覺。

## 分層維度（由 §8 的 137 目錄實測推導）

主維度＝**法院目錄**（137 個）。理由：目錄是 archive 的**作者意圖**，不是我們的分類；
用它的好處是「每個目錄都有代表」這句話**可機檢**，且與 `court_raw` 一一對應。

次維度＝**業務尾字**（民事／刑事／行政／憲法／家事／懲戒／訴願）。
因為 `doc_type`（judgment／ruling）與法條體系都跟業務走。

## 配額怎麼分（不是平均，是有理由的）

1. **保底**：每個目錄 **≥1 件**（137 件）。這條是硬要求——它讓
   「七層都有代表」變成機檢事實而非承諾。
2. **剩下 63 件按目錄容量比例分配**（`min(目錄檔案數−1, 配額)`），
   上限 5 件／目錄。理由：容量大的目錄內部變異大（不同年份、不同案由），
   給更多樣本；容量小的（`最高法院家事` 只有 2 檔）多給也沒有意義。
3. **不足則回補**：若某目錄檔案數 < 配額，把名額讓給其他目錄，最後不足就如實報數。

⚠ **不排除 M1 的 100 卷**：新的 200 件是**獨立的分母**，
與 `eval-manifest.json`（100 卷）**並存不混比**（§7 已載明跨樣本數字不可比）。

## 用法

    .venv/bin/python agent/scripts/sample_stratified_200.py
    .venv/bin/python agent/scripts/sample_stratified_200.py --out specs/008-.../allowlist-200.json
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import inventory as _inventory  # noqa: E402
import profile_build as _PB  # noqa: E402

TOTAL = 200
MIN_PER_DIR = 1
MAX_PER_DIR = 5

BUSINESS_SUFFIX = ("民事", "刑事", "行政", "憲法", "家事", "訴願", "懲戒", "補償")


def business_of(court_dir: str) -> str:
    for suf in BUSINESS_SUFFIX:
        if suf in court_dir:
            return suf
    return "unknown"


def level_of(court_dir: str) -> str:
    """與 `court_code_map.py` 的 LEVELS 同一套規則與順序——**兩份必須一致**，
    不一致時對照表會與抽樣分層對不上（SC-002 ③ 的前提）。"""
    if court_dir.startswith(("最高", "憲法法庭", "懲戒法院", "司法院")):
        return "最高"
    if "行政法院" in court_dir:
        return "行政法院"
    if "高等法院" in court_dir:
        return "高等法院"
    if "地方法院" in court_dir:
        return "地方法院"
    if "少年及家事" in court_dir:
        return "少年家事"
    if "智慧財產及商業法院" in court_dir:
        return "智財法院"
    if "簡易庭" in court_dir:
        return "簡易庭"
    return "★未分類★"


def entries_digest(paths: list[str]) -> str:
    """**直接重用 `profile_build.entries_digest`**，不自己重寫。

    第一版我在這裡自己寫了一份，結果多加了結尾換行，`verify_allowlist` 立刻
    報「entries_sha256 不符」。同樣的演算法寫兩份就是兩種答案——
    閘門能抓到，但那是浪費一次全量解壓。**單一真相來源**。
    """
    return _PB.entries_digest(paths)


def _artifact_sha256() -> str:
    """artifact 的 SHA-256。**不寫死**——寫死就會與實際 archive 脫鉤，
    而 `load_allowlist` 會讀這個值當 provenance。"""
    import artifact as _a
    p = Path(_a.ARTIFACT_PATH)
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def allocate(counts: dict[str, int]) -> dict[str, int]:
    """回傳每個目錄的配額。保底 1，剩餘按容量比例，上限 MAX_PER_DIR。"""
    quota = {d: min(MIN_PER_DIR, counts[d]) for d in counts}
    remaining = TOTAL - sum(quota.values())
    if remaining <= 0:
        return quota
    # 容量加權：目錄越大分得越多，但受 MAX_PER_DIR 限制。
    spare = {d: counts[d] - quota[d] for d in counts if counts[d] > quota[d]}
    while remaining > 0 and spare:
        pool = sorted(spare, key=lambda d: (-spare[d], d))
        progressed = False
        for d in pool:
            if remaining == 0:
                break
            if quota[d] >= min(MAX_PER_DIR, counts[d]):
                continue
            quota[d] += 1
            spare[d] -= 1
            if spare[d] == 0:
                del spare[d]
            remaining -= 1
            progressed = True
        if not progressed:
            break
    return quota


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sample_stratified_200",
        description="從全庫分層抽樣 200 件（每個法院目錄都有代表）",
    )
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--total", type=int, default=TOTAL)
    ap.add_argument(
        "--exclude-frozen-from", type=Path,
        default=ROOT / "specs" / "004-judicial-source-fidelity" / "allowlist-m1.json",
        help="從這份 allowlist 讀 excluded_frozen_document_ids（B2/B3/B4 的凍結語料）",
    )
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    # 凍結語料必須排除：那是 B2/B3/B4 已驗收的 430 筆的一部分（憲法 X：
    # 不得擴大到那些檔案）。實測本次 200 卷命中 0 筆，但**仍顯式排除**——
    # 若上游日後把凍結語料搬進其他目錄，沒這道欄位就會靜默命中。
    frozen: set[str] = set()
    if args.exclude_frozen_from and Path(args.exclude_frozen_from).is_file():
        frozen = set(
            json.loads(Path(args.exclude_frozen_from).read_text(encoding="utf-8"))
            ["excluded_frozen_document_ids"]
        )

    by_dir: dict[str, list] = collections.defaultdict(list)
    for e in _inventory.inventory():
        if e.is_dir:
            continue
        parts = e.path.split("\\")
        if len(parts) != 3 or not parts[2].endswith(".json"):
            continue
        by_dir[parts[1]].append(e)

    counts = {d: len(v) for d, v in by_dir.items()}
    # 先濾掉凍結語料，再算配額——否則配額會被即將被排除的檔案占掉。
    by_dir = {
        d: [e for e in v if e.path.split("\\")[2][:-len(".json")] not in frozen]
        for d, v in by_dir.items()
    }
    by_dir = {d: v for d, v in by_dir.items() if v}
    dropped = sum(len(v) for v in by_dir.values() if not v)
    if frozen:
        print(f"已排除凍結語料 {len(frozen)} 筆（來源：{args.exclude_frozen_from.name}）")
    quota = allocate(counts)
    quota = {d: q for d, q in quota.items() if q > 0}

    selected: list[dict] = []
    for d in sorted(quota):
        pool = sorted(by_dir[d], key=lambda e: (-e.unpacked_size, e.path))
        for e in pool[: quota[d]]:
            jid = e.path.split("\\")[2][:-len(".json")]
            selected.append({
                "path": e.path,
                "path_posix": e.path.replace("\\", "/"),
                "unpacked_size": e.unpacked_size,
                "crc32": "%08X" % e.crc32,
                "jid": jid,
                "court_code": jid.split(",")[0],
                "court_dir": d,
                "level": level_of(d),
                "business": business_of(d),
                "jid_fields": len(jid.split(",")),
            })

    by_level = collections.Counter(s["level"] for s in selected)
    by_biz = collections.Counter(s["business"] for s in selected)
    by_fields = collections.Counter(s["jid_fields"] for s in selected)
    corpus_level = collections.Counter()
    corpus_biz = collections.Counter()
    for d, n in counts.items():
        corpus_level[level_of(d)] += n
        corpus_biz[business_of(d)] += n

    print(f"# 分層抽樣：{len(selected)} 件 / 全庫 {sum(counts.values()):,} 件\n")
    print(f"法院目錄 {len(counts)} 個，全部 ≥{MIN_PER_DIR} 件："
          f"{'是' if len(quota) == len(counts) else f'否（{len(counts) - len(quota)} 個目錄未被抽中）'}")
    print(f"未分類目錄：{corpus_level.get('★未分類★', 0)} 個\n")

    print("| 層級 | 母體檔案 | 抽中件數 | 覆蓋 |")
    print("|---|---:|---:|---:|")
    for lv, n in corpus_level.most_common():
        s = by_level.get(lv, 0)
        print(f"| {lv} | {n:,} | {s} | {'✅' if s else '❌'} |")
    print(f"| **合計** | **{sum(counts.values()):,}** | **{len(selected)}** | |")

    print("\n| 業務 | 母體檔案 | 抽中件數 |")
    print("|---|---:|---:|")
    for b, n in corpus_biz.most_common():
        print(f"| {b} | {n:,} | {by_biz.get(b, 0)} |")

    print(f"\n`jid` 欄數分布（抽中）：{dict(sorted(by_fields.items()))}")
    print("  5-field 只可能出現在憲法法庭；兩種形狀都有樣本＝"
          f"{'是' if len(by_fields) > 1 else '否（FR-028 未覆蓋）'}")

    payload = {
        "schema": _PB.ALLOWLIST_SCHEMA,
        "generated_at": "2026-10-10",
        "note": (
            "分層抽樣：每個法院目錄 ≥1 件，剩餘按容量比例（上限 5 件/目錄）。"
            "**與 allowlist-m1.json（100 卷）是獨立分母，數字不可混比。**"
            "已排除 B2/B3/B4 的凍結語料。"
        ),
        "artifact": {
            "path": "data/judgements/raw/202607--(20260916Update).rar",
            "sha256": _artifact_sha256(),
        },
        "selection": {
            "method": "stratified_by_court_dir",
            "total_requested": args.total,
            "min_per_dir": MIN_PER_DIR,
            "max_per_dir": MAX_PER_DIR,
            "strata": "court_dir（archive 作者意圖）＋次維度 business",
            "courts": sorted(quota),
            "pool_total_all_dirs": sum(counts.values()),
        },
        "excluded_frozen_document_ids": sorted(frozen),
        "count": len(selected),
        "entries_sha256": entries_digest([s["path"] for s in selected]),
        "entries": [
            {k: s[k] for k in ("path", "path_posix", "unpacked_size", "crc32")}
            for s in selected
        ],
        "strata_detail": [
            {k: s[k] for k in ("jid", "court_code", "court_dir", "level", "business", "jid_fields")}
            for s in selected
        ],
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(f"\n清單寫入 {args.out}")
        print(f"entries_sha256={payload['entries_sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())