#!/usr/bin/env python3
"""量測「檢索時能拿到多少上下文」對法條抽取召回的影響。

## 量的是什麼

同一份判決切成 chunk 後，**只用檢索得到的文字**做規則抽取，會漏掉多少法條。
基準是「整份原文依序掃描」（資料完整時的上限）。

⚠ **本基準不是「正確答案」**。它自己會把縮寫當成游標的更新來源，例如：

    …違反勞動基準法第63條、職安法第26條第1項等保護他人法律）、第185條第1項…

基準（認縮寫）會把 `第185條` 歸給 `職業安全衛生法`，而**不認縮寫**的模式會歸給
`勞動基準法`——後者才是判決原意。所以本工具報的 `多抓 FP` 應讀作
**「與基準不一致」**而非「錯」；哪一邊對要靠 FR-011 的人工標註判斷。

## 模式（全部都是「檢索時真的做得到的」）

| 代號 | 檢索行為 | 游標 |
|---|---|---|
| `chunk` | 只取該 chunk（＝ v2 現況） | 每個 chunk 從 `None` 起 |
| `chunk_ab` | 同上，但認得縮寫 | 同上 |
| `ordered` | 取 ±1 鄰居，**依序串接後掃描** | **跨鄰居延續** |
| `ordered_ab` | 同上 ＋ 縮寫 | 同上 |
| `parent` | 取該 chunk ＋ 其父塊，依序串接掃描 | 跨父塊延續 |
| `parent_ab` | 同上 ＋ 縮寫 | 同上 |

⚠ **`ordered` 與 `parent` 必須依序串接後共用一個游標**。若把每段各自獨立掃描再取
聯集，得到的結果會**與 `chunk` 幾乎相同**——那不是「上下文無效」的證據，
而是實作把上下文丟掉了。本工具第一版正是這樣寫錯的。

## 本工具自我更正的三個方法學錯誤（記錄以免重蹈）

1. **把基準當成被評估的模式**：第一版的 `doc_cursor` 實作是「掃描到每個 chunk 末端的
   前綴再取聯集」。chunk 覆蓋全文時，前綴聯集**恆等於整份掃描**＝基準，
   必然得到召回 1.0000／precision 1.0000——**同義反覆，量不出東西**。
   現已移除該列；整份掃描只當基準。
2. **模式標錯**：`prev_fallback` 的 `fallback` 分支是 no-op，與 `chunk` 的唯一差別其實是
   縮寫表有無，卻被標成「回退前句」。量到的 +7pp 是**縮寫**的功勞。
3. **「句首殘缺回退前句」本身不成立**：單一 chunk 內 `cur` 已跨句延續，
   「前一句的 `cur`」就是當前的 `cur`，沒有東西可回退。這個修法方向應直接廢棄，
   不是實測無效。

## 實測（2026-10-10，100 卷）

chunker ＝ M1 凍結 `chunk.py`（`parent` 系列需 v2 parser，另跑）：

| 模式 | 召回 | 漏抓 FN | 與基準不一致 FP |
|---|---:|---:|---:|
| `chunk`（v2 現況） | 0.7283 | 548 | 57 |
| `chunk_ab` | 0.7992 | 405 | 0 |
| `ordered`（±1 依序） | 0.8632 | 276 | 128 |
| `ordered_ab` | 0.9494 | 102 | 0 |

**排序**（+13.5pp）與**縮寫表**（+7.1pp）**各自有效且可疊加**。

⚠ **跨 chunker 版本的數字不可直接比較**（v2 結構切分下同一模式召回 0.7124，
與 M1 的 0.7283 差 1.6pp）。故輸出檔一律記 `chunker` 欄位。

## 用法

    .venv/bin/python agent/scripts/law_extraction_benchmark.py
    .venv/bin/python agent/scripts/law_extraction_benchmark.py \
        --parser ~/share/split_judgment_v2.py     # 加測 parent 系列
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ingest" / "judgements"))
sys.path.insert(0, str(ROOT / "agent" / "scripts"))

import chunk as M1_CHUNK  # noqa: E402
import profile_build as PB  # noqa: E402
import text as _text  # noqa: E402

MANIFEST = ROOT / "specs" / "006-m1-judicial-selection-profile" / "profile-manifest.json"

#: 全域法規清單（FR-011 的 base list）。
LAWS = [
    "民事訴訟法", "民事訴訟費用法", "民法", "家事事件法", "勞動基準法",
    "勞動事件法", "勞工退休金條例", "勞工保險條例", "就業服務法",
    "性別平等工作法", "土地法", "強制執行法", "保險法", "銀行法",
    "證券交易法", "信託法", "破產法", "建築法", "公寓大廈管理條例",
    "都市更新條例", "個人資料保護法", "著作權法", "商標法", "專利法",
    "公平交易法", "金融消費者保護法", "洗錢防制法", "刑法", "憲法",
    "證券投資信託及顧問法", "職業安全衛生法", "消費者保護法",
]

#: 確定性的全域縮寫後備表。**主機制應是判決內「全名（下稱簡稱）」**（FR-011）——
#: 這張表只是 fallback，且實測顯示它可能造成**錯誤歸屬**（見模組 docstring）。
ABBR = {
    "勞基法": "勞動基準法",
    "證交法": "證券交易法",
    "勞退條例": "勞工退休金條例",
    "投顧法": "證券投資信託及顧問法",
    "職安法": "職業安全衛生法",
    "消保法": "消費者保護法",
}


def _mk(with_abbr: bool):
    """做一個抽取器。**不做句界切分**——游標是「上一個出現過的法規名」，
    句界與游標語意無關，切了只會製造假的重置。"""
    names = sorted(set(LAWS) | (set(ABBR) if with_abbr else set()), key=len, reverse=True)
    pat = re.compile("(" + "|".join(names) + ")?第(\\d+)條(?:之(\\d+))?")

    def extract(text: str, cur: str | None = None) -> tuple[set[str], str | None]:
        out: set[str] = set()
        for m in pat.finditer(text):
            if m.group(1):
                cur = ABBR.get(m.group(1), m.group(1))
            if cur is None:
                continue  # 沒有線索就不猜——猜測是偽造（憲法 VI）
            out.add(f"{cur}§{m.group(2)}" + (f"-{m.group(3)}" if m.group(3) else ""))
        return out, cur

    return extract


EX = _mk(with_abbr=False)
EX_AB = _mk(with_abbr=True)


def _win(texts: list[str], idx: int, radius: int = 1) -> str:
    lo, hi = max(0, idx - radius), min(len(texts), idx + radius + 1)
    return "".join(texts[lo:hi])


def _measure(texts: list[str], parents: dict[str, str] | None = None) -> dict[str, set[str]]:
    """回傳各模式在「整份文件取聯集」下的結果集合。

    之所以取聯集：這是「使用者翻完這份判決的所有 chunk」的結果，
    對應 `judgment_meta.laws[]` 那種文件層欄位。
    """
    got: dict[str, set[str]] = {k: set() for k in _MODE_NAMES(parents is not None)}
    for i, t in enumerate(texts):
        s, _ = EX(t)
        got["chunk"] |= s
        s, _ = EX_AB(t)
        got["chunk_ab"] |= s
        s, _ = EX(_win(texts, i, 1))          # 依序串接 → 游標自然延續
        got["ordered"] |= s
        s, _ = EX_AB(_win(texts, i, 1))
        got["ordered_ab"] |= s
        if parents is not None:
            pid_parents = parents
            ptext = pid_parents.get(str(i)) or ""
            if ptext:
                s, _ = EX(ptext + t)           # 父塊在前 → 依序
                got["parent"] |= s
                s, _ = EX_AB(ptext + t)
                got["parent_ab"] |= s
            else:
                s, _ = EX(t)
                got["parent"] |= s
                s, _ = EX_AB(t)
                got["parent_ab"] |= s
    return got


def _MODE_NAMES(has_parent: bool) -> list[str]:
    names = ["chunk", "chunk_ab", "ordered", "ordered_ab"]
    if has_parent:
        names += ["parent", "parent_ab"]
    return names


LABELS = {
    "chunk": "chunk（單 chunk，不認縮寫）＝v2 現況",
    "chunk_ab": "chunk ＋ 縮寫表",
    "ordered": "ordered window ±1 依序（不認縮寫）",
    "ordered_ab": "ordered window ±1 依序 ＋ 縮寫表",
    "parent": "parent（父塊＋chunk 依序，不認縮寫）",
    "parent_ab": "parent ＋ 縮寫表",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="law_extraction_benchmark",
        description="量測「檢索時可取得的上下文」對法條抽取召回的影響",
    )
    ap.add_argument("--parser", type=Path, default=None,
                    help="v2 parser 路徑；給定則加測 parent 系列（需結構切分才有 parent）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    v2 = None
    if args.parser is not None:
        import p2_prescreen_forced as P
        v2 = P._load_parser(args.parser)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    out_rows: list[dict] = []
    for entry in manifest["entries"]:
        raw = PB.PROFILE_DIR / "raw" / entry["path_posix"]
        jf = json.loads(raw.read_text(encoding="utf-8"))["JFULL"]

        base, _ = EX_AB(jf)   # 基準＝整份依序 ＋ 縮寫

        chunks_m1 = M1_CHUNK.chunk_text(
            _text.LosslessText.from_string(jf),
            source_document=entry["path_posix"], jid=entry["jid"],
        )
        texts_m1 = [jf[c.start_offset : c.end_offset] for c in chunks_m1]
        got_m1 = _measure(texts_m1, None)

        row: dict[str, object] = {
            "jid": entry["jid"], "baseline": len(base),
            "chunks_m1": len(texts_m1),
        }
        for k, v in got_m1.items():
            row[f"m1.{k}"] = (len(v & base), len(base - v), len(v - base))

        if v2 is not None:
            try:
                res = v2.safe_parse(jf, max_chars=800, jid=entry["jid"])
            except Exception:
                continue
            v2chunks = res["chunks"]
            if not v2chunks:
                continue
            pmap = res.get("parents") or {}
            texts_v2 = [c["text"] for c in v2chunks]
            parents = {
                str(i): (pmap.get(c["parent_id"]) or {}).get("text") or ""
                for i, c in enumerate(v2chunks)
            }
            row["chunks_v2"] = len(v2chunks)
            row["parents_present"] = sum(1 for v in parents.values() if v)
            for k, v in _measure(texts_v2, parents).items():
                row[f"v2.{k}"] = (len(v & base), len(base - v), len(v - base))
        out_rows.append(row)

    def report(prefix: str, label: str, modes: list[str], subset: list[dict]) -> None:
        tot = sum(int(r["baseline"]) for r in subset)
        if not tot:
            return
        print(f"\n### {label}（{len(subset)} 卷，基準 {tot} 條）\n")
        print("| 模式 | 召回 | 漏抓 FN | 與基準不一致 FP | precision |")
        print("|---|---:|---:|---:|---:|")
        for k in modes:
            h = sum(int(r[f"{prefix}.{k}"][0]) for r in subset if f"{prefix}.{k}" in r)
            fn = sum(int(r[f"{prefix}.{k}"][1]) for r in subset if f"{prefix}.{k}" in r)
            fp = sum(int(r[f"{prefix}.{k}"][2]) for r in subset if f"{prefix}.{k}" in r)
            prec = h / (h + fp) if (h + fp) else 0.0
            print(f"| {LABELS[k]} | {h / tot:.4f} | {fn} | {fp} | {prec:.4f} |")

    nz = [r for r in out_rows if int(r["baseline"]) > 0]
    print(f"# 檢索上下文 × 法條抽取召回（全量 {len(out_rows)} 卷）\n")
    print(f"基準＝整份原文依序掃描 ＋ 縮寫表。有法條的卷 {len(nz)}/{len(out_rows)}。")
    print("⚠ **基準的 FP 欄應讀作「與基準不一致」**——基準自己也會因縮寫而錯誤歸屬。")
    report("m1", "chunker ＝ M1 凍結 `chunk.py`",
           ["chunk", "chunk_ab", "ordered", "ordered_ab"], nz)
    if v2 is not None:
        modes = ["chunk", "chunk_ab", "ordered", "ordered_ab", "parent", "parent_ab"]
        report("v2", "chunker ＝ v2 結構切分（max_chars=800）", modes, nz)
        pp = sum(int(r.get("parents_present", 0)) for r in nz)
        tc = sum(int(r.get("chunks_v2", 0)) for r in nz)
        zero = sum(1 for r in nz if not r.get("parents_present"))
        print(f"\nv2 parent 覆蓋：{pp}/{tc} chunk（{pp / tc:.1%}）；完全無 parent 的卷 {zero}")
        print("⚠ **跨 chunker 兩表的數字不可直接比較**（基準相同但切分不同）。")

        out = ROOT / "specs" / "007-judgment-three-tier-storage" / "law-extraction-benchmark.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "baseline": "整份原文依序掃描 + 縮寫表",
        "chunkers": ["ingest/judgements/chunk.py (M1 frozen)"]
                    + (["v2 structure split max_chars=800"] if v2 is not None else []),
        "documents": len(out_rows),
        "documents_with_articles": len(nz),
        "baseline_total_articles": sum(int(r["baseline"]) for r in nz),
        "per_document": out_rows,
        "note": "[hit, FN, disagree_with_baseline]；disagree 不等於錯，需人工標註判定",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n明細寫入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())