#!/usr/bin/env python3
"""逐字驗證 M1 審閱包裡的 `JFULL`（spec 006 T110 驗收，憲法 XI）。

## 為什麼需要這一支

審閱包在 `data/judgements/profile-m1/`，是 **gitignore 的**——所以「重跑之後
還對不對」沒有任何測試會自動提醒。這支把逐字性變成可重複執行的指令。

## 為什麼要全 100 卷

2026-10-10 實測教訓：原本想「抽 1 卷驗一驗」，但那次真正的 bug（universal
newlines 把 CRLF 讀成 LF）是**系統性**的，抽樣會讓它溜過去。逐字性這種東西
**只有全量驗有意義**——抽 1 卷通過並不證明其餘 99 卷通過。

## 讀回那一端同樣要小心

`Path.read_text()` 在 `newline=None` 時會做 universal newlines，把 CRLF 讀成
LF，於是「檔案沒問題」會被誤判成「逐字性壞掉」。2026-10-10 的第一版驗證就是
栽在這裡——它報 100 卷全部不符，而真正的問題在**驗證程式自己**。
所以這裡用 `newline=""` 讀取，並在報告裡附上每卷的 CRLF 數供交叉核對。

用法：
    .venv/bin/python scripts/verify-m1-review-bundle.py [--profile-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import profile_build as PB  # noqa: E402


def _section_for(bundle: str, jid: str) -> str:
    """用 **JID 定位**章節。

    不用「第 N 個 JFULL 區段」：包內依 size 降冪排序，與 manifest 的 `entries`
    順序無關。照順序比對會拿 A 卷的原文去比 B 卷的區段，報出**假的**不符。
    """
    heading = f"\n## {jid}\n"
    try:
        start = bundle.index(heading)
    except ValueError as exc:
        raise KeyError(f"審閱包沒有 {jid} 的章節") from exc
    rest = bundle[start + len(heading) :]
    end = rest.find("\n## ")
    return rest[:end] if end > 0 else rest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="verify-m1-review-bundle",
        description="逐位元組比對審閱包裡每卷的 JFULL 與原始 JSON",
    )
    ap.add_argument(
        "--profile-dir",
        type=Path,
        default=PB.PROFILE_DIR,
        help="profile 目錄（預設 data/judgements/profile-m1）",
    )
    ap.add_argument(
        "--manifest",
        type=Path,
        default=ROOT
        / "specs"
        / "006-m1-judicial-selection-profile"
        / "profile-manifest.json",
        help="manifest 路徑（提供要驗的卷清單與 path_posix）",
    )
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if not args.manifest.is_file():
        print(f"✗ manifest 不存在：{args.manifest}", file=sys.stderr)
        return 2
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

    bundle_path = args.profile_dir / "review-bundle.md"
    if not bundle_path.is_file():
        print(f"✗ 審閱包不存在：{bundle_path}", file=sys.stderr)
        return 2
    # ⚠ newline="" —— 讀回也會破壞逐字性，理由見模組 docstring。
    with bundle_path.open(encoding="utf-8", newline="") as fh:
        bundle = fh.read()

    checked = 0
    mismatched: list[tuple[str, int, int]] = []
    total_crlf = 0
    for entry in manifest["entries"]:
        raw_path = args.profile_dir / "raw" / entry["path_posix"]
        if not raw_path.is_file():
            print(f"✗ 原始檔不存在：{raw_path}", file=sys.stderr)
            return 2
        original = json.loads(raw_path.read_text(encoding="utf-8"))["JFULL"]
        section = _section_for(bundle, entry["jid"])
        extracted = PB.extract_jfull_section(section, occurrence=0)
        checked += 1
        total_crlf += original.count("\r\n")
        if extracted != original:
            mismatched.append((entry["jid"], len(extracted), len(original)))

    print(f"[比對] 卷數 {checked}；原文 CRLF 合計 {total_crlf} 處")
    print(f"[標記] BEGIN {bundle.count(PB.JFULL_BEGIN)} / END {bundle.count(PB.JFULL_END)}")
    if mismatched:
        print(f"✗ 逐字不符 {len(mismatched)} 卷：", file=sys.stderr)
        for jid, got, want in mismatched[:5]:
            print(f"  - {jid}：抽出 {got} vs 原文 {want}", file=sys.stderr)
        return 1
    print("✓ 全部逐位元組相符（JFULL 逐字性成立）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
