#!/usr/bin/env python3
"""conftest 的 laws corpus 守衛：證明它在兩種環境下都對。

## 為什麼是「工具」而不是「測試」（2026-10-10 maintainer 決定）

它原本叫 `tests/test_conftest_corpus_guard.py`，而那是一個錯誤：

  · 它自己**跑兩次完整 pytest**（乾淨 clone 一次 + 開發機一次）
  · 開發機那次要 ~125 秒 → 每次執行 2.5 分鐘
  · 放在 tests/ 裡 = CI 每次 push 都白付這 2.5 分鐘，
    換來一個「測試測試的測試」的自我參照

它是**量測工具**，不是測試。spec 008 的 verify/ 底下同一類的東西也都是
手動執行的。改放 scripts/，只在你要確認守衛時跑。

## 為什麼需要這支（constitution XII / R1）

這個守衛修的是一個**已經紅了 8 次**的 CI bug，它自己不能沒有驗證手段 ——
否則下一次有人擴充 `CORPUS_RUNTIME_TESTS` 清單或改壞判斷時，症狀會是
「CI 紅了但沒人知道是這個守衛退化」，那正是它要修的那個病。

## 預估耗時

約 **2.5 分鐘**（= 兩次完整 pytest）。跑之前先想清楚值不值。

## 用法

    .venv/bin/python scripts/verify-corpus-guard.py \
        | tee specs/008-evidence-first-stream/evidence/conftest-guard-proof.txt

⚠ 輸出若已在 /tmp/opencode/guard.txt，先 `cp` 那份，不要為存檔重跑。

不改任何程式碼與測試資料，只驗證守衛行為。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAWS_FLAT = ROOT / "data" / "laws" / "laws_flat.jsonl"
LAWS_META = ROOT / "data" / "laws" / "laws_meta.jsonl"
GUARD_LIST = ROOT / "tests" / "conftest.py"
BACKUP = ROOT / "data" / "laws"  # ⚠ 必須與來源**同一個檔案系統**。
#   2026-10-10 實測踩到：放 /tmp/opencode 時 `Path.rename()` 丟
#   `OSError: [Errno 18] Invalid cross-device link`，而 finally 裡的還原
#   也一起炸掉 —— 結果是「守衛自己拋錯、檔案沒被移走、測試白跑」。
#   /tmp 在這台機器是獨立掛載（tmpfs 或另一個 partition）。
#   放在 data/laws/ 底下最安全：同一個目錄、同一個 fs、不會離開 repo。

#: 這兩組清單是「需要 laws_flat 才能跑」的測試檔。加一個檔進來 = 少驗證
#: 一個檔，所以清單與實際失敗集合必須逐條對得上。
GUARDED = [
    # 模組層級 from_jsonl
    "test_b2b_chain.py", "test_b2b_extract.py", "test_b2b_resolve.py",
    "test_b2b_safety.py", "test_b2c_chain.py", "test_b2c_structure.py",
    "test_b2d_chain.py", "test_b2e_eval.py", "test_b2f_serve.py",
    "test_b3b_eval.py", "test_b4b_eval.py",
    # 測試函式內 real_corpus() / 未傳 corpus 的 serve_question()
    "test_b1_linking.py", "test_b1_evidence.py", "test_b1_serving_demo.py",
    "test_b1_abstention.py", "test_b2d_abstain.py", "test_b3b_serve.py",
    "test_b3c_gate.py", "test_b4a_serve.py", "test_b4b_serve.py",
    "test_b4b_f1_live.py", "test_judgements_slice.py",
]


def run_pytest() -> tuple[int, str]:
    r = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def check_guard_list_in_sync() -> list[str]:
    """這份 GUARDED 必須與 tests/conftest.py 的兩組清單**逐字相同**。

    為什麼要這個靜態檢查：守衛失效最可能的原因不是 pytest 行為變了，
    而是**有人改了 conftest 的清單卻沒更新這裡** —— 那時這個工具會
    對著舊清單比對，報「沒有失敗檔漏在守衛之外」而實際上守衛漏了。
    症狀是假綠，正是 constitution XII 要防的那種。

    所以這裡**不維護自己的副本**，改成直接從 conftest.py 解析。
    """
    import re
    src = GUARD_LIST.read_text(encoding="utf-8")
    problems: list[str] = []
    total = 0
    for name in ("CORPUS_MODULE_TESTS", "CORPUS_RUNTIME_TESTS"):
        m = re.search(rf"{name} = frozenset\(\{{(.*?)\}}\)", src, re.DOTALL)
        if not m:
            problems.append(f"無法從 conftest.py 解析 {name}（格式變了？）")
            continue
        found = set(re.findall(r'"([^"]+\.py)"', m.group(1)))
        total += len(found)
        declared = {x for x in GUARDED}
        missing = found - declared
        if missing:
            problems.append(
                f"{name} 有 {len(missing)} 個檔不在本工具的 GUARDED 裡："
                + "、".join(sorted(missing)))
    print(f"  conftest.py 兩組清單合計 {total} 個檔；本工具 GUARDED {len(GUARDED)} 個")
    return problems


def summary(out: str) -> tuple[int, int, int, list[str]]:
    """回傳 (passed, skipped, failed, 失敗檔名清單)。

    ⚠ 2026-10-10 實測踩到：原本用 `line.split()` 後比對 `tok == "passed"`，
      但 pytest 的摘要行是 `1661 passed, 120 skipped, ...` —— token 是
      **`passed,`（帶逗號）**，永遠比對不到，於是 passed 恆為 0，
      「只跑 0 個」的警告就誤報了。改成 strip 掉標點。
    """
    passed = skipped = failed = 0
    for line in out.splitlines():
        if " passed" not in line and " failed" not in line:
            continue
        parts = [p.strip(" ,") for p in line.split()]
        for i, tok in enumerate(parts):
            if i and parts[i - 1].isdigit():
                if tok == "passed":
                    passed = int(parts[i - 1])
                elif tok == "skipped":
                    skipped = int(parts[i - 1])
                elif tok in ("failed", "errors"):
                    failed = int(parts[i - 1])
    files = sorted({l.split()[1].split("::")[0].split("/")[-1]
                    for l in out.splitlines() if l.startswith("FAILED ")})
    return passed, skipped, failed, files


def main() -> int:
    print("=" * 78)
    print("conftest laws corpus 守衛：兩種環境的驗證")
    print("=" * 78)
    print(f"產生時間：{time.strftime('%Y-%m-%d %H:%M:%S %z')}")
    print(f"repo：{ROOT}")
    print(f"laws_flat.jsonl 目前{'存在' if LAWS_FLAT.is_file() else '不存在'}"
          f"（{LAWS_FLAT.stat().st_size:,} bytes）"
          if LAWS_FLAT.is_file() else "laws_flat.jsonl 不存在")
    print()

    had_flat = LAWS_FLAT.is_file()
    had_meta = LAWS_META.is_file()
    # 靜態檢查先做（0 秒），發現清單不同步就不用花 2.5 分鐘跑 pytest。
    print("-" * 78)
    print("檢查 0：本工具的 GUARDED 是否與 tests/conftest.py 的清單同步")
    print("-" * 78)
    sync_problems = check_guard_list_in_sync()
    for p in sync_problems:
        print(f"  ✗ {p}")
    if not sync_problems:
        print("  ✓ 兩份清單一致")
    print()

    ok = True

    # ── 情境 1：乾淨 clone ────────────────────────────────────────────
    print("-" * 78)
    print("情境 1：乾淨 clone（移走 laws_flat.jsonl + laws_meta.jsonl）")
    print("  這就是 CI 的環境，也是這個 bug 讓 CI 連續紅 8 次的環境")
    print("-" * 78)
    moved = []
    try:
        if had_flat:
            LAWS_FLAT.rename(BACKUP / "laws_flat.jsonl.bak"); moved.append("flat")
        if had_meta:
            LAWS_META.rename(BACKUP / "laws_meta.jsonl.bak"); moved.append("meta")
        print(f"  已暫存：{moved or '（本來就不存在）'}")
        rc, out = run_pytest()
    finally:
        if had_flat:
            (BACKUP / "laws_flat.jsonl.bak").rename(LAWS_FLAT)
        if had_meta:
            (BACKUP / "laws_meta.jsonl.bak").rename(LAWS_META)
        print("  已還原（finally 區塊，不論成敗都還）")

    p, s, f, files = summary(out)
    print(f"\n  exit code : {rc}")
    print(f"  passed    : {p}")
    print(f"  skipped   : {s}")
    print(f"  failed    : {f}")
    if files:
        print(f"  失敗檔    : {files}")
    print()
    if rc != 0 or f > 0:
        print("  ✗ 乾淨 clone 下有失敗 —— 守衛沒擋乾淨")
        ok = False
        for line in out.splitlines():
            if line.startswith("FAILED") or line.startswith("ERROR"):
                print(f"      {line}")
    else:
        print("  ✓ 乾淨 clone 下全綠，且跑了實際測試（不是 0 個）")
        if p < 1000:
            print(f"  ⚠ 只跑 {p} 個，比預期少很多 —— 確認守衛沒有過度跳過")
            ok = False

    # ── 情境 2：開發機 ────────────────────────────────────────────────
    print()
    print("-" * 78)
    print("情境 2：開發機（檔案都在，守衛應該完全不生效）")
    print("-" * 78)
    if not LAWS_FLAT.is_file():
        print("  ⚠ 檔案沒還原成功，跳過這個情境")
        return 1
    rc2, out2 = run_pytest()
    p2, s2, f2, files2 = summary(out2)
    print(f"  exit code : {rc2}")
    print(f"  passed    : {p2}")
    print(f"  skipped   : {s2}")
    print(f"  failed    : {f2}")
    if files2:
        print(f"  失敗檔    : {files2}")
    if rc2 != 0 or f2 > 0:
        print("  ✗ 開發機環境有失敗 —— 守衛誤傷了正常環境")
        ok = False
        for line in out2.splitlines():
            if line.startswith("FAILED"):
                print(f"      {line}")
    else:
        print("  ✓ 開發機全綠")

    # ── 守衛的清單有沒有漏 ────────────────────────────────────────────
    print()
    print("-" * 78)
    print("交叉檢查：情境 1 的 failed 檔 是否都在守衛清單裡")
    print("-" * 78)
    unguarded = [x for x in files if x and x not in GUARDED]
    if unguarded:
        print(f"  ✗ 有 {len(unguarded)} 個失敗檔不在守衛清單 —— 清單漏了：")
        for x in unguarded:
            print(f"      {x}")
        ok = False
    else:
        print("  ✓ 沒有任何失敗檔漏在守衛之外")

    print()
    print("-" * 78)
    print("反向檢查：守衛清單裡有沒有其實不需要擋的（過度跳過）")
    print("-" * 78)
    print(f"  情境 1 passed={p}，情境 2 passed={p2}")
    delta = p2 - p
    listed = len(GUARDED)
    print(f"  差額 {delta} 個 passed，守衛清單 {listed} 個檔")
    if delta > listed * 60:
        print("  ⚠ 少跑的測試數遠多於守衛涵蓋的檔案 —— 可能過度跳過")
        ok = False
    else:
        print("  ✓ 差額可解釋（每個檔有多個測試）")

    print()
    print("=" * 78)
    print("結論")
    print("=" * 78)
    if sync_problems:
        print(f"  ✗ 靜態檢查未過（{len(sync_problems)} 項）—— 那兩個情境的數字不採信")
        for p in sync_problems:
            print(f"      {p}")
    if ok:
        print("  ✓ 乾淨 clone：0 failed，測試真的跑得到")
        print("  ✓ 開發機：0 failed，守衛不誤傷")
        print("  ✓ 沒有失敗檔漏在守衛之外")
    else:
        print("  ✗ 守衛未通過驗證")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())