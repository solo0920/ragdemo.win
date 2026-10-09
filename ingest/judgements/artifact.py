#!/usr/bin/env python3
"""司法判決 raw artifact 的身分與完整性（spec 004 FR-007/FR-008）。

## 這份檔案做什麼

把 `data/judgements/raw/` 底下的 RAR artifact 的**身分釘成程式碼裡的常數**，
而不是讓它變成「檔案系統剛好長這樣」。後面每一個 stage 在讀它之前都會先
`verify()`；digest 不符就中止。

## 為什麼要 pin

三份調查（decoder-proof.md / corpus-schema-survey.md / citation-pattern-survey.md）
全程都在比對同一組值：

    sha256 = ef35ce4401d7d751bbe7cbeab93cb92949f5971a26947cbde80c64e9722fea4c
    bytes  = 297279556

那三份調查之間的每個數字（108,547 entries、3,967 bytes、SHA-256 65cd7d32…）
都只在「artifact 還是這一份」的前提下有意義。digest 變了，結論就全部作廢，
而且**不會有人發現** —— 因為程式照樣跑得動，只是餵進另一批資料。

## raw/ 是唯讀的

`assert_outside_raw()`（T002）把這件事變成結構性保證而不是紀律。任何 stage
想把東西寫進 `raw/` 都會拋錯，而不是靜靜地把 authoritative source 蓋掉。

## 這份不做什麼

不選 decoder（T003 只記錄已證明的 configuration）、不解壓（T005）、不碰
inventory（T004）。本檔案只回答「這是不是我們認定的那一份 artifact」。

## Provenance

    fileSetId  : 70691
    source     : https://opendata.judicial.gov.tw/api/FilesetLists/70691/file
    acquisition: **人工下載**（該 API 對所有 RAR fileset 回 HTTP 500，見 plan.md §4）
    container  : RAR 4
    acquired   : 2026-10-07
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ── 被釘死的 artifact 身分 ─────────────────────────────────────────────────
#
# 這三個值是「已證明」不是「已猜測」：見 decoder-proof.md §1，每一個 investigation
# 階段結束前都重新比對過一次，三次一致。改動任何一個都應該被當成
# 「換了一份資料」，不是「更新了一下常數」。
ARTIFACT_FILENAME = "202607--(20260916Update).rar"
ARTIFACT_SHA256 = "ef35ce4401d7d751bbe7cbeab93cb92949f5971a26947cbde80c64e9722fea4c"
ARTIFACT_SIZE_BYTES = 297279556

FILESET_ID = 70691
ACQUIRED_ON = "2026-10-07"

RAW_DIR = ROOT / "data" / "judgements" / "raw"
ARTIFACT_PATH = RAW_DIR / ARTIFACT_FILENAME


class ArtifactIntegrityError(RuntimeError):
    """artifact 的 digest／大小與釘死值不符，或 artifact 不存在。"""


@dataclass(frozen=True)
class ArtifactIdentity:
    """一個 artifact 的身分。frozen —— 身分不該在程式中被改寫。"""

    name: str
    size_bytes: int
    sha256: str

    def as_dict(self) -> dict:
        return {"name": self.name, "size_bytes": self.size_bytes, "sha256": self.sha256}


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """檔案的 SHA-256 hex digest。

    分塊讀取：artifact 是 283 MB，整份讀進記憶體沒有理由。
    與 `ingest/laws/sync_daily.py::sha256_bytes` 同演算法，但那份吃 bytes、
    這份吃 path —— inventory (T004) 要對整個目錄算 hash，用 bytes 版不合用。
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def identify(path: Path | None = None) -> ArtifactIdentity:
    """回傳 artifact 的身分（name/size/sha256），**不**與釘死值比對。

    分離是刻意的：`identify()` 回答「這是什麼」，`verify()` 回答「這是不是
    我們要的那一份」。合在一起會讓「記錄一個陌生 artifact 的身分」也拋錯，
    而那在診斷「上游換檔了」時正是我們要的能力。
    """
    p = ARTIFACT_PATH if path is None else Path(path)
    if not p.is_file():
        raise ArtifactIntegrityError(f"artifact 不存在：{p}")
    return ArtifactIdentity(
        name=p.name,
        size_bytes=p.stat().st_size,
        sha256=sha256_file(p),
    )


def verify(path: Path | None = None) -> ArtifactIdentity:
    """確認 artifact 與釘死值一致；不一致就拋 ArtifactIntegrityError。

    **大小與 digest 都查。** 只查其中一個都留了破口：大小相同而內容被換過是
    換檔時最常見的情況（同一份月度資料重新發佈），只比大小會完全漏掉。
    """
    ident = identify(path)
    problems = []
    if ident.size_bytes != ARTIFACT_SIZE_BYTES:
        problems.append(f"size {ident.size_bytes} != 釘死值 {ARTIFACT_SIZE_BYTES}")
    if ident.sha256 != ARTIFACT_SHA256:
        problems.append(f"sha256 {ident.sha256[:16]}… != 釘死值 {ARTIFACT_SHA256[:16]}…")
    if problems:
        where = ARTIFACT_PATH if path is None else path
        raise ArtifactIntegrityError(
            f"artifact 身分不符（{where.name}）：" + "；".join(problems)
        )
    return ident


def provenance() -> dict:
    """回傳 acquisition provenance。

    這份記錄會隨 derived corpus 一起存下去（spec FR-008）：日後要能回答
    「這批資料是哪一天、用什麼方式、從哪裡拿到的」。
    """
    return {
        "fileset_id": FILESET_ID,
        "source_url": f"https://opendata.judicial.gov.tw/api/FilesetLists/{FILESET_ID}/file",
        "acquisition": "manual-download",
        "acquired_on": ACQUIRED_ON,
        "container": "rar4",
        "artifact_name": ARTIFACT_FILENAME,
        "artifact_sha256": ARTIFACT_SHA256,
        "artifact_size_bytes": ARTIFACT_SIZE_BYTES,
    }


# ── T002：raw/ 寫入防護 ──────────────────────────────────────────────────────
#
# authoritative source 必須**結構上**不可寫，而不是靠紀律。spec 004 FR-002
# 與 FR-006 說的是同一件事：原始位元組不得被動。任何 stage 只要能改寫
# `raw/`，整個 feature 的所有 invariant 就都只是建議。
#
# 這裡用「解析後的真實路徑」判斷，而不是字串比對：`data/judgements/raw/../raw/x`
# 這個寫法字串不等於 raw/，但它確實寫進去了。只擋字串的版本會被一句話繞過。

def _is_within(child: Path, parent: Path) -> bool:
    """child 是否在 parent 之下（含 parent 本身）。

    用 `relative_to()` 而非 `str.startswith()`：後者會把
    `raw_backup/` 誤判成在 `raw/` 之下 —— 那是一個真實會發生的資料夾名
    （很多備份工具用 `_bak`／`_backup` 後綴）。
    """
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def assert_outside_raw(path: Path, *, raw_dir: Path | None = None) -> Path:
    """確保 `path` 不在 `raw/` 之下；是就拋錯。回傳解析後的路徑。

    用於每一個要寫檔的 stage，**寫之前**呼叫。回傳 resolved path 讓呼叫端
    直接拿它去寫，不必再自己 resolve 一次（那會是第二個可能被繞過的地方）。
    """
    raw = (RAW_DIR if raw_dir is None else Path(raw_dir)).resolve()
    resolved = Path(path).resolve()

    if _is_within(resolved, raw):
        raise ArtifactIntegrityError(
            f"拒絕寫入 authoritative source：{resolved} 位於 {raw} 之下。"
            "raw/ 是唯讀的；derived 資料請寫到 data/judgements/ 底下的其他位置。"
        )
    return resolved


def is_in_raw(path: Path, *, raw_dir: Path | None = None) -> bool:
    """`path` 是否位於 `raw/` 之下。唯讀查詢，供呼叫端先判斷。"""
    raw = (RAW_DIR if raw_dir is None else Path(raw_dir)).resolve()
    return _is_within(Path(path).resolve(), raw)