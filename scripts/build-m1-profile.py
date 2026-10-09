#!/usr/bin/env python3
"""M1 profile builder 的命令列入口（spec 006 T112 / FR-010）。

## 為什麼有這一支

模組本身不該自己決定「跑在哪、decoder 是哪支、產物寫哪」——那些是**環境**。
這支 CLI 只做四件事：解析引數、跑前置閘門、呼叫模組、把階段計數印出來。

## 失敗語義（憲法 IX：失敗必須指得出是哪個階段）

| exit | 意義 |
|---|---|
| 0 | 跑完（**含**部分 entry 失敗——那會如實出現在計數與 manifest 裡） |
| 2 | allowlist 或 artifact 不符（**前置閘門**擋下，一個位元組都沒寫） |
| 3 | decoder 缺席或不可執行 |
| 4 | 環境缺失（例如 allowlist 檔不存在） |

刻意區分 2／3／4：三者都是「整輪沒跑」，但要修的地方完全不同。

## 不提供什麼

`--force`、`--skip-verify`、`--overwrite`。這支的覆寫語義是 FR-006 訂死的
（每次執行覆寫產物），而**繞過驗證的旗標一律不提供**——與 `host-sync.sh`、
`host-doctor.sh` 的既有哲學一致。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import profile_build as PB  # noqa: E402

EXIT_OK = 0
EXIT_INPUT_MISMATCH = 2
EXIT_NO_DECODER = 3
EXIT_ENV_MISSING = 4

#: 檢索面檔案（T114 的防護清單）。**只讀**，這支 CLI 不會寫它們。
RETRIEVAL_FILES = (
    "backend/app/rag.py",
    "backend/app/b1_serve.py",
    "backend/app/b2d_answer.py",
    "backend/app/judgement_store.py",
    "ingest/judgements/index_load.py",
    "ingest/laws/qdrant_load.py",
    "agent/scripts/eval_judgements_slice.py",
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="build-m1-profile",
        description="解壓 allowlist 上的判決、驗證、切塊，產出 profile 與 manifest",
    )
    p.add_argument(
        "--allowlist",
        type=Path,
        default=None,
        help="allowlist 路徑（預設：specs/004-…/allowlist-m1.json）",
    )
    p.add_argument(
        "--profile-dir",
        type=Path,
        default=None,
        help="profile 輸出目錄（預設：data/judgements/profile-m1）",
    )
    p.add_argument(
        "--archive",
        type=Path,
        default=None,
        help="RAR 路徑（預設：artifact.py 釘死的那個）",
    )
    p.add_argument(
        "--decoder",
        type=Path,
        default=None,
        help="unrar binary 路徑。**必須明確指定或放進 PATH**——不猜（沿用 T003）",
    )
    p.add_argument(
        "--manifest-out",
        type=Path,
        default=None,
        help="manifest 輸出路徑（預設寫進 specs/006-…/，進版控）",
    )
    p.add_argument(
        "--external-snapshot",
        type=Path,
        default=None,
        help="repo 外的 T006 凍結快照；不給就記 present:false（不中斷）",
    )
    p.add_argument(
        "--limit", type=int, default=None, help="只跑前 N 筆（診斷用，不是補位）"
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    # ── 階段 0：環境 ────────────────────────────────────────────────────────
    if args.allowlist is not None and not args.allowlist.is_file():
        print(f"✗ allowlist 不存在：{args.allowlist}", file=sys.stderr)
        return EXIT_ENV_MISSING
    try:
        al = PB.load_allowlist(args.allowlist)
    except PB.AllowlistError as exc:
        print(f"✗ allowlist 讀不到：{exc}", file=sys.stderr)
        return EXIT_ENV_MISSING

    # ── 階段 1：離線層自檢（FR-010a）────────────────────────────────────────
    problems = PB.verify_allowlist(al)
    if problems:
        print("✗ 離線層自檢失敗（清單本身有問題）：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return EXIT_INPUT_MISMATCH

    # ── 階段 2：對庫層自檢（FR-010b）—— 需要真 artifact ─────────────────────
    archive_problems = PB.verify_selection_against_archive(al, archive=args.archive)
    if archive_problems:
        print("✗ 對庫層自檢失敗（清單與 archive 對不上）：", file=sys.stderr)
        for p in archive_problems:
            print(f"  - {p}", file=sys.stderr)
        return EXIT_INPUT_MISMATCH
    print(f"[閘門] 離線層 PASS；對庫層 PASS（{len(al)} 筆）")

    # ── 階段 3：decoder ─────────────────────────────────────────────────────
    if args.decoder is None:
        print(
            "✗ 沒有 --decoder。這批 entry 的 method 不是 store，需要 binary。\n"
            "  沿用 T003 的原則：不替你猜 PATH 裡有什麼。",
            file=sys.stderr,
        )
        return EXIT_NO_DECODER
    if not args.decoder.is_file():
        print(f"✗ decoder 不存在：{args.decoder}", file=sys.stderr)
        return EXIT_NO_DECODER

    # ── 階段 4：跑 ─────────────────────────────────────────────────────────
    profile_dir = args.profile_dir or PB.PROFILE_DIR
    print(f"[解壓] → {profile_dir}")
    results = PB.process_all(
        al,
        profile_dir=profile_dir,
        decoder=args.decoder,
        archive=args.archive,
        limit=args.limit,
    )

    counts = {s: sum(1 for r in results if getattr(r, s)) for s in PB.STAGES}
    print(
        f"[計數] allowlisted={len(results)}/{al.count} "
        f"extracted={counts['extracted']} "
        f"size_crc_ok={counts['size_crc_ok']} "
        f"schema_valid={counts['schema_valid']} "
        f"chunked={counts['chunked']}"
    )
    failed = [r for r in results if r.fail]
    for r in failed:
        print(f"  ✗ {r.entry.jid} 停在 {r.fail[0]}：{r.fail[1][:100]}")
    if not failed:
        print("  （無失敗卷）")

    # ── 階段 5：產出物 ─────────────────────────────────────────────────────
    manifest = PB.build_manifest(
        results,
        al,
        decoder=args.decoder,
        external_snapshot=args.external_snapshot,
        limited=args.limit is not None,
    )
    manifest_path = args.manifest_out or (
        ROOT / "specs" / "006-m1-judicial-selection-profile" / "profile-manifest.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[manifest] → {manifest_path}")

    bundle_path = profile_dir / "review-bundle.md"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    # ⚠ `newline=""` 不可省。`Path.write_text` 預設走 universal newlines 轉換，
    # 會把原文裡的 **CRLF 寫成 LF** —— 審閱包內容看起來完全正常，但 `JFULL`
    # 的逐字性就被破壞了（2026-10-10 實測：100 卷全部逐字不符，每卷少掉的
    # 字元數正好等於其 CRLF 數）。這是**看不見**的改寫，比明面上的錯更危險。
    with bundle_path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(PB.render_review_bundle(results))
    print(f"[審閱包] → {bundle_path}")

    print(
        f"[凍結] seed_tree_sha256={manifest['frozen_corpus']['seed_tree_sha256'][:16]}… "
        f"external_snapshot.present="
        f"{manifest['frozen_corpus']['external_snapshot']['present']}"
    )
    print(
        f"[FORCED] {manifest['forced_break_documents']} 卷有強制斷點，"
        f"chunk 總數 {manifest['total_chunks']}"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
