#!/usr/bin/env python3
"""在 200 卷分層樣本上跑結構切分 parser，產出 **評估分母錨點** `eval-manifest-200.json`。

## 為什麼需要這一支

spec 007 的所有比率門檻都以「共同評估分母」為錨點，而 rev6 已把分母由
M1 的 100 卷換成 **200 卷分層樣本**。錨點必須**進版控**，否則下個 clone
無法驗證「幾/200」怎麼來的（〈共同評估分母〉明文要求）。

## 與 100 卷 manifest 的關係

**兩套並存，數字不可混比。** 100 卷那份是 M1 關閉時的歷史錨點，保留不刪；
本產物是新的現行錨點。schema 刻意沿用 `m1-eval-manifest/1`——**不是懶得改**，
而是同一份 manifest 的兩次抽樣，欄位語意相同才能用同一套讀取程式。

## 每卷記錄什麼

除了解壓 manifest 已有的量測值（`jfull_sha256`／`crlf_count`／`chunk_count`／
`forced_break_chunk_indexes`），另記 parser 產出的 metadata
（`doc_type`／`parse_status`／`parse_issues`／`case_no`／`court_in_text`／
`judgment_date`／`hearing_closed`／`judges`／`clerk`／`laws`），
以及 **§8 的 `court` 分層資訊**（`court_code`／`court_dir`／`level`／`business`）。

⚠ **`court` 不由 parser 推論**（FR-004：從目錄取，裁決 ⑤）。本檔記的是
**目錄原字串 `court_dir`** 與分層結果，**不是** `normalize_district()` 的輸出——
那個函式的規則草案在 §8 且**還有三個未決難題**，未裁決前不得產生 `court` 值。

⚠ **`parser_version` 如實記錄**：`split_judgment_v2.py` 現況是 `2.0`，
而 spec rev3 起寫 `PARSER_VERSION='2.1'`。**本檔記 parser 實際回報的值**，
不寫 spec 的期望值——兩者不一致本身就是待解決的項。

## 用法

    .venv/bin/python agent/scripts/build_eval_manifest_200.py \\
        --parser ~/share/split_judgment_v2.py
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
sys.path.insert(0, str(ROOT / "agent" / "scripts"))

import profile_build as PB  # noqa: E402
import p2_prescreen_forced as P  # noqa: E402
import sample_stratified_200 as S  # noqa: E402

SPEC_DIR = ROOT / "specs" / "007-judgment-three-tier-storage"
ALLOWLIST = SPEC_DIR / "allowlist-200.json"
PROFILE_DIR = ROOT / "data" / "judgements" / "profile-200"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="build_eval_manifest_200",
        description="在 200 卷分層樣本上產出評估分母錨點",
    )
    ap.add_argument("--parser", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=SPEC_DIR / "eval-manifest-200.json")
    ap.add_argument("--max-chars", type=int, default=800)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    v2 = P._load_parser(args.parser)
    al = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    strata = {s["jid"]: s for s in al["strata_detail"]}
    profile = json.loads(
        (PROFILE_DIR / "profile-manifest.json").read_text(encoding="utf-8")
    ) if (PROFILE_DIR / "profile-manifest.json").is_file() else None
    prof_by_jid = {e["jid"]: e for e in (profile["entries"] if profile else [])}

    docs = []
    for entry in al["entries"]:
        jid = entry["path_posix"].rsplit("/", 1)[-1][:-len(".json")]
        st = strata.get(jid, {})
        raw_path = PB.PROFILE_DIR / "raw" / entry["path_posix"]
        if not raw_path.is_file():
            raw_path = PROFILE_DIR / "raw" / entry["path_posix"]
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        jfull = raw["JFULL"]
        res = v2.safe_parse(jfull, max_chars=args.max_chars, jid=jid)
        doc = res["doc"]

        pe = prof_by_jid.get(jid, {})
        rec = {
            "jid": jid,
            "entry_path": entry["path_posix"],
            "court_code": st.get("court_code", jid.split(",")[0]),
            "court_dir": st.get("court_dir"),
            "level": st.get("level"),
            "business": st.get("business"),
            "jid_fields": st.get("jid_fields", len(jid.split(","))),
            "jfull_sha256": hashlib.sha256(jfull.encode("utf-8")).hexdigest(),
            "jfull_char_len": len(jfull),
            "crlf_count": jfull.count("\r\n"),
            "doc_type": doc.get("doc_type"),
            "parse_status": doc.get("parse_status"),
            "parse_issues": doc.get("parse_issues", []),
            "parser_version": doc.get("parser_version"),
            "case_no": doc.get("case_no"),
            "court_in_text": doc.get("court"),
            "judgment_date": doc.get("judgment_date"),
            "hearing_closed": doc.get("hearing_closed"),
            "judges": doc.get("judges"),
            "clerk": doc.get("clerk"),
            "chunk_count": len(res.get("chunks") or []),
            "forced_break_chunk_indexes": pe.get("forced_break_chunk_indexes", []),
            "laws": doc.get("laws", []),
            "laws_unresolved": doc.get("laws_unresolved", 0),
            "fail": pe.get("fail"),
        }
        rec["notes_candidate"] = [
            f for f in ("no_reasoning_title",) if f in (rec["parse_issues"] or [])
        ]
        docs.append(rec)

    docs.sort(key=lambda d: d["jid"])
    status = collections.Counter(d["parse_status"] for d in docs)
    dtype = collections.Counter(d["doc_type"] for d in docs)
    issues = collections.Counter(
        f for d in docs for f in (d["parse_issues"] or [])
    )
    by_level = collections.Counter(d["level"] for d in docs)
    by_biz = collections.Counter(d["business"] for d in docs)
    parser_versions = sorted({d["parser_version"] for d in docs if d["parser_version"]})

    doc_ids = "\n".join(d["jid"] for d in docs)
    payload = {
        "schema": "m1-eval-manifest/1",
        "note": (
            "評估分母錨點（200 卷分層樣本）。**與 eval-manifest.json（100 卷）並存，"
            "數字不可混比。** `court` 欄位刻意不存在——normalize_district() 的規則"
            "草案在 §8 且尚有三個未決難題（spec 007），未裁決前不得產生該值。"
        ),
        "generated_at": "2026-10-10",
        "generated_by": "agent/scripts/build_eval_manifest_200.py",
        "denominator": {
            "documents": len(docs),
            "doc_ids_sha256": hashlib.sha256(doc_ids.encode("utf-8")).hexdigest(),
            "entries_sha256": al["entries_sha256"],
            "allowlist": "specs/007-judgment-three-tier-storage/allowlist-200.json",
            "selection_method": al["selection"]["method"],
            "strata": "court_dir（137 個，全部 ≥1）＋ 次維度 business",
            "is_population_estimate": False,
            "caveat": (
                "按法院目錄分層 → 每個目錄都被過度代表、大目錄相對 underrepresented。"
                "是**型別覆蓋的測試 fixture**，不是母體估計。"
            ),
        },
        "parser": {
            "path": str(args.parser.name),
            "versions_reported": parser_versions,
            "file_sha256": hashlib.sha256(args.parser.read_bytes()).hexdigest(),
            "max_chars": args.max_chars,
            "warning": (
                "parser 回報的版本與 spec 007 rev3 記載的 PARSER_VERSION='2.1' 不一致"
                f"（實際回報 {parser_versions}）。本檔如實記錄 parser 的回報值。"
            ) if parser_versions != ["2.1"] else None,
        },
        "summary": {
            "parse_status": dict(status),
            "doc_type": dict(dtype),
            "parse_issues_histogram": dict(issues.most_common()),
            "by_level": dict(by_level),
            "by_business": dict(by_biz),
            "total_chunks": sum(d["chunk_count"] for d in docs),
            "forced_break_documents": sum(
                1 for d in docs if d["forced_break_chunk_indexes"]
            ),
        },
        "documents": docs,
    }
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    print(f"評估分母錨點 → {args.out}")
    print(f"  documents      {len(docs)}")
    print(f"  doc_ids_sha256 {payload['denominator']['doc_ids_sha256'][:16]}…")
    print(f"  parse_status   {dict(status)}")
    print(f"  doc_type       {dict(dtype)}")
    print(f"  by_level       {dict(by_level)}")
    print(f"  by_business    {dict(by_biz)}")
    print(f"  issues         {dict(issues.most_common())}")
    print(f"  parser 回報版本 {parser_versions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())