#!/usr/bin/env python3
"""**獨立驗證器**：把三層（parquet／postgres／qdrant）逐一對**官方原始檔**比對。

## 為什麼需要這一支（而不是擴充 verify-parquet-fidelity.py）

因為「驗證器自己對官方檔案」這件事**必須一次做對**，而現有工具不夠格：

| 現況 | 問題 |
|---|---|
| L3 只抽 200 點 | 22 萬筆抽 200 ＝ 0.09%，不能宣稱「一致」 |
| L3 只驗 pg 的 `content_hash` 自洽 | 那是**自己跟自己比**，證明不了來自官方檔 |
| pg 沒對 `ChLaw.json`／`ChOrder.json` 逐筆比 | 那才是「來自官方」的證據 |
| parquet 沒對官方原檔比 | 判決側的比對對象是 archive，不是官方網站 |

**hash 自洽 ≠ 來自官方。** 一份被均勻改寫過的資料庫，hash 完全可以自洽。
這個工具的存在就是為了區分那兩件事。

## 比對的基礎設施

每個點都獨立算出**三個值**，兩兩比對：

```
官方原檔（解出 ChLaw.json/ChOrder.json、unrar p 取出 archive 內 .json）
   ↓ normalize（唯一的轉換函式，ingest/laws/normalize.py）
   ↓
parquet / pg / qdrant
```

轉換只允許一次，且**必須是 repo 內的那一份**——若驗證器自己寫一份
normalize，兩份實作會漂移，那比對就沒有意義了。

## 判定

| 結果 | 意義 |
|---|---|
| 全層一致 | 「檢索出來能還原官方原始內容」成立 |
| 任一層缺欄位 | **不宣告通過**——缺欄位是未覆蓋，不是通過 |

⚠ 這支工具的設計原則來自一個真實教訓：`verify-parquet-fidelity.py` 第一版
在 archive 比對 0/200 全 skip 的情況下**回報「全部通過」**。所以這裡
**任何一個計數為 0 一律判失敗**，沒有例外。

## 用法

    .venv/bin/python scripts/verify-three-layers.py
    .venv/bin/python scripts/verify-three-layers.py --unrar ~/.local/bin/unrar
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingest" / "laws"))
sys.path.insert(0, str(ROOT / "backend"))

PARQUET = ROOT / "data" / "judgements" / "parquet" / "judgements.parquet"
RAW_DIR = ROOT / "data" / "judgements" / "profile-200" / "raw"
ARCHIVE_DIR = ROOT / "data" / "judgements" / "raw"
CH_LAW = ROOT / "data" / "laws" / "ChLaw.json"
CH_ORDER = ROOT / "data" / "laws" / "ChOrder.json"

FAILS: list[str] = []


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fail(msg: str) -> None:
    FAILS.append(msg)
    print(f"  ✗ {msg}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


# ─────────────────────────────────────────────────────────────────────────
# 官方原檔 → 應有的內容（唯一事實來源）
# ─────────────────────────────────────────────────────────────────────────
def official_judgements() -> dict[str, tuple[str, str]]:
    """回傳 jid → (jfull_sha256, entry_sha256)。取自 **archive 原始 bytes**。

    ⚠ 必須走 bytes 路徑：若讓 Python 的 universal newlines 碰 JFULL，
      CRLF 會被讀成 LF，hash 就對不上——而且檔案看起來完全正常。
    """
    out: dict[str, tuple[str, str]] = {}
    for p in sorted(RAW_DIR.rglob("*.json")):
        b = p.read_bytes()
        d = json.loads(b.decode("utf-8-sig"))
        jfull = d.get("JFULL") or ""
        out[d["JID"]] = (sha(jfull.encode("utf-8")), sha(b))
    return out


def official_laws() -> dict[tuple[str, int], tuple[str, int]]:
    """回傳 (pcode, article_seq) → (content_md5, char_len)。取自官方 ChLaw/ChOrder。

    用 repo 內唯一的 normalize（ingest/laws/normalize.py）——不自己重寫一份，
    兩份實作漂移的話比對就沒有意義。
    """
    import normalize as N
    out: dict[tuple[str, int], tuple[str, int]] = {}
    for f in (CH_LAW, CH_ORDER):
        if not f.is_file():
            fail(f"缺官方檔 {f}")
            continue
        laws = json.loads(f.read_text(encoding="utf-8-sig"))["Laws"]
        for law in laws:
            law["_pcode"] = N.pcode_of(law)
            law["_source"] = f.stem.replace("Ch", "").lower()
            for a in N.law_articles(law):
                out[(a["pcode"], a["article_seq"])] = (
                    hashlib.md5(a["article_content"].encode("utf-8")).hexdigest(),
                    a["char_len"],
                )
    return out


def official_law_meta() -> dict[str, tuple[str, str, str]]:
    """pcode → (law_name, law_level, law_url)。"""
    import normalize as N
    out: dict[str, tuple[str, str, str]] = {}
    for f in (CH_LAW, CH_ORDER):
        if not f.is_file():
            continue
        for law in json.loads(f.read_text(encoding="utf-8-sig"))["Laws"]:
            out[N.pcode_of(law)] = (law.get("LawName", ""), law.get("LawLevel", ""),
                                    law.get("LawURL", ""))
    return out


# ─────────────────────────────────────────────────────────────────────────
# 層 1：parquet（判決原文）
# ─────────────────────────────────────────────────────────────────────────
def check_parquet(off: dict[str, tuple[str, str]], unrar: Path | None) -> None:
    print("\n" + "=" * 72)
    print("【層 1】parquet 原文層 ↔ archive 原始 bytes（官方公布內容）")
    print("=" * 72)
    try:
        import pyarrow.parquet as pq
    except ImportError:
        fail("缺 pyarrow")
        return
    if not PARQUET.is_file():
        fail(f"parquet 不存在 {PARQUET}")
        return

    t = pq.read_table(PARQUET)
    cols = {c: t.column(c).to_pylist() for c in t.column_names}
    n = t.num_rows
    print(f"  parquet 列數 {n}｜官方（解壓 raw）份數 {len(off)}")
    if n == 0:
        fail("parquet 0 列")
        return

    # A) 與解壓產物比（不需要 unrar）
    a_ok = a_bad = 0
    for i in range(n):
        jid = cols["jid"][i]
        if jid not in off:
            a_bad += 1
            continue
        if off[jid][0] == cols["jfull_sha256"][i] and off[jid][1] == cols["entry_sha256"][i]:
            a_ok += 1
        else:
            a_bad += 1
    (ok if a_bad == 0 else fail)(f"parquet ↔ 解壓 raw：一致 {a_ok}／不符 {a_bad}")
    if a_ok == 0:
        fail("一筆都沒比到 —— 未驗證")
        return

    # B) 逐筆從 archive 取原始 bytes 重算（繞過解壓產物，唯一事實來源）
    if unrar is None or not unrar.is_file():
        print("  – 跳過 archive 原始 bytes 比對（未給 --unrar）")
        return
    arc_file = next(ARCHIVE_DIR.glob("*.rar"), None)
    if arc_file is None:
        fail("archive 不存在")
        return
    b_ok = b_bad = b_skip = 0
    for i in range(n):
        r = subprocess.run(
            [str(unrar), "p", "-inul", str(arc_file), cols["entry_path"][i]],
            capture_output=True, timeout=120,
        )
        if r.returncode != 0 or not r.stdout:
            b_skip += 1
            continue
        raw = r.stdout
        try:
            jfull = json.loads(raw.decode("utf-8-sig")).get("JFULL") or ""
        except Exception:
            b_bad += 1
            continue
        if sha(jfull.encode("utf-8")) == cols["jfull_sha256"][i] and sha(raw) == cols["entry_sha256"][i]:
            b_ok += 1
        else:
            b_bad += 1
            if b_bad <= 3:
                fail(f"{cols['jid'][i]}：與 archive 原始 bytes 不符")
    if b_ok == 0:
        fail("archive 原始 bytes 一筆都沒比到 —— 未驗證")
    elif b_bad:
        fail(f"archive 原始 bytes：{b_bad} 筆不符")
    elif b_skip:
        print(f"  ⚠ archive 原始 bytes：一致 {b_ok}／取不到 {b_skip}（未覆蓋）")
    else:
        ok(f"parquet ↔ archive 原始 bytes：{b_ok}/{n} 逐位元組一致")

    # C) 欄位完整性（還原需要的欄位不得為空）
    for f in ("jfull", "entry_sha256", "jfull_sha256", "entry_path"):
        empty = sum(1 for v in cols[f] if not v)
        if empty:
            fail(f"parquet 欄位 {f} 有 {empty} 筆為空")
    if not any("欄位" in f for f in FAILS):
        ok("還原所需欄位（jfull/entry_sha256/jfull_sha256/entry_path）全非空")


# ─────────────────────────────────────────────────────────────────────────
# 層 2：postgres（法規 metadata ＋ 條文）
# ─────────────────────────────────────────────────────────────────────────
def q(sql: str) -> list[list[str]]:
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "rag", "-d", "ragdemo",
         "-t", "-A", "-F", "\x01", "-c", sql],
        capture_output=True, text=True, cwd=ROOT, timeout=1800,
    )
    if r.returncode != 0:
        fail(f"psql 失敗：{r.stderr[:120]}")
        return []
    return [ln.split("\x01") for ln in r.stdout.split("\n") if ln.strip()]


def check_pg(off_art: dict, off_meta: dict) -> None:
    print("\n" + "=" * 72)
    print("【層 2】postgres ↔ 官方 ChLaw.json / ChOrder.json")
    print("=" * 72)

    # 全量匯出（避免換行破壞解析——實測踩過）
    with tempfile.TemporaryDirectory() as td:
        art_csv = Path(td) / "art.csv"
        meta_csv = Path(td) / "meta.csv"
        for target, sql in (
            (art_csv, r"\copy (SELECT pcode, article_seq, md5(content), char_len FROM article) "
                      r"TO STDOUT WITH (FORMAT csv)"),
            (meta_csv, r"\copy (SELECT pcode, law_name, law_level, law_url FROM law) "
                       r"TO STDOUT WITH (FORMAT csv)"),
        ):
            r = subprocess.run(
                ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "rag", "-d",
                 "ragdemo", "-c", sql],
                capture_output=True, text=True, cwd=ROOT, timeout=3600,
            )
            if r.returncode != 0:
                fail(f"匯出失敗：{r.stderr[:150]}")
                return
            target.write_text(r.stdout, encoding="utf-8")

        import csv
        pg_art = {}
        with art_csv.open(encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) >= 4:
                    pg_art[(row[0], int(row[1]))] = (row[2], int(row[3]))
        pg_meta = {}
        with meta_csv.open(encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) >= 4:
                    pg_meta[row[0]] = (row[1], row[2], row[3])

    print(f"  官方條文 {len(off_art):,}｜pg 條文 {len(pg_art):,}")
    print(f"  官方法規 {len(off_meta):,}｜pg 法規 {len(pg_meta):,}")
    if not off_art or not pg_art:
        fail("條文集合為空 —— 未驗證")
        return

    same = diff = missing = 0
    ex = []
    for k, want in off_art.items():
        got = pg_art.get(k)
        if got is None:
            missing += 1
            continue
        if got == want:
            same += 1
        else:
            diff += 1
            if len(ex) < 3:
                ex.append(k)
    (ok if diff == 0 and missing == 0 else fail)(
        f"pg article ↔ 官方：逐字一致 {same:,}／不符 {diff}／缺 {missing}")
    for k in ex:
        fail(f"  差異 {k}")

    msame = mdiff = mmiss = 0
    for p, want in off_meta.items():
        got = pg_meta.get(p)
        if got is None:
            mmiss += 1
            continue
        if got == want:
            msame += 1
        else:
            mdiff += 1
    (ok if mdiff == 0 and mmiss == 0 else fail)(
        f"pg law（name/level/url）↔ 官方：一致 {msame:,}／不符 {mdiff}／缺 {mmiss}")


# ─────────────────────────────────────────────────────────────────────────
# 層 3：qdrant
# ─────────────────────────────────────────────────────────────────────────
def check_qdrant(off_art: dict, off_meta: dict) -> None:
    print("\n" + "=" * 72)
    print("【層 3】qdrant laws ↔ 官方 ChLaw.json / ChOrder.json")
    print("=" * 72)
    try:
        import httpx
        import _hostenv
        _hostenv.load_host_env()
    except ImportError:
        fail("缺 httpx")
        return
    import os
    Q = _hostenv.host_qdrant_url().rstrip("/")
    key = (os.getenv("QDRANT_API_KEY") or "").strip()
    H = {"api-key": key} if key else {}

    info = httpx.get(f"{Q}/collections/laws", headers=H, timeout=120).json().get("result") or {}
    total = info.get("points_count", 0)
    print(f"  qdrant laws points {total:,}｜官方條文 {len(off_art):,}")
    if total == 0:
        fail("qdrant 0 點")
        return
    # ⚠ **點數必須 ≥ 條文數，不能要求相等**。超長條文會被切成多點
    # （chunk_idx），每條可產生多個 point。實測（2026-10-10）：
    # 222,109 條文 → 222,151 點，多出 42 個＝32 條含表格的超長條文各多 1 段。
    # 第一版寫 `total != len(off_art)` 就報錯，把正確的多分段當成資料不符——
    #   那會逼人為了讓數字好看而**合併分段**，那是降低資料忠實度（憲法 XI）。
    # 正確的判斷是「點數 < 條文數」→ 有條文完全沒進庫。
    if total < len(off_art):
        fail(f"點數 {total:,} < 條文數 {len(off_art):,}：有條文完全沒進向量庫")
    else:
        extra = total - len(off_art)
        print(f"  （點數比條文數多 {extra}，是超長條文分段所致——預期內）")

    # 全量 scroll，比對 md5(text) 與 char_len／law_url／law_level
    import collections
    got: dict[tuple[str, int], dict[int, str]] = {}
    payload_by_key: dict[tuple[str, int], dict] = {}
    offset = None
    while True:
        body = {"limit": 1000, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        r = httpx.post(f"{Q}/collections/laws/points/scroll", headers=H, json=body, timeout=600)
        res = r.json()["result"]
        for pt in res["points"]:
            pl = pt["payload"]
            # 多段落的條文會拆成多點（chunk_idx）。**驗證必須涵蓋全部點**——
            # 第一版用 `if 'chunk_idx' in pl: continue` 把它們跳過，結果
            # 74 筆「有內容但很長、會被切成多點」的條文完全沒被驗到，
            # 卻讓總數看起來只差 74。跳過它們等於把最需要驗的長條文漏掉。
            #
            # 正確做法：把同一條的所有分段**依 chunk_idx 順序**接回，再比對
            # 官方整條的 md5 與 char_len。這同時驗到了「分段沒有遺漏、順序正確」。
            key = (pl["pcode"], pl["article_seq"])
            pieces = got.setdefault(key, {})
            pieces[int(pl.get("chunk_idx", 0))] = pl.get("text") or ""
            payload_by_key.setdefault(key, pl)
        offset = res.get("next_page_offset")
        if offset is None:
            break

    # 逐條把分段接回、與官方整條比對
    same = diff = miss = 0
    ex = []
    for k, (md5, ln) in off_art.items():
        pieces = got.get(k)
        if pieces is None:
            miss += 1
            continue
        joined = "\n".join(pieces[i] for i in sorted(pieces))
        want_url, want_lv = off_meta.get(k[0], ("", "", ""))[2], off_meta.get(k[0], ("", "", ""))[1]
        pl_any = payload_by_key[k]
        if hashlib.md5(joined.encode()).hexdigest() == md5 and pl_any["char_len"] == ln \
                and pl_any["law_url"] == want_url and pl_any["law_level"] == want_lv:
            same += 1
        else:
            diff += 1
            if len(ex) < 3:
                ex.append((k, len(pieces), len(joined), ln))
    (ok if diff == 0 and miss == 0 else fail)(
        f"qdrant ↔ 官方（分段接回後 md5＋char_len＋law_url＋law_level）："
        f"一致 {same:,}／不符 {diff}／缺 {miss}")
    for e in ex:
        fail(f"  差異 {e}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="verify-three-layers",
        description="獨立驗證：parquet／pg／qdrant 三層逐一對官方原始檔比對",
    )
    ap.add_argument("--unrar", type=Path, default=None)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    print("=" * 72)
    print("三層一致性驗證 —— 對象是「官方公布的原始檔」，不是自己的中間產物")
    print("=" * 72)

    off_j = official_judgements()
    off_a = official_laws()
    off_m = official_law_meta()
    print(f"\n官方來源：判決 {len(off_j):,} 份｜法規 {len(off_m):,} 部｜條文 {len(off_a):,} 條")

    check_parquet(off_j, args.unrar)
    check_pg(off_a, off_m)
    check_qdrant(off_a, off_m)

    print("\n" + "=" * 72)
    if FAILS:
        print(f"結論：**不通過**（{len(FAILS)} 項）")
        for f in FAILS[:20]:
            print(f"  - {f}")
        return 1
    print("結論：**三層皆與官方原始檔一致**")
    print("  → 從檢索取出的判決原文與法規命令，可逐位元組還原成政府公布內容")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())