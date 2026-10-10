#!/usr/bin/env python3
"""S1-1：把 M1 解出的判決 raw 建為 **parquet 原文層**（FR-001）。

## 這一層的唯一職責：**忠實保存**

司法資料是法律事實。這個檔案日後會被拿來當**證據**——它必須能逐位元組
還原成司法院公布的那一份。這一���**不做任何清理**：

| 不做 | 為什麼 |
|---|---|
| 不改 CRLF | `JFULL` 的 CRLF 是原始 bytes 的一部分（實測：200 卷有 CRLF） |
| 不 trim | 去頭尾空白＝改寫原文 |
| 不 collapse 換行 | 同上 |
| 不做 Unicode 正規化 | NFKC 會改變字元（例：全形/半形） |
| 不抽 chunk | chunk 是衍生層，可重建；原文層不是 |

裁決 ③「文字清理只發生在衍生層」就是這條。**任何在這裡的「整理」都是偽造。**

## 還原驗證（這是本層存在的理由）

建完後必須能回答：「parquet 讀回來的 `jfull`，跟政府網站公布的那份
逐位元組相同嗎？」

`verify_parquet_fidelity.py` 負責那個問題。它比對的**不是**自己寫進去的東西，
而是 **archive 內的原始 .json bytes**——那是唯一的事實來源。

## 用法

    .venv/bin/python scripts/build-judgement-parquet.py
    .venv/bin/python scripts/build-judgement-parquet.py --limit 20   # 診斷
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: M1 解壓出的 raw 根目錄（200 卷分層樣本）。`data/judgements/*` 已 gitignore，
#: 這個目錄**每次都要重新解壓產生**，不是版控資產。
DEFAULT_RAW = ROOT / "data" / "judgements" / "profile-200" / "raw"

#: FR-001 要求的欄位。**順序有意義**（entry_sha256 在前，便於人工目視比對）。
COLUMNS = (
    "jid", "jyear", "jcase", "jno", "jdate", "jtitle",
    "jfull", "jpdf",
    "entry_path", "entry_sha256", "artifact_sha256",
    "jfull_sha256", "jfull_char_len", "jfull_byte_len", "crlf_count",
)

#: source JSON 的欄位 → parquet 欄位。**一對一，不改名**：
#: 改名會讓「這個值從哪來」需要額外對照表，而對照表就是可能出錯的地方。
SOURCE_KEYS = {
    "jyear": "JYEAR", "jcase": "JCASE", "jno": "JNO", "jdate": "JDATE",
    "jtitle": "JTITLE", "jfull": "JFULL", "jpdf": "JPDF",
}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="build-judgement-parquet",
        description="把解壓出的判決 raw 建為 parquet 原文層（逐位元組忠實）",
    )
    ap.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data" / "judgements" / "parquet" / "judgements.parquet")
    ap.add_argument("--limit", type=int, default=None, help="只建前 N 筆（診斷用）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        print("✗ 缺 pyarrow。這是 S1 唯一的新依賴（spec 007 FR-014）。", file=sys.stderr)
        print("  安裝：.venv/bin/pip install pyarrow==26.0.0", file=sys.stderr)
        return 4

    if not args.raw_dir.is_dir():
        print(f"✗ raw 目錄不存在：{args.raw_dir}", file=sys.stderr)
        print("  那是 M1 解壓的產物（gitignore），需先跑 scripts/build-m1-profile.py", file=sys.stderr)
        return 4

    # ⚠ 讀 raw .json 時**必須用 bytes**再用 utf-8-sig 解，不能讓 Python 的
    #   universal newlines 碰它——那會把 JFULL 裡的 CRLF 讀成 LF，
    #   檔案看起來完全正常，但**位元組級還原已經不可能**。
    #   這是 M1 踩過的坑（review-verdict 100/100 卷逐字不符）。
    paths = sorted(args.raw_dir.rglob("*.json"))
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        print(f"✗ {args.raw_dir} 底下沒有 .json", file=sys.stderr)
        return 4

    rows: list[dict] = []
    problems: list[str] = []
    for p in paths:
        raw_bytes = p.read_bytes()
        try:
            d = json.loads(raw_bytes.decode("utf-8-sig"))
        except Exception as exc:
            problems.append(f"{p.name}: JSON 解讀失敗 {exc}")
            continue
        jid = d.get("JID") or ""
        if not jid:
            problems.append(f"{p.name}: 無 JID")
            continue
        if jid != p.stem:
            # 檔名與 JID 不一致＝身份矛盾。這時**不猜**，記問題並跳過。
            problems.append(f"{p.name}: JID({jid}) 與檔名不符")
            continue
        jfull = d.get("JFULL") or ""
        rel = p.relative_to(args.raw_dir).as_posix()
        row = {
            "jid": jid,
            "entry_path": rel,
            # entry_sha256：整個 .json 的位元組 hash。這是「這份判決就是這份」的證據。
            "entry_sha256": sha256_bytes(raw_bytes),
            "jfull": jfull,
            "jfull_sha256": sha256_bytes(jfull.encode("utf-8")),
            # ⚠ byte_len 用 encode 後的長度，不是 len(str)。
            #   str 的長度是「字元數」，非 ASCII 會算錯。
            "jfull_byte_len": len(jfull.encode("utf-8")),
            "jfull_char_len": len(jfull),
            # CRLF 計數：這是**還原正確與否的快速指標**。
            # 若將來讀回的 CRLF 數不等於這個值，代表位元組已被改動（憲法 XI）。
            "crlf_count": jfull.count("\r\n"),
        }
        for dst, src in SOURCE_KEYS.items():
            row[dst] = d.get(src) or ""
        rows.append(row)

    if problems:
        print(f"✗ {len(problems)} 筆有問題（已跳過，不猜）：", file=sys.stderr)
        for x in problems[:20]:
            print(f"  {x}", file=sys.stderr)
        if len(rows) == 0:
            return 2

    # artifact_sha256：archive 本身的 hash，整批共用一個值。
    artifact = ROOT / "data" / "judgements" / "raw"
    art_sha = ""
    for cand in sorted(artifact.glob("*.rar")):
        h = hashlib.sha256()
        with cand.open("rb") as fh:
            for blk in iter(lambda: fh.read(1 << 20), b""):
                h.update(blk)
        art_sha = h.hexdigest()
        break
    for r in rows:
        r["artifact_sha256"] = art_sha

    # 明確定義型別。**特別是 jfull 用 large_string**：判決正文可達 32 萬字元
    # （實測最大 321,449），用 string（2GB 上限）雖然夠但那是 32-bit 偏移；
    # large_string 用 64-bit 偏移，未來卷數暴增也不會溢出。
    schema = pa.schema([
        ("jid", pa.string()),
        ("jyear", pa.string()),
        ("jcase", pa.string()),
        ("jno", pa.string()),
        ("jdate", pa.string()),
        ("jtitle", pa.string()),
        ("jfull", pa.large_string()),
        ("jpdf", pa.string()),
        ("entry_path", pa.string()),
        ("entry_sha256", pa.string()),
        ("artifact_sha256", pa.string()),
        ("jfull_sha256", pa.string()),
        ("jfull_char_len", pa.int64()),
        ("jfull_byte_len", pa.int64()),
        ("crlf_count", pa.int64()),
    ])
    table = pa.Table.from_pylist(rows, schema=schema)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, args.out, compression="zstd")

    total_crlf = sum(r["crlf_count"] for r in rows)
    total_bytes = sum(r["jfull_byte_len"] for r in rows)
    print(f"parquet → {args.out}")
    print(f"  列數        {len(rows):,}")
    print(f"  JFULL 位元組 {total_bytes:,}（zstd 壓縮後 {args.out.stat().st_size:,}）")
    print(f"  CRLF 總數   {total_crlf:,}（還原正確與否的指標）")
    print(f"  artifact    {art_sha[:16]}…")
    if problems:
        print(f"  ⚠ 跳過 {len(problems)} 筆（見上）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())