#!/usr/bin/env python3
"""量測「判決＋法條 evidence」功能的**改動前基線**（constitution XII / R1、R2）。

## 為什麼需要這支

feature 描述裡列了三個「已知問題」數字，並註明「不可直接採信，須依 R1 重測」。
本工具就是那個重測。它**只量測，不修改任何東西**。

## 三項約束（constitution XII）

1. 每個數字都有可重跑指令：`python specs/008-evidence-first-stream/verify/measure-baseline.py`
2. 原始輸出存 `specs/008-evidence-first-stream/evidence/`（本檔 stdout 由呼叫端導向該處）
3. 每個數字標明**分母＝母體是什麼、多少**

## 母體定義（避免「25 ms/卷」這種無分母的數字）

| 量測項 | 母體 | 分母來源 |
|---|---|---|
| `extract_statute_mentions` 耗時 | M1 評估 manifest 的判決卷 | `specs/007-…/eval-manifest-200.json` 的 `documents` |
| `judgement_store.jfull_map` 載入 | 目前實際存在於 `data/judgements/seed/` 的檔案 | 目錄 glob 實測 |
| 容器對 `laws_flat.jsonl` 的依賴 | 該檔的 gitignore 狀態與 git 追蹤狀態 | `git check-ignore` + `git ls-files` |

## 用法

    # 產生證據檔（呼叫端負責 tee）
    .venv/bin/python specs/008-evidence-first-stream/verify/measure-baseline.py \
        | tee specs/008-evidence-first-stream/evidence/baseline-$(date +%Y%m%d-%H%M%S).txt
"""
from __future__ import annotations

import json
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# `judgement_store` 在 backend/app/ 之下（**不是** backend/ 之下，2026-10-10 實測）。
# 首次量測時只加了 backend/ 而報 ModuleNotFoundError —— 記在這裡避免重蹈。
sys.path.insert(0, str(ROOT / "backend" / "app"))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "specs" / "007-judgment-three-tier-storage" / "eval-manifest-200.json"
RAW = ROOT / "data" / "judgements" / "profile-200" / "raw"
SEED = ROOT / "data" / "judgements" / "seed"
LAWS_FLAT = ROOT / "data" / "laws" / "laws_flat.jsonl"


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def main() -> int:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %z")
    print("=" * 72)
    print(f"基線量測（改動前）  {stamp}")
    print("=" * 72)

    # ── ① jfull_map：查詢路徑上的載入成本 ────────────────────────────
    print("\n【1】judgement_store.jfull_map() — 查詢路徑上的記憶體與時間")
    seed_files = sorted(SEED.glob("*.json")) if SEED.is_dir() else []
    print(f"  母體：{SEED.relative_to(ROOT)} 下的 seed 檔案")
    print(f"  分母：{len(seed_files)} 個檔案")
    print(f"  RSS（量測前）：{rss_mb():.0f} MB")

    import judgement_store  # noqa: E402

    t0 = time.perf_counter()
    jfull = judgement_store.jfull_map()
    t1 = time.perf_counter()
    print(f"  首次 jfull_map() 耗時：{(t1 - t0) * 1000:.0f} ms")
    print(f"  RSS（量測後）：{rss_mb():.0f} MB")
    total_chars = sum(len(v) for v in jfull.values())
    print(f"  回傳 entry 數：{len(jfull)}｜JFULL 總字元：{total_chars:,}")
    print(f"  ⚠ 這是**單一份 seed**的量測，不是全庫。若 seed 換成 200 卷，成本會變。")

    # 第二次（快取）
    t0 = time.perf_counter()
    judgement_store.jfull_map()
    t1 = time.perf_counter()
    print(f"  第二次（TTL 快取）耗時：{(t1 - t0) * 1000:.3f} ms")

    # ── ② extract_statute_mentions：法名 regex 的成本 ────────────────
    print("\n【2】extract_statute_mentions() — 法名 regex 抽取成本")
    from app.b1_serve import StatuteCorpus, extract_statute_mentions  # noqa: E402

    corpus_path = LAWS_FLAT
    print(f"  法名來源：{corpus_path.relative_to(ROOT)}")
    if not corpus_path.is_file():
        print(f"  ✗ 檔案不存在：{corpus_path}")
        print("    → 這本身就是一個發現：容器環境取不到它（見【3】）")
        return 1

    t0 = time.perf_counter()
    corpus = StatuteCorpus.from_jsonl(corpus_path)
    t1 = time.perf_counter()
    print(f"  StatuteCorpus.from_jsonl() 載入耗時：{(t1 - t0):.2f} s")
    print(f"  母體：{corpus_path} 的全部條文")
    print(f"  分母：{len(corpus.rows):,} 條文／{len(corpus.law_names):,} 部法規")
    print(f"  RSS（載入 corpus 後）：{rss_mb():.0f} MB")

    if not MANIFEST.is_file():
        print(f"  ✗ 找不到評估 manifest：{MANIFEST}")
        return 1
    docs = json.loads(MANIFEST.read_text(encoding="utf-8"))["documents"]
    print(f"  量測母體：評估 manifest 的 {len(docs)} 卷判決")

    times_ms: list[float] = []
    empty = 0
    chars_total = 0
    for d in docs:
        p = RAW / d["entry_path"]
        if not p.is_file():
            continue
        jf = json.loads(p.read_text(encoding="utf-8"))["JFULL"]
        chars_total += len(jf)
        t0 = time.perf_counter()
        res = extract_statute_mentions(jf, corpus)
        times_ms.append((time.perf_counter() - t0) * 1000)
        if not res:
            empty += 1

    if not times_ms:
        print("  ✗ 沒有任何卷可量測")
        return 1
    times_ms.sort()

    def pct(p: float) -> float:
        # 近��中位數分位，避免插值假精確
        idx = min(len(times_ms) - 1, int(round((p / 100) * (len(times_ms) - 1))))
        return times_ms[idx]

    print(f"  實測卷數（分母）：{len(times_ms)}")
    print(f"  JFULL 總字元（分母）：{chars_total:,}")
    print(f"  耗時 min    ：{times_ms[0]:.1f} ms")
    print(f"  耗時 median ：{pct(50):.1f} ms")
    print(f"  耗時 p95    ：{pct(95):.1f} ms")
    print(f"  耗時 max    ：{times_ms[-1]:.1f} ms")
    print(f"  抽出結果為空的卷：{empty}/{len(times_ms)}")

    # ── ③ 容器對 laws_flat.jsonl 的依賴 ────────────────────────────
    print("\n【3】容器對 laws_flat.jsonl 的依賴")
    rel = str(LAWS_FLAT.relative_to(ROOT))
    r = subprocess.run(["git", "check-ignore", "-v", rel], cwd=ROOT,
                       capture_output=True, text=True)
    print(f"  git check-ignore {rel} → rc={r.returncode}")
    if r.returncode == 0:
        print(f"    規則：{r.stdout.strip()}")
        print("    ⇒ 該檔**不在版控**，git clone 後不存在")
    r2 = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=ROOT,
                        capture_output=True, text=True)
    print(f"  git ls-files --error-unmatch → rc={r2.returncode}"
          f"（{'已追蹤' if r2.returncode == 0 else '未被追蹤'}）")
    print(f"  檔案目前存在於本機：{LAWS_FLAT.is_file()}"
          f"（大小 {LAWS_FLAT.stat().st_size:,} bytes）"
          if LAWS_FLAT.is_file() else "  檔案不存在")
    print("  ⇒ 結論需由呼叫端比對 Dockerfile 的 COPY 清單才能定案；"
          "本工具只陳述 git 事實。")

    print("\n" + "=" * 72)
    print("量測結束。以上數字僅為**改動前**基線（R2）。")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())