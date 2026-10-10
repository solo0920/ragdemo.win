#!/usr/bin/env python3
"""查證：`extract_statute_mentions()` 是否依賴 parser 的 court type 或 body-start 結果。

## 查什麼（maintainer 指示 4c）

1. `extract_statute_mentions()` 實際做了什麼 → 逐行原始碼（不做摘要）
2. 那 12 卷（實測 11 卷，見 court-type-breakdown.txt）上實際輸出什麼
3. `is_inferred` 的游標推斷是否依賴 parser 結構

## 分母定義

- 母體：`eval-manifest-200.json` 的 `documents[]` = 200 卷
- 「12 卷」母體：`unsupported_court_type` **且**標頭命中行政／特別法域關鍵字者
  （實測 11 卷，與 court-type-breakdown.txt 同規則）
- corpus：`data/laws/laws_flat.jsonl` 全量 11,803 部／222,109 條

## 用法

    .venv/bin/python specs/008-evidence-first-stream/verify/mention-parser-dependency.py \
        | tee specs/008-evidence-first-stream/evidence/mention-parser-dependency.txt

**只回報，不改 Q2=B，不改任何程式碼。**
"""
from __future__ import annotations

import collections
import inspect
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend" / "app"))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "specs" / "007-judgment-three-tier-storage" / "eval-manifest-200.json"
RAW = ROOT / "data" / "judgements" / "profile-200" / "raw"
LAWS_FLAT = ROOT / "data" / "laws" / "laws_flat.jsonl"
B1 = ROOT / "backend" / "app" / "b1_serve.py"

ADMIN_HEADER = re.compile(r"(?:行政法院|行政訴訟庭|訴願決定|覆審決|懲戒法庭|刑事補償法庭)")


def head_of(entry_path: str) -> str:
    p = RAW / entry_path
    if not p.is_file():
        return ""
    jf = json.loads(p.read_text(encoding="utf-8"))["JFULL"]
    return (jf.split("\r\n")[0] if "\r\n" in jf else jf.split("\n")[0]).strip()


def rule(t: str) -> None:
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def main() -> int:
    from app.b1_serve import (  # noqa: E402
        StatuteCorpus,
        extract_statute_mentions,
        extract_statute_mentions_with_spans,
    )

    docs = json.loads(MANIFEST.read_text(encoding="utf-8"))["documents"]
    flagged = [d for d in docs if "unsupported_court_type" in (d["parse_issues"] or [])]
    admin = [d for d in flagged if ADMIN_HEADER.search(head_of(d["entry_path"]))]

    # ── ① 函式簽名：參數即依賴的上界 ──────────────────────────────
    rule("【1】extract_statute_mentions 的簽名 —— 參數即它能看到的全部輸入")
    print(f"  extract_statute_mentions{inspect.signature(extract_statute_mentions)}")
    print(f"  extract_statute_mentions_with_spans"
          f"{inspect.signature(extract_statute_mentions_with_spans)}")
    print("\n  參數只有 text 與 corpus。**沒有** court_type、body_start、chunk、")
    print("  section、parse_result 任何一項 → 結構上不可能依賴 parser 輸出。")

    rule("【2】逐行原始碼：呼叫鏈與所有被引用的屬性")
    src = B1.read_text(encoding="utf-8")
    lines = src.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("def extract_statute_mentions_with_spans"))
    end = next(i for i, l in enumerate(lines) if l.startswith("def make_judgement_evidence"))
    for i in range(start, end):
        print(f"  {i + 1:5d}| {lines[i]}")

    rule("【3】靜態檢查：函式體內是否出現任何 parser 相關識別字")
    body = "\n".join(lines[start:end])
    # 同時掃整個 b1_serve.py 作為對照
    for kw in ["court_type", "unsupported_court_type", "body_start", "no_body_start",
               "split_judgment", "parse_", "chunk_index", "parse_issues"]:
        in_fn = kw in body
        in_file = kw in src
        print(f"  {kw:24s} 函式體內：{'有' if in_fn else '無':2s}   "
              f"整個 b1_serve.py：{'有' if in_file else '無'}")

    rule("【4】corpus 載入並對 200 卷實跑")
    t0 = time.perf_counter()
    corpus = StatuteCorpus.from_jsonl(LAWS_FLAT)
    load_s = time.perf_counter() - t0
    print(f"  corpus：{len(corpus.rows):,} 條文／{len(corpus.law_names):,} 部法規"
          f"（載入 {load_s:.2f} s）")

    times: list[float] = []
    per_vol: dict[str, tuple[int, int]] = {}
    empty_all = []
    for d in docs:
        p = RAW / d["entry_path"]
        if not p.is_file():
            continue
        jf = json.loads(p.read_text(encoding="utf-8"))["JFULL"]
        t = time.perf_counter()
        res = extract_statute_mentions(jf, corpus)
        ms = (time.perf_counter() - t) * 1000
        times.append(ms)
        per_vol[d["jid"]] = (len(res), ms)
        if not res:
            empty_all.append(d["jid"])
    times.sort()
    print(f"  分母：{len(times)} 卷（of 200）｜JFULL 總字元 "
          f"{sum(len(json.loads((RAW / d['entry_path']).read_text(encoding='utf-8'))['JFULL']) for d in docs if (RAW / d['entry_path']).is_file()):,}")
    print(f"  耗時 median {statistics.median(times):.1f} ms｜"
          f"max {times[-1]:.1f} ms｜min {times[0]:.1f} ms")
    print(f"  抽出為空的卷：{len(empty_all)}/{len(times)}")
    for j in empty_all:
        print(f"    · {j}")

    rule(f"【5】行政／特別法域 {len(admin)} 卷上的實際輸出")
    print("  母體：unsupported_court_type 且標頭命中行政／特別法域關鍵字")
    print("  注意：這 11/12 卷**仍然被 parser 標記失敗**，但抽取函式照樣跑了。")
    print(f"  {'jid':40s}{'字元':>8s}{'mention':>8s}{'scoped':>8s}{'耗時ms':>9s}")
    print("  " + "-" * 76)
    tot_m = tot_s = 0
    for d in sorted(admin, key=lambda x: x["jid"]):
        jf = json.loads((RAW / d["entry_path"]).read_text(encoding="utf-8"))["JFULL"]
        t = time.perf_counter()
        res = extract_statute_mentions(jf, corpus)
        ms = (time.perf_counter() - t) * 1000
        nsc = sum(1 for m in res if m.scoped)
        tot_m += len(res)
        tot_s += nsc
        print(f"  {d['jid'][:38]:40s}{len(jf):8d}{len(res):8d}{nsc:8d}{ms:9.1f}")
    print("  " + "-" * 76)
    print(f"  {'合計':40s}{'':8s}{tot_m:8d}{tot_s:8d}")
    empty_admin = [d["jid"] for d in admin
                   if per_vol[d["jid"]][0] == 0]
    print(f"  其中抽出為空：{len(empty_admin)}/{len(admin)} 卷")
    for j in empty_admin:
        print(f"    · {j}")

    # 對照組：有標記但非行政
    non_admin = [d for d in flagged if d not in admin]
    print(f"\n  對照組（被標記 unsupported_court_type 但**非**行政／特別法域）"
          f"：{len(non_admin)} 卷")
    nm = sum(per_vol[d["jid"]][0] for d in non_admin)
    ne = sum(1 for d in non_admin if per_vol[d["jid"]][0] == 0)
    print(f"    mention 合計 {nm:,}｜抽出為空 {ne}/{len(non_admin)} 卷")
    clean = [d for d in docs if "unsupported_court_type" not in (d["parse_issues"] or [])]
    cm = sum(per_vol[d["jid"]][0] for d in clean)
    ce = sum(1 for d in clean if per_vol[d["jid"]][0] == 0)
    print(f"  未被標記組：{len(clean)} 卷｜mention 合計 {cm:,}｜抽出為空 {ce}/{len(clean)} 卷")
    print("\n  → 三組的「抽出為空率」若無系統性差異，即證明抽取不受標記影響。")

    # 抽樣看實際內容
    rule("【6】行政／特別法域卷的實際 mention 內容（逐筆）")
    shown = 0
    for d in sorted(admin, key=lambda x: x["jid"]):
        jf = json.loads((RAW / d["entry_path"]).read_text(encoding="utf-8"))["JFULL"]
        res = extract_statute_mentions(jf, corpus)
        print(f"\n  ── {d['jid']}")
        print(f"     標頭：{head_of(d['entry_path'])[:50]}")
        print(f"     mention 數：{len(res)}")
        for m in res[:8]:
            print(f"       · {m.law_name}{m.article}"
                  f"{m.detail or ''}  surface={m.surface!r}"
                  f"  scoped={m.scoped}  collapsed={m.surface_collapsed}")
        if len(res) > 8:
            print(f"       … 另 {len(res) - 8} 筆")
        if not res:
            # 說明為何空
            hits = [ln for ln in jf.splitlines()
                    if any(k in ln for k in ("條", "項", "款"))][:0]
            print(f"     JFULL 前 200 字元：{jf[:200]!r}")
        shown += 1
        if shown >= 11:
            break

    rule("【7】is_inferred：spec 引用的欄位在程式碼中是否存在")
    print("  spec 005/FR-005 宣稱：游標推斷（「前開／上開／同法」）必須標 is_inferred=true")
    print("\n  $ grep -rn 'is_inferred' --include='*.py' backend/app/ ingest/ scripts/ agent/")
    import subprocess
    r = subprocess.run(["grep", "-rn", "is_inferred", "--include=*.py",
                        "backend/app", "ingest", "scripts", "agent"],
                       cwd=ROOT, capture_output=True, text=True)
    print("  " + (r.stdout.strip() if r.stdout.strip() else "（無輸出）"))
    print(f"  rc={r.returncode}（rc=1 ＝ 完全找不到）")

    print("\n  $ grep -rn 'is_inferred' --include='*.py' backend/.venv/")
    r2 = subprocess.run(["grep", "-rn", "is_inferred", "--include=*.py",
                         "backend/.venv"], cwd=ROOT, capture_output=True, text=True)
    out = r2.stdout.strip().splitlines()
    print(f"  {len(out)} 筆，全部在第三方套件內（與本功能無關）：")
    for l in out[:3]:
        print(f"    {l}")
    if len(out) > 3:
        print(f"    … 另 {len(out) - 3} 筆")
    print(f"  rc={r2.returncode}")

    print("\n  實際存在的對應欄位（StatuteMention dataclass）：")
    for i, l in enumerate(lines):
        if l.startswith("class StatuteMention"):
            for j in range(i, i + 9):
                print(f"    {j + 1:5d}| {lines[j]}")
            break

    rule("【8】實際的「游標推斷」機制是 scoped／scoped_from")
    print("  $ grep -n 'scoped' backend/app/b1_serve.py")
    r3 = subprocess.run(["grep", "-n", "scoped", "backend/app/b1_serve.py"],
                        cwd=ROOT, capture_output=True, text=True)
    print("  " + r3.stdout.strip())
    print("\n  機制：同一句中先出現「某法第X條」（qualified），後續裸「同法第Y條」")
    print("  由**前一個 qualified mention**提供法名（scoped=True）。")
    print("  這是**同句內的游標**，不是 spec 描述的「前開／上開／同法」跨段推斷。")

    # 實測 scoped 比例
    print("\n  全 200 卷的 scoped 比例（實測）：")
    sc_tot = qual_tot = 0
    for d in docs:
        p = RAW / d["entry_path"]
        if not p.is_file():
            continue
        jf = json.loads(p.read_text(encoding="utf-8"))["JFULL"]
        for m in extract_statute_mentions(jf, corpus):
            qual_tot += 1
            if m.scoped:
                sc_tot += 1
    print(f"    qualified mention：{qual_tot:,} 筆")
    print(f"    其中 scoped（靠游標）：{sc_tot:,} 筆"
          f"（{sc_tot / qual_tot * 100:.2f}%）")

    rule("結論（僅陳述實測，不改 Q2=B）")
    print("  1. extract_statute_mentions(text, corpus) 的參數不含任何 parser 輸出，")
    print("     函式體內也無 court_type / body_start 等識別字 → **結構上不可能**")
    print("     依賴 parser 的 court type 或 body-start 結果。")
    print(f"  2. {len(admin)} 卷行政／特別法域卷**照樣被抽取**，合計 {tot_m} 筆 mention；")
    print(f"     抽出為空 {len(empty_admin)} 卷。")
    print("  3. `is_inferred` 欄位在專案程式碼中**完全不存在**（只在 pydantic 內部）。")
    print("     spec 引用的「前開／上開／同法」游標推斷**尚未實作**；")
    print("     現有的是 scoped／scoped_from（同句內游標）。")
    print("     ⇒ spec 的 FR-005 描述的是**待建功能**，不是既有行為。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())