#!/usr/bin/env python3
"""M1 司法選樣解壓與 profile 建立（spec 006 / FR-001, FR-010 / INV-PROV, INV-PATH）。

## 這份模組做什麼

把 T007 已定案的 **100 條 entry path**（`specs/004-judicial-source-fidelity/
allowlist-m1.json`）讀進來、**驗證它沒有漂移**，然後（後續 task）逐筆解壓、過
schema、切塊，產出 profile、manifest 與人可讀審閱包。

本檔目前是 **Phase 1**：只有 allowlist 的載入與**離線層自檢**（`load_allowlist` /
`verify_allowlist`）。解壓、切塊、manifest 寫出由後續 task 追加。

## 為什麼「驗清單」是一件獨立的事

清單是**決策的證據**，不是推算的副產品。`scope.md` 記了選樣規則（4 院民事、
202607、`unpacked_size` 降冪、path 字典序 tiebreak），但記錄本身可能被手改、
可能被截斷、可能與檔案內容不一致。所以每次使用前都必須回答：**我手上的這份，
還是當初決策的那份？**

回答方式是重算 `entries_sha256` 並比對。這一點在做任何解壓之前就該成立——
清單漂移不該等到解壓一半才發現。

## digest 定義：**與 `selection.py` 的 `sample_digest` 不同，不可混用**

| | `selection.sample_digest` | 本檔 `entries_digest` |
|---|---|---|
| 輸入順序 | **先排序**再連接 | **保持清單順序**（已是選樣序） |
| 對象 | T006 的 494 筆抽樣 | T007 的 100 筆 allowlist |
| 結果 | `280b95bb…` | `7afade72…` |

兩者算的都是「paths 以 `\\n` 連接後 sha256」，但**排序與否不同**，因此
**同一組路徑會算出兩個不同的 digest**。這裡刻意**不重用** `sample_digest`：
它內建的排序會讓 allowlist 的 digest 與 `scope.md` 記載值對不上，而錯誤的
digest 對不上時會被誤讀成「清單漂移」——那是一個假的紅燈，比沒有紅燈更壞。

## 路徑可攜性（FR-011 / INV-PATH）

本檔**不含任何絕對路徑**，repo 內路徑一律由 `__file__` 解析（沿用 `selection.py`
的 `ROOT` 寫法）。必須讀 repo 外資料時（例如 T006 的凍結快照）路徑來自環境
變數，未設就記錄「不存在」並繼續，**不中止、不假裝存在**。

## 這份不做什麼

不解壓（那是 `extract.py` 的事，decoder 路徑必須由呼叫端提供）、不解析判決
JSON、不切塊、不寫任何產物、不碰 `data/judgements/{raw,seed}`。它只認得
「一份 allowlist」與「一份 authoritative inventory」。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import artifact as _artifact
import selection as _selection

ROOT = Path(__file__).resolve().parents[2]

#: T007（spec 004）決策的機器可讀對應物。**唯讀**。
ALLOWLIST_PATH = (
    ROOT / "specs" / "004-judicial-source-fidelity" / "allowlist-m1.json"
)

#: allowlist 的 schema 標籤。換格式就是換契約，必須明確升版（原則 IV）。
ALLOWLIST_SCHEMA = "m1-allowlist/1"

#: profile 落點（FR-005）。整個目錄進 .gitignore（`data/judgements/profile-*/`）。
PROFILE_DIR = ROOT / "data" / "judgements" / "profile-m1"

#: 凍結語料的 repo 內錨點（FR-011）。**只含 repo 相對路徑** → 任何機器可算。
SEED_DIR = ROOT / "data" / "judgements" / "seed"

#: T006 的凍結快照在 repo 外，路徑以佔位形式記錄，**絕對路徑不進版控**。
#: `ARTIFACTS_ROOT` 由環境變數提供；未設即記錄「不存在」。
EXTERNAL_SNAPSHOT_RELPATTERN = (
    "<ARTIFACTS_ROOT>/t007e05cb/out/corpus_snapshot.json"
)


class AllowlistError(RuntimeError):
    """allowlist 無法讀取或格式不符（結構壞掉，無法逐項驗證）。"""


@dataclass(frozen=True)
class Entry:
    """allowlist 裡的一筆 entry。frozen。

    `path` 是 **RAR 內的原始字面**（含反斜線）——那是 authoritative 形式，
    解壓時必須原樣使用。`path_posix` 只供顯示與比較。
    """

    path: str
    path_posix: str
    unpacked_size: int
    crc32: str

    @property
    def jid(self) -> str:
        """從 path 取出 JID（檔名去掉 `.json`）。"""
        return self.path_posix.rsplit("/", 1)[-1][:-len(".json")]


@dataclass(frozen=True)
class Allowlist:
    """一份已載入的 allowlist。frozen（`entries` 為 tuple）。"""

    schema: str
    count: int
    entries_sha256: str
    entries: tuple[Entry, ...]
    excluded_frozen: tuple[str, ...]
    artifact_sha256: str
    selection: dict

    def __len__(self) -> int:
        return len(self.entries)


def entries_digest(paths: list[str]) -> str:
    """allowlist 的 entries digest（**保持順序**，見模組 docstring 的對照表）。

    定義：`"\\n".join(paths)` → UTF-8 → SHA-256，無結尾換行。
    與 `scope.md` 記載的產生指令逐字一致。
    """
    joined = "\n".join(paths)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def load_allowlist(path: Path | None = None) -> Allowlist:
    """讀取 allowlist。

    **只負責讀取與結構檢查**；內容一致性交給 `verify_allowlist` —— 分開是為了
    讓「壞到讀不了」與「讀得懂但對不上」是兩種不同的錯誤。

    ⚠ 本函式**不讀** `scope.md` 散文、**不讀**任何 repo 外檔案（FR-001）。
    """
    p = ALLOWLIST_PATH if path is None else Path(path)
    if not p.is_file():
        raise AllowlistError(f"allowlist 不存在：{p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AllowlistError(f"allowlist 不是合法 JSON：{p}（{exc}）") from exc
    if not isinstance(raw, dict):
        raise AllowlistError(f"allowlist 頂層應為物件：{p}")

    missing = [
        k
        for k in (
            "schema",
            "artifact",
            "selection",
            "excluded_frozen_document_ids",
            "count",
            "entries_sha256",
            "entries",
        )
        if k not in raw
    ]
    if missing:
        raise AllowlistError(f"allowlist 缺欄位：{missing}")

    try:
        entries = tuple(
            Entry(
                path=e["path"],
                path_posix=e["path_posix"],
                unpacked_size=e["unpacked_size"],
                crc32=e["crc32"],
            )
            for e in raw["entries"]
        )
    except (KeyError, TypeError) as exc:
        raise AllowlistError(f"allowlist 的 entries 欄位結構不符：{exc}") from exc

    return Allowlist(
        schema=raw["schema"],
        count=raw["count"],
        entries_sha256=raw["entries_sha256"],
        entries=entries,
        excluded_frozen=tuple(raw["excluded_frozen_document_ids"]),
        artifact_sha256=raw["artifact"]["sha256"],
        selection=raw["selection"],
    )


def verify_allowlist(al: Allowlist) -> list[str]:
    """**離線層**自檢。回傳問題清單，空清單＝通過。

    兩條刻意的設計：

    1. **不提前 return** —— 一次回報全部問題。逐條 raise 會讓第一個問題擋住其餘
       的診斷，那等於把「哪幾處壞了」變成「一次只能修一處」。
    2. **不硬寫 74** —— 被排除的凍結 JID 數量是 spec 004 的事實（凍結語料的規模），
       它會隨 004 的語料變動而變。把 74 寫死在 M1 會製造**第二個真相來源**，
       而兩者一旦不一致，沒有人知道該信誰。改驗「唯一、非空、與 entries 零交集」
       加上「不少於 selection 記錄的被排除數」，這三條都是**結構性**不變式。
    """
    problems: list[str] = []

    if al.schema != ALLOWLIST_SCHEMA:
        problems.append(
            f"schema 不符：{al.schema!r} != {ALLOWLIST_SCHEMA!r}"
        )

    if al.count != len(al.entries):
        problems.append(f"count != len(entries)：{al.count} != {len(al.entries)}")

    recomputed = entries_digest([e.path for e in al.entries])
    if recomputed != al.entries_sha256:
        problems.append(
            f"entries_sha256 不符：重算 {recomputed[:16]}… != "
            f"記載 {al.entries_sha256[:16]}…（清單已漂移）"
        )

    seen: dict[str, int] = {}
    for i, e in enumerate(al.entries):
        expected_posix = _selection.forward_slash(e.path)
        if e.path_posix != expected_posix:
            problems.append(
                f"entries[{i}] path_posix != forward_slash(path)："
                f"{e.path_posix!r} != {expected_posix!r}"
            )
        if not e.path.endswith(".json"):
            problems.append(f"entries[{i}] path 結尾不是 .json：{e.path!r}")
        if len(e.crc32) != 8 or any(c not in "0123456789abcdefABCDEF" for c in e.crc32):
            problems.append(f"entries[{i}] crc32 不是 8 位 hex：{e.crc32!r}")
        if not isinstance(e.unpacked_size, int) or e.unpacked_size <= 0:
            problems.append(
                f"entries[{i}] unpacked_size 非正整數：{e.unpacked_size!r}"
            )
        seen[e.path] = seen.get(e.path, 0) + 1

    dups = sorted(p for p, n in seen.items() if n > 1)
    if dups:
        problems.append(f"entries 有重複 path：{dups[:3]}（共 {len(dups)} 筆）")

    if not al.excluded_frozen:
        problems.append("excluded_frozen_document_ids 為空")
    else:
        dup_frozen = sorted(
            {j for j in al.excluded_frozen if al.excluded_frozen.count(j) > 1}
        )
        if dup_frozen:
            problems.append(
                f"excluded_frozen 有重複：{dup_frozen[:3]}（共 {len(dup_frozen)} 筆）"
            )
        overlap = sorted({e.jid for e in al.entries} & set(al.excluded_frozen))
        if overlap:
            problems.append(
                f"entries 與 excluded_frozen 有交集：{overlap[:3]}"
                f"（共 {len(overlap)} 筆）——選樣等於沒排除凍結語料"
            )

    # 選擇參數的內部算術一致性（母體 − 被排除 = 扣除後母體）。
    sel = al.selection or {}
    total = sel.get("pool_total_4courts_civil")
    excluded_in_pool = sel.get("excluded_frozen_in_pool")
    after = sel.get("pool_after_exclusion")
    if isinstance(total, int) and isinstance(excluded_in_pool, int) and isinstance(after, int):
        if total - excluded_in_pool != after:
            problems.append(
                f"selection 算術不自洽：{total} − {excluded_in_pool} != {after}"
            )
        if len(al.excluded_frozen) < excluded_in_pool:
            problems.append(
                f"excluded_frozen 筆數 {len(al.excluded_frozen)} 少於 "
                f"selection 記錄的被排除數 {excluded_in_pool}"
            )

    if al.artifact_sha256 != _artifact.ARTIFACT_SHA256:
        problems.append(
            f"artifact sha256 不符：allowlist {al.artifact_sha256[:16]}… != "
            f"artifact.py 釘死值 {_artifact.ARTIFACT_SHA256[:16]}…"
        )

    return problems
