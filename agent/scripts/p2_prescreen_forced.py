#!/usr/bin/env python3
"""P2 審閱的**機器預篩**：在 228 個 FORCED 點裡找出可能是 holding 被切斷的位置。

## 這支做什麼、明確不做什麼

**做**：對每個 FORCED（硬切）點抓取前後文，用一組「判決理由論述」的句式特徵
打分，排出**最值得人看的十幾個**候選，輸出成一份可逐筆判讀的報告。

**不做**：**不下結論**。輸出裡沒有「這是正例」這種判斷——那必須由人審
（P2）。這支唯一的產出是「先看這幾處」，而它**允許漏**（規則抓不到的
holding 仍然存在，只是不會出現在這份報告裡）。

## 為什麼需要它

228 個 FORCED 點（37 卷）逐一讀原文不現實，而其中大部分落在**附表、系譜、
通訊記錄**這類連續資料流裡——那裡的硬切沒有論證被截斷的意義。預篩的目的是
把視線集中到「論述文字交界」的位置。

**簽名區過濾是最大的單一改善**（2026-10-10 實測）：特徵詞 `訴訟費用`、`給付`
在主文與末尾聲明區也會出現，而初版沒有位置資訊，於是前 5 名裡有 3 名是切在
「書記官簽名」「以上正本係照原本作成」的**假陽性**。加上位置過濾後：

```
228 點 → 落在簽名區及其後 204 處（89%）被排除 → 有特徵命中 9 處
```

## 用法

    # 建議給 --parser：沒有它就定位不到簽名區，前几名會混入固定格式區的切點
    .venv/bin/python agent/scripts/p2_prescreen_forced.py \\
        --parser ~/share/split_judgment_v2.py --top 9

parser 路徑**刻意不寫死**（FR-011／INV-PATH：不得出現 `/home/<user>/…`），
也刻意**不讀環境變數**（env-audit 會判定為缺宣告的跨機不一致來源——S1 spec
FR-011 記錄了同類案例）。所以由呼叫端明確指定，給錯就非零退出。

## 規則的出處與已知盲點

特徵詞分三類，都是臺灣判決書理由段的常見句式（**經實測 36/37 卷**含
`事實及理由` 與 `判決如下` 兩個錨點）：

| 類 | 詞例 | 用意 |
|---|---|---|
| 論述動詞 | 本院認為、綜合觀察、應負、尚難認為、依上論斷 | 理由段的推理骨架 |
| 論述連接 | 因此、故、綜合上開、另按、再按、按上開 | 結論／轉折的落點 |
| 終局判斷 | 駁回、應給付、給付、賠償、敗訴、訴訟費用 | 主文式的斷言 |

**已知盲點（如實寫出，不假裝解決）**：

1. ~~「本院認為」可能出現在理由段開頭~~——**部分修正**：簽名區已用位置過濾
   排除（204/228，89%），但理由段**開頭**的特徵詞仍會留下來，所以**仍會有假陽性**。
2. **理由段裡完全沒有特徵詞的論述會漏掉**——尤其是法條引用的密集段落。
   所以這份報告的定位是「先看這些」，**不是**「只有這些」。
3. 詞表是**從這批資料觀察到的**，不是從語料庫來的；跨案由可能不適用。
   這一點要等第一批審閱結果回來才能判斷是否要擴充。
4. **簽名區位置由 parser 提供**，所以本工具的正確性繫於該 parser 的
   `find_footer`。若 parser 升版改動簽名區定位，本工具的過濾會**靜默偏移**——
   故過濾數量（`已排除 N 處`）印在報告開頭，變動時看得出來。

## 實測基線（2026-10-10，100 卷／228 點）

| 條件 | 結果 |
|---|---|
| 無 `--parser` | 30 處命中（**已知含簽名區假陽性**），報告開頭印警告 |
| 有 `--parser` | **排除 204 處（89%）**，剩 **9 處**命中 |
| 第 1 名 | `PCDV,113,重訴更一,1` chunk 26：「…損害賠償請求權云云，**亦無**」↔「**理由。**」 |

⚠ **offset 空間曾經搞錯過（已修正，記錄在此避免重蹈）**：`chunk_text` 的
`start_offset` 是**原文 CRLF 空間**，而 `normalize()` 之後是 **LF 空間**，
兩者差額＝前置行數。初版拿 LF 空間去比簽名區位置，於是把 `PCDV,110,重訴,571`
chunk 23（offset 17,237，**真在正文**）誤判成末尾區；該卷簽名區在 CRLF 空間是
17,294，正確結論是「17237 < 17294 ⇒ 正文」。修正後用
`splitlines(keepends=True)` 逐行實測累加，**不依賴換行格式假設**。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import profile_build as PB  # noqa: E402

MANIFEST = ROOT / "specs" / "006-m1-judicial-selection-profile" / "profile-manifest.json"

#: 三類特徵，各附權重。權重是**主觀排序用的啟發式**，不是統計結果——
#: 它的唯一責任是「讓值得看的點排在前面」，不是「算出正確機率」。
REASONING_PATTERNS = {
    "論述動詞": (r"本院認為|綜合觀察|應負|尚難認為|尚無理由|依上論斷|如上所述", 3.0),
    "論述連接": (r"因此|故|綜合上開|綜合上列|另按|再按|按上開|是以", 2.0),
    "終局判斷": (r"駁回|應給付|給付|賠償|敗訴|訴訟費用|應負賠償", 2.5),
}

#: 明顯是**非論述**內容的訊號——命中就扣分，把附表／系譜／對話推下去。
NON_REASONING = re.compile(
    r"附表[一二三四五六七八九十]|通訊記錄|對話紀錄|親屬關係|系譜|戶籍"
    r"|電話|傳真|手機|0980|0912|line\s*:|LINE:|@\S+\s",
)


def _footer_offset(jfull: str, parser_mod) -> int | None:
    """回傳**簽名區在原文 `jfull` 的字元 offset**；找不到回 `None`。

    ## 為什麼需要它（2026-10-10 實測）

    「終局判斷」特徵詞（`訴訟費用`、`給付`）在**主文與末尾聲明區**也會出現。
    初版沒有位置資訊，於是簽名區附近的切點被排進前几名——實測前 5 名裡有
    **3 名是假陽性**（切在「書記官簽名」「以上正本係照原本作成」「附表 編號…」）。
    那不是規則的錯，是**規則缺位置**。

    ## offset 換算（實測 7 個 N 值全對）

    `normalize()` 把 CRLF 換成 LF，所以**normalize 後的 offset 不是原文 offset**：

        原文 offset == normalize 後 offset + 前置行數

    每個 CRLF 比 LF 多 1 字元（實測 N=1/10/100/500/1000/1969 全相符）。
    ⚠ 該換算**依賴「每個換行都是 CRLF 且 normalize 逐一對應」**；若來源有單獨的
    `\r`（舊 Mac 換行）會錯。因此實作用 `splitlines(keepends=True)` 逐行實測累加，
    **不依賴該假設**。
    """
    norm_lines = parser_mod.normalize(jfull).split("\n")
    nrm = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    main_i = next((i for i, l in enumerate(norm_lines) if nrm(l) == "主文"), None)
    if main_i is None:
        return None
    body_i = next(
        (
            i
            for i in range(main_i + 1, len(norm_lines))
            if nrm(norm_lines[i]) in parser_mod.TITLES
            or parser_mod.R0.match(norm_lines[i].strip())
            or parser_mod.R1.match(norm_lines[i].strip())
        ),
        main_i,
    )
    fi = parser_mod.find_footer(norm_lines, body_i)
    if fi is None:
        return None
    raw_lines = jfull.splitlines(keepends=True)
    return sum(len(l) for l in raw_lines[:fi])


def _load_parser(path):
    """載入結構切分 parser。**找不到就明確報錯**，不靜默降級。

    為什麼不靜默：簽名區過濾是這個工具的核心價值之一。少了它，輸出看起來
    一樣正常，但前几名會混入「書記官簽名」那種毫無意義的切點——那正是
    2026-10-10 初版的實際狀況（過濾函式 import 失敗回 `None`，於是 0 處被排除，
    而報告沒有說任何話）。
    """
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"✗ parser 不存在：{p}")
    spec = importlib.util.spec_from_file_location("s1_parser", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

CONTEXT_BEFORE = 220
CONTEXT_AFTER = 260


def score_context(text: str) -> tuple[float, list[str]]:
    """對一段前後文打分。回傳（分數, 命中的特徵描述）。"""
    hits: list[str] = []
    score = 0.0
    for label, (pattern, weight) in REASONING_PATTERNS.items():
        found = re.findall(pattern, text)
        if found:
            # 同一特徵重複出現只算一次——理由段會反覆用「本院認為」，
            # 重複累加會讓整段都拿到高���，等於沒有排序作用。
            score += weight
            uniq = sorted(set(found))
            hits.append(f"{label}×{len(found)}：{'、'.join(uniq[:4])}")
    if NON_REASONING.search(text):
        score -= 4.0
        hits.append("（非論述訊號：附表／系譜／通訊記錄）")
    return score, hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="p2_prescreen_forced",
        description="在 FORCED 硬切點裡預篩出可能是 holding 被切斷的位置（不下結論）",
    )
    ap.add_argument("--top", type=int, default=20, help="輸出前 N 名（預設 20）")
    ap.add_argument(
        "--all", action="store_true", help="輸出全部有命中的點，不只前 N"
    )
    ap.add_argument("--min-score", type=float, default=2.0, help="分數門檻（預設 2.0）")
    ap.add_argument(
        "--parser",
        type=Path,
        default=None,
        help="結構切分 parser 路徑。**強烈建議提供**——沒有它就無法定位簽名區，"
        "報告會混入「書記官簽名」那類毫無意義的切點（前幾名可能是假陽性）",
    )
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    parser_mod = _load_parser(args.parser)

    if not MANIFEST.is_file():
        print(f"✗ manifest 不存在：{MANIFEST}", file=sys.stderr)
        return 2
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    forced = [e for e in manifest["entries"] if e["forced_break_chunk_indexes"]]

    rows: list[dict] = []
    excluded_tail = 0
    for entry in forced:
        raw = PB.PROFILE_DIR / "raw" / entry["path_posix"]
        if not raw.is_file():
            print(f"✗ 原始檔不存在：{raw}", file=sys.stderr)
            return 2
        jfull = json.loads(raw.read_text(encoding="utf-8"))["JFULL"]
        foot_at = _footer_offset(jfull, parser_mod) if parser_mod else None

        # chunk 的 offset 邊界表不在 manifest 裡（manifest 只存統計），
        # 所以邊界要從 chunk_index × 目標大小推是不可靠的。
        # 正確做法：直接重跑 chunker（凍結參數）取得精確邊界。
        import chunk as _chunk
        import text as _text

        chunks = _chunk.chunk_text(
            _text.LosslessText.from_string(jfull),
            source_document=entry["path_posix"],
            jid=entry["jid"],
        )

        for idx in entry["forced_break_chunk_indexes"]:
            if idx >= len(chunks):
                continue
            c = chunks[idx]
            # 簽名區／其後（附表）的切點不是「holding 被切斷」——那是判決書的
            # 固定格式區，硬切在那裡沒有論證被截斷的意義（實測 30 個命中裡
            # 有 14 個落在這裡，是最大的假陽性來源）。
            if foot_at is not None and c.start_offset >= foot_at:
                excluded_tail += 1
                continue
            before = jfull[max(0, c.start_offset - CONTEXT_BEFORE) : c.start_offset]
            after = jfull[c.start_offset : c.start_offset + CONTEXT_AFTER]
            score, hits = score_context(before + "\n" + after)
            if score >= args.min_score or args.all:
                rows.append(
                    {
                        "jid": entry["jid"],
                        "chunk_index": idx,
                        "start_offset": c.start_offset,
                        "score": round(score, 2),
                        "hits": hits,
                        "forced_count_in_doc": len(
                            entry["forced_break_chunk_indexes"]
                        ),
                        "before_tail": before[-90:],
                        "after_head": after[:150],
                    }
                )

    rows.sort(key=lambda r: -r["score"])
    shown = rows if args.all else rows[: args.top]

    total = sum(len(e["forced_break_chunk_indexes"]) for e in forced)
    print(f"# P2 預篩：FORCED 點 {total} 處")
    if parser_mod is None:
        print(
            "# ⚠ **未提供 --parser：簽名區過濾未啟用**，前几名可能混入"
            "「書記官簽名／以上正本」那類固定格式區的切點（實測前 5 有 3 是這種）"
        )
    else:
        print(
            f"# 已排除簽名區及其後 {excluded_tail} 處"
            f"（{excluded_tail / total:.0%}）——硬切在判決書固定格式區"
            f"不具論證截斷的意義"
        )
    print(f"# 有特徵命中（≥{args.min_score}）：{len(rows)} 處；以下顯示 {len(shown)} 處")
    print("# **本報告不下結論**——它是「先看這些」，不是「只有這些」。")
    print("# 規則盲點見本檔 docstring。\n")

    for i, r in enumerate(shown, 1):
        print(f"## {i}. {r['jid']}　chunk {r['chunk_index']}　"
              f"score {r['score']}　（該卷 FORCED 共 {r['forced_count_in_doc']} 處）")
        print(f"- 命中：{'；'.join(r['hits'])}")
        print(f"- 切點 offset：{r['start_offset']}")
        print(f"- 前文尾：{r['before_tail']!r}")
        print(f"- 後文頭：{r['after_head']!r}")
        print()

    if not rows:
        print("（沒有任何點達到門檻）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())