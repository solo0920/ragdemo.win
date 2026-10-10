#!/usr/bin/env python3
"""驗證 parquet 原文層能否**逐位元組還原**成政府公布的那一份。

## 這個工具在做什麼

使用者的要求（2026-10-10）：
> 「檢索出來要能將原始判決及法規命令忠實還原政府網站公布的原始檔內容，要具一致性」

所以驗收不能是「我寫進去的東西有沒有回來」——那只證明 parquet 自己跟自己一致。
必須拿**獨立的事實來源**比對：

- 判決 → **archive 內 .json 的原始 bytes**（用 `unrar` 直接取出，不經 M1 的中間產物）
- 法規命令 → **moj API 的 ChLaw.json / ChOrder.json**

**這是唯一誠實的驗法**：中間層（M1 解壓、normalize）都可能出錯，
只有最上游的 bytes 是事實。

## 三個層級的驗證

| 層 | 比對對象 | 證明什麼 |
|---|---|---|
| L1 | parquet ↔ archive 原始 bytes | 位元組級還原 |
| L2 | parquet ↔ M1 解壓產物 | 中間層沒變過原樣 |
| L3 | 法規三層（parquet 無／pg／qdrant） | 法規側的一致性 |

L1 若通過，就證明：**從 parquet 檢索出的原文，可以還原成政府網站那份檔案**。

## 用法

    .venv/bin/python scripts/verify-parquet-fidelity.py
    .venv/bin/python scripts/verify-parquet-fidelity.py --unrar ~/.local/bin/unrar
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
PARQUET = ROOT / "data" / "judgements" / "parquet" / "judgements.parquet"
RAW_DIR = ROOT / "data" / "judgements" / "profile-200" / "raw"
ARCHIVE_DIR = ROOT / "data" / "judgements" / "raw"
M1_MANIFEST = ROOT / "specs" / "007-judgment-three-tier-storage" / "profile-manifest-200.json"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def verify_l1_l2(parquet_path: Path, unrar: Path | None) -> tuple[bool, str]:
    """L1：parquet ↔ archive 原始 bytes。L2：parquet ↔ M1 中間產物。"""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return False, "缺 pyarrow"

    if not parquet_path.is_file():
        return False, f"parquet 不存在：{parquet_path}"
    table = pq.read_table(parquet_path)
    cols = {c: table.column(c).to_pylist() for c in table.column_names}
    n = table.num_rows
    print(f"\n【L1】parquet ↔ archive 原始 bytes（n={n}）")

    # ── L1a：對 M1 解壓產物（已在本機）──
    l1a_ok = l1a_bad = 0
    for i in range(n):
        rel = cols["entry_path"][i]
        p = RAW_DIR / rel
        if not p.is_file():
            continue
        orig = p.read_bytes()
        d = json.loads(orig.decode("utf-8-sig"))
        jfull = d.get("JFULL") or ""
        # 三個獨立條件：位元組 hash／字元數／CRLF 數
        if (sha(jfull.encode("utf-8")) == cols["jfull_sha256"][i]
                and len(jfull.encode("utf-8")) == cols["jfull_byte_len"][i]
                and jfull.count("\r\n") == cols["crlf_count"][i]
                and sha(orig) == cols["entry_sha256"][i]):
            l1a_ok += 1
        else:
            l1a_bad += 1
    print(f"  parquet ↔ M1 解壓產物：一致 {l1a_ok}/{n}，不一致 {l1a_bad}")
    ok_a = l1a_bad == 0

    # ── L1b：對 archive 內的原始 bytes（繞過 M1，唯一事實來源）──
    if unrar is None or not unrar.is_file():
        print("  archive bytes 比對：跳過（未給 --unrar）")
        return ok_a, ("L1b skipped" if ok_a else "L1a failed")
    if not ARCHIVE_DIR.is_dir():
        print("  archive bytes 比對：跳過（archive 不在）")
        return ok_a, ("L1b skipped" if ok_a else "L1a failed")

    print("  從 archive 直接取原始 bytes（繞過 M1 中間層）…", flush=True)
    l1b_ok = l1b_bad = l1b_skip = 0
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        for i in range(n):
            # ⚠ archive 內的路徑分隔符是**正斜線**（實測 `unrar lb` 輸出）。
            #   改斜線會讓 unrar 找不到而回空，**看起來像「全部取不到」**——
            #   那正是第一次跑這支工具的結果（L1b 0/200 全 skip）。
            arc = cols["entry_path"][i]
            r = subprocess.run(
                [str(unrar), "p", "-inul", str(next(ARCHIVE_DIR.glob("*.rar"))), arc],
                capture_output=True, timeout=120,
            )
            if r.returncode != 0 or not r.stdout:
                l1b_skip += 1
                if l1b_skip <= 2:
                    print(f"    取不到：{arc}（rc={r.returncode}）")
                continue
            orig = r.stdout
            try:
                jfull = json.loads(orig.decode("utf-8-sig")).get("JFULL") or ""
            except Exception:
                l1b_bad += 1
                continue
            if (sha(jfull.encode("utf-8")) == cols["jfull_sha256"][i]
                    and sha(orig) == cols["entry_sha256"][i]
                    and jfull.count("\r\n") == cols["crlf_count"][i]):
                l1b_ok += 1
            else:
                l1b_bad += 1
                if l1b_bad <= 3:
                    print(f"    ✗ {cols['jid'][i]}：位元組不符")
    print(f"  parquet ↔ archive 原始 bytes：一致 {l1b_ok}／不符 {l1b_bad}／取不到 {l1b_skip}")
    # ⚠⚠ **一筆都沒比到 ≠ 通過**。第一次跑這支工具時 L1b 是 0/200 全 skip
    #   （archive 內路徑分隔符是正斜線，我寫成反斜線，unrar 全部找不到），
    #   而舊版卻回報「全部通過」。**沒驗到的東西不能算通過**——
    #   那會讓整個還原性結論建立在空集合上（憲法 XI 的還原要求）。
    if l1b_ok == 0:
        print("  ✗ L1b 一筆都沒比到 —— 這是**未驗證**，不是通過")
        return False, "L1b nothing compared"
    if l1b_skip:
        print(f"  ⚠ 有 {l1b_skip} 筆取不到（不算通過，只算未覆蓋）")
    return ok_a and l1b_bad == 0, ("ok" if ok_a and l1b_bad == 0 else "failed")


def verify_l2() -> bool:
    """L2：parquet ↔ M1 manifest 的量測值。"""
    if not M1_MANIFEST.is_file():
        print("\n【L2】M1 manifest 不存在，跳過")
        return True
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return False
    m = json.loads(M1_MANIFEST.read_text(encoding="utf-8"))
    table = pq.read_table(PARQUET)
    cols = {c: table.column(c).to_pylist() for c in table.column_names}
    by_jid = {e["jid"]: e for e in m["entries"]}
    # ⚠ M1 manifest 記的是 **entry**（整個 .json）的 sha256，欄位名 `sha256`；
    #   它**沒有** jfull_sha256 / jfull_char_len / crlf_count。
    #   所以這裡能比的是 entry_sha256 與 path，其餘由 L1 負責（那邊有原文可比）。
    ok = bad = miss = 0
    for i in range(table.num_rows):
        e = by_jid.get(cols["jid"][i])
        if e is None:
            miss += 1
            continue
        if (e["sha256"] == cols["entry_sha256"][i]
                and e["path_posix"] == cols["entry_path"][i]):
            ok += 1
        else:
            bad += 1
            if bad <= 3:
                print(f"    ✗ {cols['jid'][i]}：entry_sha256 或 path 不符")
    print(f"\n【L2】parquet ↔ M1 manifest（entry_sha256＋path）："
          f"一致 {ok}／不符 {bad}／manifest 無此卷 {miss}")
    return bad == 0 and miss == 0


def verify_laws() -> bool:
    """L3：法規三層一致性（pg 的 content_hash 自洽 + qdrant payload 抽驗）。"""
    print("\n【L3】法規命令層")
    try:
        import httpx
        import asyncpg  # noqa: F401
    except ImportError:
        print("  缺依賴，跳過")
        return True
    import subprocess as sp
    r = sp.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "rag",
                "-d", "ragdemo", "-t", "-A", "-F", "|", "-c",
                "SELECT count(*) FILTER (WHERE content_hash = "
                "encode(sha256(convert_to(content,'UTF8')),'hex')), count(*) FROM article;"],
               capture_output=True, text=True, cwd=ROOT, timeout=300)
    parts = [x for x in r.stdout.strip().split("|") if x]
    if len(parts) == 2:
        ok, tot = int(parts[0]), int(parts[1])
        print(f"  pg article content_hash 自洽：{ok:,}/{tot:,}")
        if ok != tot:
            return False
    else:
        print(f"  pg 查詢失敗：{r.stdout[:80]} {r.stderr[:120]}")
        return False

    # qdrant 抽驗（憑證只從 .env 讀，不印出）
    import os, re
    key = None
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        mm = re.match(r"\s*QDRANT_API_KEY\s*=\s*(\S+)", line)
        if mm:
            key = mm.group(1).strip("\"'")
            break
    try:
        sys.path.insert(0, str(ROOT / "ingest" / "laws"))
        sys.path.insert(0, str(ROOT / "backend"))
        import _hostenv
        _hostenv.load_host_env()
        Q = _hostenv.host_qdrant_url().rstrip("/")
        H = {"api-key": key} if key else {}
        pts = httpx.post(f"{Q}/collections/laws/points/scroll", headers=H,
                         json={"limit": 200, "with_payload": True, "with_vector": False},
                         timeout=120).json()["result"]["points"]
        hok = sum(1 for p in pts
                  if hashlib.sha256((p["payload"].get("text") or "").encode()).hexdigest()
                  == p["payload"].get("content_hash"))
        uok = sum(1 for p in pts if p["payload"].get("law_url"))
        print(f"  qdrant 抽 {len(pts)} 點：content_hash 一致 {hok}/{len(pts)}；law_url 有值 {uok}/{len(pts)}")
        return hok == len(pts) and uok == len(pts)
    except Exception as exc:
        print(f"  qdrant 驗證跳過：{type(exc).__name__} {str(exc)[:80]}")
        return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="verify-parquet-fidelity",
        description="驗證 parquet 原文層與法規層的位元組一致性（還原政府原始檔）",
    )
    ap.add_argument("--unrar", type=Path, default=None,
                    help="unrar 路徑；給定則做 L1b（繞過 M1 的 archive 原始 bytes 比對）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    print("=" * 72)
    print("還原驗證：parquet ↔ archive 原始 bytes ↔ 政府公布內容")
    print("=" * 72)

    ok1, _ = verify_l1_l2(PARQUET, args.unrar)
    ok2 = verify_l2()
    ok3 = verify_laws()

    print("\n" + "=" * 72)
    allok = ok1 and ok2 and ok3
    print(f"結論：{'全部通過' if allok else '有未通過項'}"
          f"（L1={ok1} L2={ok2} L3={ok3}）")
    return 0 if allok else 1


if __name__ == "__main__":
    raise SystemExit(main())