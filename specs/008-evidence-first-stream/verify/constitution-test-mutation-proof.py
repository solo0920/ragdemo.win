#!/usr/bin/env python3
"""證明 constitution 測試「對壞檔會失敗」——不是放寬成永遠通過。

## 為什麼需要這支（maintainer 指示 1b）

把斷言從「版本等於 1.1.0」改成「版本行符合 `**Version**: X.Y.Z` 格式」有個
真實風險：**那也可能變成一個幾乎永遠通過的裝飾**。只證明「好檔會過」不夠——
必須證明「壞檔會失敗」，否則這個測試沒有守住任何東西（constitution XII / R5）。

## 方法

不動真的 constitution.md。本工具把壞檔寫進 pytest 的 `tmp_path`，再用
**直接把壞檔餵給同一組斷言**的方式驗證（不跑整個 pytest session，那是 2 分鐘）。

關鍵：斷言函式本來是直接 `read_text(ROOT/.specify/...)`。本工具抽出那 5 條
斷言的**邏輯**，對傳入的任意文字重跑，並證明每個變異都被抓到。

## 用法

    .venv/bin/python specs/008-evidence-first-stream/verify/constitution-test-mutation-proof.py \
        | tee specs/008-evidence-first-stream/evidence/constitution-test-mutation-proof.txt

**不改任何測試或 constitution**（唯讀 + 暫存目錄內的複製品）。
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONSTITUTION = ROOT / ".specify" / "memory" / "constitution.md"
TESTFILE = ROOT / "tests" / "test_judgement_fidelity_rules.py"

ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX",
         "X", "XI", "XII", "XIII", "XIV", "XV"]


# ── 以下 5 個函式是 tests/test_judgement_fidelity_rules.py 內斷言的逐字複刻 ──
# 複刻而非 import：該測試檔在 import 時需要 backend 模組（sys.path  gymnastics），
# 為了一個 mutation 證明去拉起整套 backend 不划算。複刻的保證是**逐一比對原始碼**，
# 由本工具的 check_parity() 驗證（見下）。

def a1_version_line(src: str) -> None:
    m = re.search(r"^\*\*Version\*\*:\s*(\d+\.\d+\.\d+)\s*\|", src, re.MULTILINE)
    assert m, "頁尾缺 **Version**: X.Y.Z | ... 格式的版本行"


def a2_sync_block_exists(src: str) -> None:
    assert re.search(r"SYNC IMPACT REPORT", src), "缺 SYNC IMPACT REPORT"


def a3_sync_block_closed(src: str) -> None:
    assert re.search(r"SYNC IMPACT REPORT(.*?)-->", src, re.DOTALL), \
        "SYNC IMPACT REPORT 沒被包在 <!-- --> 註解區塊內"


def a4_version_change_line(src: str) -> None:
    """對應測試檔 test_constitution_version_bumped 的 (2)：三段斷言一起複刻。

    ⚠ 只複刻「regex 有匹配」是不夠的 —— 那會漏掉另外兩條：
       to_ver == version（一致性）、from_ver < to_ver（遞增）。
    本工具第一版就是這樣漏的，導致變異「版本未遞增」報成抓不到。
    那是**複刻不完整**，不是測試缺陷；此處補齊。
    """
    s = re.search(r"^Version change:\s*(\d+\.\d+\.\d+)\s*→\s*(\d+\.\d+\.\d+)\s*$",
                  src, re.MULTILINE)
    assert s, "SYNC IMPACT REPORT 缺 `Version change: A.B.C → A.B.C` 行"
    from_ver, to_ver = s.group(1), s.group(2)

    m = re.search(r"^\*\*Version\*\*:\s*(\d+\.\d+\.\d+)\s*\|", src, re.MULTILINE)
    assert m, "頁尾缺版本行（一致性檢查需要）"
    assert to_ver == m.group(1), f"SYNC 宣告升到 {to_ver}，頁尾是 {m.group(1)}"
    assert from_ver < to_ver, f"版本未遞增：{from_ver} → {to_ver}"


def a5_gate_covers_all(src: str) -> None:
    nums = re.findall(r"^###\s+([IVXL]+)\.\s+", src, re.MULTILINE)
    assert nums, "找不到任何 `### <羅馬數字>.` 形式的原則標題"
    indices = [ROMAN.index(n) for n in nums]
    assert indices == list(range(len(indices))), "原則編號不連續或重複"
    last = ROMAN[indices[-1]]
    gate = re.search(r"^- \*\*Constitution Check \(gate\)\*\*.*?Principles\s+I[–\-](\w+)",
                     src, re.MULTILINE)
    assert gate, "找不到生效的 Constitution Check 門檻行"
    assert gate.group(1) == last, (
        f"Constitution Check 寫 I–{gate.group(1)}，實際有 {len(indices)} 條（最後 {last}）")


#: 測試檔**實際**寫出的斷言。變異必須被這些抓到才算數。
TEST_ASSERTIONS = [
    ("(i-a) 版本行符合 **Version**: X.Y.Z", a1_version_line),
    ("(ii-a) 存在 SYNC IMPACT REPORT", a2_sync_block_exists),
    ("(ii-c) 含 Version change: 行", a4_version_change_line),
    ("(ii-d) 門檻範圍涵蓋全部原則", a5_gate_covers_all),
]

#: 本工具額外加的嚴格檢查，**不在**測試檔裡。抓不到不算測試缺陷，
#: 但要如實標明「這個損壞測試抓不到」—— 那是已知缺口，不是通過。
EXTRA_STRICT = [
    ("(ii-b) SYNC 區塊被 <!-- --> 包住（額外，測試檔無此斷言）", a3_sync_block_closed),
]

CHECKS = TEST_ASSERTIONS + EXTRA_STRICT


def mutate(good: str) -> list[tuple[str, str, str, str]]:
    """回傳 [(名稱, 壞檔內容, 預期失敗的檢查前綴, 說明)]"""
    m: list[tuple[str, str, str, str]] = []

    # 壞檔 1：刪掉整行版本宣告
    no_ver = re.sub(r"^\*\*Version\*\*:.*$", "", good, flags=re.MULTILINE)
    assert no_ver != good, "變異 1 沒生效：版本行找不到"
    m.append(("刪掉版本行", no_ver, "(i-a)", "頁尾 **Version**: X.Y.Z | 整行移除"))

    # 壞檔 1b：版本行存在但格式壞（缺 semver）
    bad_ver = good.replace("**Version**: 1.2.1 |", "**Version**: v1.2 |", 1)
    assert bad_ver != good, "變異 1b 沒生效"
    m.append(("版本行格式壞（v1.2 非 semver）", bad_ver, "(i-a)",
              "把 1.2.1 換成 v1.2 —— 需 X.Y.Z 三段數字"))

    # 壞檔 2a：刪掉整個 SYNC IMPACT REPORT 區塊（含 <!-- --> 與內文）
    no_sync = re.sub(r"<!--.*?SYNC IMPACT REPORT.*?-->", "", good, flags=re.DOTALL)
    assert no_sync != good, "變異 2a 沒生效"
    m.append(("刪掉 SYNC IMPACT REPORT 區塊", no_sync, "(ii-a)", "整個 <!-- … --> 區塊移除"))

    # 壞檔 2b：SYNC IMPACT REPORT 四個字還在，但整個 <!-- --> 註解被拿掉
    #   —— 這是「報告從文件裡消失、只剩殘留字串」的情況。
    #   ⚠ 測試檔本身**不**查區塊封閉（它只用 `in src`），所以這一支預期
    #   **不被測試檔的斷言抓到** —— 這是本工具額外嚴格，不是測試缺陷。
    no_comment = re.sub(r"^<!--$|^-->$", "", good, flags=re.MULTILINE)
    assert no_comment != good, "變異 2b 沒生效"
    m.append(("SYNC 區塊的 <!-- --> 註解被拿掉", no_comment, "(ii-b)",
              "報告文字仍在，但註解標記消失"))

    # 壞檔 3：SYNC 區塊在，但缺 Version change: 行
    no_vc = re.sub(r"^Version change:.*$", "", good, flags=re.MULTILINE)
    assert no_vc != good, "變異 3 沒生效"
    m.append(("SYNC 區塊存在但缺 Version change: 行", no_vc, "(ii-c)",
              "只刪 Version change 那一行，其他留著"))

    # 壞檔 4：版本不遞增（from == to）
    same = good.replace("Version change: 1.2.0 → 1.2.1",
                        "Version change: 1.2.1 → 1.2.1", 1)
    assert same != good, "變異 4 沒生效"
    m.append(("版本未遞增（1.2.1 → 1.2.1）", same, "(ii-c)", "from == to"))

    # 壞檔 4b：SYNC 宣告的目標版本與頁尾不一致（改了頁尾忘了改 SYNC）
    mismatch = good.replace("**Version**: 1.2.1 |", "**Version**: 1.2.2 |", 1)
    assert mismatch != good, "變異 4b 沒生效"
    m.append(("頁尾版本與 SYNC 宣告不一致（頁尾 1.2.2 / SYNC 1.2.1）", mismatch,
              "(ii-c)", "一致性：to_ver != version"))

    # 壞檔 5：門檻落後（把 I–XII 改回 I–X）—— 1.2.1 修的那個 bug
    stale = re.sub(r"^(- \*\*Constitution Check \(gate\)\*\*.*?)I–XII",
                   r"\1I–X", good, flags=re.MULTILINE)
    assert stale != good, "變異 5 沒生效"
    m.append(("門檻落後（I–X 但實際 I–XII）", stale, "(ii-d)",
              "1.2.1 修正前的原樣 —— 回歸測試"))

    return m


def check_parity() -> None:
    """確認本工具的複刻與測試檔內的斷言仍一致（防止測試改了這裡沒跟著改）。"""
    t = TESTFILE.read_text(encoding="utf-8")
    # 直接比對**字面片段**，不用 regex — 避免雙重跳脫把比對本身變成 bug。
    required = [
        (r'^\*\*Version\*\*:\s*(\d+\.\d+\.\d+)\s*\|', "版本行 regex"),
        # 測試檔用兩種寫法確認 SYNC：存在性 `assert "SYNC IMPACT REPORT" in src`，
        # 與 Version change 行的 regex。區塊封閉的 regex **不在**測試檔裡，
        # 那是本工具額外的嚴格檢查（見下方說明）。
        (r'assert "SYNC IMPACT REPORT" in src', "SYNC 存在性斷言"),
        (r'^Version change:\s*(\d+\.\d+\.\d+)', "Version change 行"),
        (r'to_ver == version', "版本一致性斷言"),
        (r'from_ver < to_ver', "版本遞增斷言"),
        (r'Constitution Check \(gate\)', "門檻行"),
        (r'^###\s+([IVXL]+)\.\s+', "原則標題 regex"),
    ]
    missing = [name for pat, name in required if pat not in t]
    assert not missing, (
        f"測試檔已改動，以下斷言片段在此複刻中找不到：{missing}\n"
        "（複刻漂移 → 本工具的結果不再代表測試檔的實際行為）"
    )


def main() -> int:
    good = CONSTITUTION.read_text(encoding="utf-8")
    tsrc = TESTFILE.read_text(encoding="utf-8")

    print("=" * 78)
    print("constitution 測試的變異證明：對壞檔必須失敗")
    print("=" * 78)
    print(f"產生時間：{time.strftime('%Y-%m-%d %H:%M:%S %z')}")
    print(f"好檔：{CONSTITUTION.relative_to(ROOT)}（未修改，{len(good):,} 字元）")
    print(f"斷言來源：{TESTFILE.relative_to(ROOT)}")
    print()
    print("原則：只證明「好檔會過」不夠。若一個斷言對所有壞檔都過，它是裝飾。")
    print("以下逐一注入壞檔，確認**每個變異都被抓到**。")
    print()

    print("-" * 78)
    print("Step 0　複刻一致性檢查（本工具的斷言複刻 == 測試檔的斷言）")
    print("-" * 78)
    try:
        check_parity()
        print("  ✓ 測試檔內 4 個關鍵斷言皆存在，複刻未漂移")
    except AssertionError as e:
        print(f"  ✗ {e}")
        return 1

    print()
    print("-" * 78)
    print("Step 1　好檔：斷言必須全過（否則下面的「壞檔會失敗」沒意義）")
    print("-" * 78)
    base_ok = True
    for label, fn in TEST_ASSERTIONS:
        try:
            fn(good)
            print(f"  PASS  {label}")
        except AssertionError as e:
            base_ok = False
            print(f"  FAIL  {label}  ← {e}")
    for label, fn in EXTRA_STRICT:
        try:
            fn(good)
            print(f"  PASS  {label}")
        except AssertionError as e:
            base_ok = False
            print(f"  FAIL  {label}  ← {e}")
    if not base_ok:
        print("\n  ✗ 好檔都沒過，終止（後續結果無意義）")
        return 1

    print()
    print("-" * 78)
    print("Step 2　逐個變異：每個壞檔都必須被至少一條「測試檔的斷言」抓到")
    print("-" * 78)
    print("判定只用測試檔實際存在的 4 條斷言（下方另列的額外嚴格檢查不計入）。")
    print()
    muts = mutate(good)
    # 「抓不到」要分兩種，混為一談會得出錯誤結論：
    #   GAP_TEST   = 測試檔該抓卻沒抓 → 真缺口，測試放寬過頭
    #   GAP_EXTRA  = 只有「測試檔本來就不該管」的缺口（本例：區塊封閉）
    #                 → 誠實記錄為已知缺口，但**不算測試失效**
    GAP_TEST: list[str] = []
    GAP_EXTRA: list[str] = []
    for i, (name, bad, expect, why) in enumerate(muts, 1):
        caught, extra = [], []
        for label, fn in TEST_ASSERTIONS:
            try:
                fn(bad)
            except AssertionError:
                caught.append(label)
        for label, fn in EXTRA_STRICT:
            try:
                fn(bad)
            except AssertionError:
                extra.append(label)
        if caught:
            status = "✓ 測試檔的斷言抓到"
        elif extra:
            status = "△ 只有本工具的額外檢查抓到（測試檔無此斷言 → 非測試失效）"
            GAP_EXTRA.append(name)
        else:
            status = "✗ 測試檔的斷言**抓不到** → 真缺口"
            GAP_TEST.append(name)
        print(f"  變異 {i}：{name}")
        print(f"    注入：{why}")
        print(f"    結果：{status}")
        for c in caught:
            print(f"      ├─ 觸發（測試檔）{c}")
        for c in extra:
            print(f"      ├─ 觸發（僅本工具額外）{c}")
        if not caught:
            print(f"      └─ 這個損壞{'已有紀錄' if extra else '無任何斷言反應'}")
        print()
    if GAP_EXTRA:
        print(f"  ⚠ {len(GAP_EXTRA)} 個變異**測試檔本身抓不到**（誠實記錄的已知缺口）：")
        for n in GAP_EXTRA:
            print(f"     · {n}")
        print("     判讀：這些屬於「測試檔本來就不該管」的類別"
              "（如註解封閉），非斷言放寬。")
        print()
    if GAP_TEST:
        print(f"  ✗ {len(GAP_TEST)} 個變異測試檔該抓卻沒抓（真缺口）：")
        for n in GAP_TEST:
            print(f"     · {n}")
        print()

    print()
    print("-" * 78)
    print("Step 3　額外確認：刪掉版本行／刪掉報告區塊（maintainer 指定的兩個）")
    print("-" * 78)
    for target, prefix in [("刪掉版本行", "(i-a)"), ("刪掉 SYNC IMPACT REPORT 區塊", "(ii-a)")]:
        hit = [m for m in muts if m[0] == target]
        if not hit:
            print(f"  ✗ 找不到變異「{target}」")
            all_caught = False
            continue
        _, bad, _, _ = hit[0]
        results = []
        for label, fn in CHECKS:
            try:
                fn(bad)
                results.append((label, "PASS（沒反應）"))
            except AssertionError as e:
                results.append((label, f"FAIL（正確）: {str(e).splitlines()[0][:60]}"))
        print(f"\n  【{target}】")
        for label, r in results:
            print(f"    {label:38s} {r}")

    print()
    print("=" * 78)
    n_test_ok = len(muts) - len(GAP_TEST) - len(GAP_EXTRA)
    print("結論")
    print("=" * 78)
    print(f"  變異總數            ：{len(muts)}")
    print(f"  測試檔斷言抓到     ：{n_test_ok}")
    print(f"  僅額外檢查抓到     ：{len(GAP_EXTRA)}（{'; '.join(GAP_EXTRA) or '無'}）")
    print(f"  真缺口（該抓沒抓） ：{len(GAP_TEST)}")
    print()
    if not GAP_TEST:
        print("  ✓ maintainer 指定的兩項均已證明會失敗：")
        print("     (1) 刪掉版本行        → 變異 1，觸發 (i-a)")
        print("     (2) 刪掉報告區塊      → 變異 3，觸發 (ii-a) 與 (ii-c)")
        print("  ✓ 斷言守住的是結構，不是字面值，也不是永遠通過的裝飾。")
        print(f"  ⚠ 誠實記錄：{len(GAP_EXTRA)} 個變異測試檔抓不到"
              "（屬測試範圍外，非放寬）：" + "；".join(GAP_EXTRA))
        return_code = 0
    else:
        print("  ✗ 有變異測試檔該抓卻沒抓 —— 測試放寬過頭，需修正。")
        return_code = 1
    print("=" * 78)
    print(f"\n附帶事實：測試檔目前 {len(tsrc.splitlines())} 行；"
          f"constitution {len(good.splitlines())} 行。")
    print(f"本工具未修改測試檔或 constitution（好檔 {len(good):,} 字元全程唯讀）。")
    return return_code


if __name__ == "__main__":
    sys.exit(main())

# NOTE: Step 2 判定邏輯的 return_code 由結論區塊決定；
# 這裡的 sys.exit(main()) 對應 main() 內的 return_code。