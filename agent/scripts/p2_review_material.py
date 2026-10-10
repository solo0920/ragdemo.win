#!/usr/bin/env python3
"""把 P2 預篩的候選點展開成**可逐筆判讀**的材料（每點一節，前後文加寬）。

為什麼需要這支：預篩報告只給 90／150 字元的頭尾，那是為了排序用的；
要判斷「holding 有沒有被切斷」需要看**完整的論證段落**，否則會把
「切在句中」誤判成「切在段落邊界」。

用法：
    .venv/bin/python agent/scripts/p2_review_material.py \
        --parser ~/share/split_judgment_v2.py --before 900 --after 900
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ingest" / "judgements"))
sys.path.insert(0, str(ROOT / "agent" / "scripts"))

import chunk as _chunk  # noqa: E402
import profile_build as PB  # noqa: E402
import p2_prescreen_forced as P  # noqa: E402
import text as _text  # noqa: E402

MANIFEST = ROOT / "specs" / "006-m1-judicial-selection-profile" / "profile-manifest.json"


def candidates(parser_mod, min_score: float):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    out = []
    for entry in [e for e in manifest["entries"] if e["forced_break_chunk_indexes"]]:
        raw = PB.PROFILE_DIR / "raw" / entry["path_posix"]
        jfull = json.loads(raw.read_text(encoding="utf-8"))["JFULL"]
        foot = P._footer_offset(jfull, parser_mod)
        chunks = _chunk.chunk_text(
            _text.LosslessText.from_string(jfull),
            source_document=entry["path_posix"],
            jid=entry["jid"],
        )
        for idx in entry["forced_break_chunk_indexes"]:
            c = chunks[idx]
            if foot is not None and c.start_offset >= foot:
                continue
            before = jfull[max(0, c.start_offset - 900) : c.start_offset]
            after = jfull[c.start_offset : c.start_offset + 900]
            score, hits = P.score_context(
                jfull[max(0, c.start_offset - 220) : c.start_offset + 260]
            )
            if score >= min_score:
                out.append(
                    {
                        "score": score,
                        "hits": hits,
                        "jid": entry["jid"],
                        "chunk_index": idx,
                        "start_offset": c.start_offset,
                        "end_offset": c.end_offset,
                        "doc_len": len(jfull),
                        "forced_in_doc": len(entry["forced_break_chunk_indexes"]),
                        "before": before,
                        "after": after,
                    }
                )
    out.sort(key=lambda r: (-r["score"], r["jid"], r["chunk_index"]))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="p2_review_material",
        description="把預篩候選展開成可判讀的材料（不下結論，判定由人做）",
    )
    ap.add_argument("--parser", type=Path, default=None, help="結構切分 parser 路徑")
    ap.add_argument("--min-score", type=float, default=2.0)
    ap.add_argument("--before", type=int, default=900)
    ap.add_argument("--after", type=int, default=900)
    ap.add_argument("--only", type=int, default=None, help="只看第 N 點（1-based）")
    ap.add_argument("--out", type=Path, default=None, help="寫成 Markdown（預設印 stdout）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if args.parser is None:
        print(
            "✗ 必須給 --parser：簽名區過濾需要它，否則候選會混入固定格式區。",
            file=sys.stderr,
        )
        return 2
    parser_mod = P._load_parser(args.parser)
    rows = candidates(parser_mod, args.min_score)
    if args.only is not None:
        rows = [r for r in rows if rows.index(r) == args.only - 1]

    lines = [
        "# P2 候選判讀材料",
        "",
        f"候選 {len(rows)} 處（門檻 ≥{args.min_score}，簽名區已排除）。",
        "**這份材料不下結論**——它只是把切點前後展開，讓人判斷「holding 有沒有被切斷」。",
        "",
    ]
    for i, r in enumerate(rows, 1):
        before = r["before"][-args.before :]
        after = r["after"][: args.after]
        lines += [
            f"## {i}. {r['jid']}　chunk {r['chunk_index']}　score {r['score']}",
            "",
            f"- 命中特徵：{'；'.join(r['hits'])}",
            f"- 切點：offset {r['start_offset']}（chunk 結束於 {r['end_offset']}，"
            f"全文 {r['doc_len']:,} 字元）",
            f"- 該卷 FORCED 共 {r['forced_in_doc']} 處",
            "",
            "**⟨切點在此｜`|` 之間⟩**",
            "",
            "```text",
            before,
            "│◀────────── 切點 ──────────▶│",
            after,
            "```",
            "",
            "**判讀要回答的**：`│◀` 之前的論證是否在此處自然收束？"
            "還是「理由」「亦無」「第N條」這類**必須接續**的內容被切開？",
            "",
            "---",
            "",
        ]
    text = "\n".join(lines)
    if args.out:
        args.out.write_text(text, encoding="utf-8", newline="")
        print(f"寫入 {args.out}（{len(rows)} 處候選）")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())