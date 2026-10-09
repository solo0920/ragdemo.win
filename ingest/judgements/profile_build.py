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
from dataclasses import dataclass, field
from pathlib import Path

import artifact as _artifact
import chunk as _chunk
import decoder as _decoder
import document as _document
import extract as _extract
import inventory as _inventory
import schema as _schema
import selection as _selection
import text as _text

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


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2／3：對庫層自檢、逐筆解壓、schema、切塊
# ═══════════════════════════════════════════════════════════════════════════

#: 逐階段計數的固定順序（FR-004／plan.md 契約②）。**順序有意義**：它是管線的
#: 真實順序，數字必須能互相比較（`chunked ≤ schema_valid ≤ size_crc_ok ≤
#: allowlisted`）。
STAGES = ("extracted", "size_crc_ok", "schema_valid", "chunked")

#: 解壓目的地**必須**在 raw/ 之外（T002 既有約束，沿用 `artifact.assert_outside_raw`，
#: 不重寫一份）。這裡在呼叫前就先擋，錯誤訊息會比 extract 深處的清楚。
PROFILE_RAW_SUBDIR = "raw"


def verify_selection_against_archive(
    al: Allowlist, archive: Path | None = None
) -> list[str]:
    """**對庫層**自檢（FR-010b）：由 archive 重算選樣並比對 allowlist。

    這是「清單確實是那個選樣規則算出來的」的外部證據，與 `verify_allowlist` 的
    內部自洽互補：後者證明清單自己一致，前者證明清單對得上那份 bytes。

    比對三樣東西，缺一不可：

    1. `entries_sha256`（順序敏感的整體 digest）
    2. 每筆的 `unpacked_size`
    3. 每筆的 `crc32`（%08X）——只比 path 的話，**換了內容也看不出來**

    需要持有真實 artifact；沒有時呼叫端應誠實 skip，不該假裝通過。
    """
    problems: list[str] = []
    arch = _artifact.ARTIFACT_PATH if archive is None else Path(archive)
    if not arch.is_file():
        return [f"artifact 不存在：{arch}"]

    entries = [e for e in _inventory.inventory(arch) if not e.is_dir]
    want_courts = set(al.selection.get("courts", []))
    frozen = set(al.excluded_frozen)

    def court_of(p: str) -> str:
        parts = _selection.forward_slash(p).split("/")
        return parts[1] if len(parts) > 1 else parts[0]

    def jid_of(p: str) -> str:
        return _selection.forward_slash(p).rsplit("/", 1)[-1][:-len(".json")]

    pool = [
        e
        for e in entries
        if court_of(e.path) in want_courts and jid_of(e.path) not in frozen
    ]
    # 排序定義來自 scope.md（FR-001）：size 降冪、path 字典序 tiebreak。
    pool.sort(key=lambda e: (-e.unpacked_size, e.path))
    sel = pool[: len(al.entries)]

    if len(sel) != len(al.entries):
        problems.append(f"重算筆數不符：{len(sel)} != {len(al.entries)}")
        return problems

    recomputed = entries_digest([e.path for e in sel])
    if recomputed != al.entries_sha256:
        problems.append(
            f"重算 entries_sha256 不符：{recomputed[:16]}… != "
            f"記載 {al.entries_sha256[:16]}…（選樣結果已漂移）"
        )

    by_path = {e.path: e for e in entries}
    for i, want in enumerate(al.entries):
        got = by_path.get(want.path)
        if got is None:
            problems.append(f"entries[{i}] 在 archive 中不存在：{want.path}")
            continue
        if got.unpacked_size != want.unpacked_size:
            problems.append(
                f"entries[{i}] unpacked_size 不符：{want.unpacked_size} != "
                f"{got.unpacked_size}"
            )
        got_crc = "%08X" % got.crc32
        if got_crc != want.crc32:
            problems.append(
                f"entries[{i}] crc32 不符：allowlist {want.crc32} != archive {got_crc}"
            )

    return problems


@dataclass
class EntryResult:
    """一筆 entry 走完管線的結果。

    每個階段一個旗標，失敗時記 `fail = (stage, reason)` —— **不中斷整批**
    （FR-001／INV-COUNT）。分開存旗標而不是只存最終狀態，是為了讓
    「解壓成功但 schema drift」與「解壓就失敗」在 manifest 裡是兩種看得見的東西。
    """

    entry: Entry
    extracted: bool = False
    size_crc_ok: bool = False
    schema_valid: bool = False
    chunked: bool = False
    sha256: str | None = None
    crc32_actual: str | None = None
    dest_path: Path | None = field(default=None, repr=False)
    doc: object | None = field(default=None, repr=False)
    chunks: tuple = field(default=(), repr=False)
    fail: tuple[str, str] | None = None

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def boundary_kind_histogram(self) -> dict[str, int]:
        hist: dict[str, int] = {}
        for c in self.chunks:
            key = getattr(c.boundary_kind, "value", str(c.boundary_kind))
            hist[key] = hist.get(key, 0) + 1
        return hist

    @property
    def forced_break_chunk_indexes(self) -> tuple[int, ...]:
        """哪些 chunk 的**起點**是強制斷點。

        這是 S2 hunting 的唯一訊號來源，所以定義寫死在這裡：FORCED 表示
        「上一段找不到自然邊界，被硬切」，因此斷點落在**該 chunk 的開頭**。
        第 0 個 chunk 沒有前一段，故不算。
        """
        out = []
        for c in self.chunks:
            kind = getattr(c.boundary_kind, "value", str(c.boundary_kind))
            if kind == "forced" and c.chunk_index > 0:
                out.append(c.chunk_index)
        return tuple(out)


def seed_tree_sha256(seed_dir: Path | None = None) -> str:
    """`data/judgements/seed/` 的 tree digest（FR-011／機器無關）。

    定義：`sorted(相對路徑 + "\\0" + 檔案 sha256)` 以 `\\n` 連接後 sha256。

    **只含 repo 相對路徑** —— 這是它能取代「某台機器上的快照 sha」的原因：
    任何 clone、任何機器、任何使用者帳號都算得出同一個值。
    """
    d = SEED_DIR if seed_dir is None else Path(seed_dir)
    if not d.is_dir():
        raise AllowlistError(f"seed 目錄不存在：{d}")
    rows = []
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(d).as_posix()
        rows.append(f"{rel}\0{hashlib.sha256(p.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def external_snapshot_fingerprint(snapshot: Path | None = None) -> dict:
    """repo 外的 T006 凍結快照指紋（FR-011③）。

    路徑**由呼叫端提供** —— 沿用 `extract.py` 對 decoder 的既有原則：不搜尋
    PATH、不假設存在、不設環境變數預設值（那是 T003 就定下的：自行搜尋只會讓
    同一段程式在不同機器上行為不同）。

    ⚠ 這一條是實測換來的：原本用 `ARTIFACTS_ROOT` 環境變數，結果被
    `env-audit` 的「從程式碼反查」抓到——快照缺了這個鍵、CI 兩條測試紅燈。
    那個紅燈是**對的**：憑空多一個環境變數就是憑空多一個跨機不一致的來源。
    改成 caller 傳入之後，不需要任何宣告，跨機行為也一樣明確。

    欄位**恆存在**：找不到就 `present: false` / `sha256: null`，不省略。
    為什麼要有這個欄位：讓「持有快照的機器」與「沒有的機器」產出的 manifest
    **仍然可比較** —— 前者多一個 sha 可供比對，後者明確宣告自己沒有，而不是
    兩邊欄位不同就看不出差異在哪。
    """
    pattern = EXTERNAL_SNAPSHOT_RELPATTERN
    if snapshot is None:
        return {"pattern": pattern, "present": False, "sha256": None}
    p = Path(snapshot)
    if not p.is_file():
        return {"pattern": pattern, "present": False, "sha256": None}
    return {
        "pattern": pattern,
        "present": True,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
    }


def process_entry(
    entry: Entry,
    *,
    profile_dir: Path | None = None,
    decoder: Path | None = None,
    archive: Path | None = None,
) -> EntryResult:
    """跑完一筆 entry 的整條管線：解壓 → CRC → schema → chunk。

    任一階段失敗就記 `fail=(stage, reason)` 並回傳，**不丟例外、不影響其他筆**
    （FR-001／Edge Cases）。唯一會往外拋的是「環境不對」（例如目的地在 raw/ 之下），
    因為那是整輪的設定錯誤，不是這一筆的資料問題。
    """
    base = PROFILE_DIR if profile_dir is None else Path(profile_dir)
    dest = base / PROFILE_RAW_SUBDIR
    res = EntryResult(entry=entry)

    # 環境層：目的地不得在 raw/ 之下（T002）。先擋，錯誤訊息更清楚。
    _artifact.assert_outside_raw(dest)

    try:
        ex = _extract.extract_one(
            entry.path, archive=archive, dest=dest, decoder=decoder
        )
    except Exception as exc:  # noqa: BLE001 — 逐筆記錄，不是吞掉
        res.fail = ("extracted", f"{type(exc).__name__}: {exc}")
        return res
    res.extracted = True
    res.sha256 = ex.sha256
    res.crc32_actual = "%08X" % ex.crc32
    res.dest_path = ex.destination

    return _run_stages(res, ex.data)


def _run_stages(res: EntryResult, data: bytes) -> EntryResult:
    """從**已取得的 bytes** 繼續跑：size/CRC 比對 → schema → chunk。

    拆開這一段的理由不是好看，是**可測性**（原則 V）：解壓之後的三個階段不必
    靠 archive 就能驗證單元測試（餵 bytes 即可），而 `extract` 那一段則用
    `tests/fixtures/judgements/fixture.rar` 的 store entry 測 —— 兩邊都不需要
    那 284MB 的 artifact。
    """
    entry = res.entry

    # size + CRC32：allowlist 記的是 archive header 的值，解出來的是實際值。
    # 兩者都對得上才算過 —— 只比其中一邊會漏掉「header 本身是壞的」。
    size = len(data)
    if size != entry.unpacked_size:
        res.fail = (
            "size_crc_ok",
            f"size 不符：allowlist {entry.unpacked_size} != 解壓 {size}",
        )
        return res
    if res.crc32_actual != entry.crc32:
        res.fail = (
            "size_crc_ok",
            f"crc32 不符：allowlist {entry.crc32} != 解壓 {res.crc32_actual}",
        )
        return res
    res.size_crc_ok = True

    # schema（FR-003）。Drift 記錄，不靜默丟棄。
    parsed = _schema.parse_and_validate(data)
    if not parsed.valid:
        res.fail = ("schema_valid", _describe_drift(parsed))
        return res
    res.schema_valid = True

    doc = _document.from_extracted(
        _ShimExtracted(res.entry.path, data), entry_path=res.entry.path
    )
    res.doc = doc

    # chunk（FR-004）。**不傳 chunk_size** —— 用凍結的預設值；調參數屬改
    # 行為，必須停下回報（憲法 X），不是這裡能悄悄決定的。
    chunks = _chunk.chunk_text(
        _text.LosslessText.from_string(doc.jfull),
        source_document=res.entry.path_posix,
        jid=doc.jid,
    )
    # 重組斷言（INV-SUB）：每個 chunk 必須逐字等於原文切片。這是「切塊沒有改寫
    # 原文」的機器證明，不是相信 chunker 的註解。
    for c in chunks:
        if c.text != doc.jfull[c.start_offset : c.end_offset]:
            res.fail = (
                "chunked",
                f"chunk {c.chunk_index} 重組不一致（INV-SUB 被違反）",
            )
            return res
    res.chunks = tuple(chunks)
    res.chunked = True
    return res


@dataclass(frozen=True)
class _ShimExtracted:
    """給 `document.from_extracted` 的最小介面。

    `from_extracted` 實際讀的是 `.data` / `.path` / `.sha256`（後者進 provenance）。
    刻意不去 subclass `extract.ExtractedEntry`：那是 dataclass，而我們手上沒有
    一個真的解壓結果（bytes 是外部餵進來的）。這個 shim 只滿足契約，不擴張它 ——
    若 `from_extracted` 哪天多讀一個欄位，這裡會在測試中立刻爆掉，那正是
    「契約變了要有人知道」的樣子。
    """

    path: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 5：產出物（manifest／審閱包／結論檔）
# ═══════════════════════════════════════════════════════════════════════════

MANIFEST_SCHEMA = "m1-profile-manifest/1"

#: 審閱包裡 `JFULL` 的邊界標記。為什麼用 HTML 註解而不是 code fence：
#: fence 的內容若含 ``` 就會提前收尾，而**逐字**是硬要求（憲法 XI），
#: 不能因為排版方便就讓原文被截斷。註解在渲染後不可見，但測試可以精確取出
#: 兩行之間的位元組並與原文逐位元組比對。
#:
#: ⚠ **CRLF 陷阱（2026-10-09 實測才發現）**：Marker 必須與原文**同一行**，
#: 前後不得插入換行。這些判決的 `JFULL` 含 **CRLF**；若 marker 單獨成行，
#: Markdown 檔裡就會多出 `BEGIN\n` 與 `\nEND` 這兩處行邊界，取出的字串會
#: 比原文多 2 個 `\n`、少掉原文結尾的 `\r` —— **看起來只差兩個字元，實際
#: 是逐字性被破壞**。所以 marker 直接黏在原文頭尾，中間不換行。
JFULL_BEGIN = "<!-- JFULL:BEGIN -->"
JFULL_END = "<!-- JFULL:END -->"


def extract_jfull_section(bundle: str, *, occurrence: int = 0) -> str:
    """從審閱包取出第 `occurrence` 個 `JFULL` 區段的**逐字**內容。

    這是 T110「`JFULL` 逐字」的機器驗證入口：取出結果必須 `==` 原始 `JFULL`，
    一個字元都不差。它同時是 CRLF 陷阱的守門人 —— 若有人日後把 marker 改成
    單獨成行，這裡就會紅。
    """
    begins = []
    pos = 0
    while True:
        i = bundle.find(JFULL_BEGIN, pos)
        if i < 0:
            break
        begins.append(i)
        pos = i + 1
    if len(begins) <= occurrence:
        raise ValueError(f"審閱包只有 {len(begins)} 個 JFULL 區段，取不到第 {occurrence} 個")
    start = begins[occurrence] + len(JFULL_BEGIN)
    end = bundle.find(JFULL_END, start)
    if end < 0:
        raise ValueError("JFULL 區段沒有結束標記（內容被截斷？）")
    return bundle[start:end]

VERDICTS_HEADER = (
    "| jid | 判定（正例／負例） | 理由 | 審閱者 | 日期 |\n"
    "|---|---|---|---|---|\n"
)


def _module_sha256(module) -> str:
    return hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()


def process_all(
    al: Allowlist,
    *,
    profile_dir: Path | None = None,
    decoder: Path | None = None,
    archive: Path | None = None,
    limit: int | None = None,
) -> list[EntryResult]:
    """跑完 allowlist 裡的每一筆（FR-001）。

    `limit` 只給「先跑幾筆看看」用；它**不是**補位機制，也不改變通過率的分母
    ——分母永遠是實際處理的筆數，而 manifest 會同時記錄 `allowlist.count`，
    兩者並列就看得見「只跑了 10 筆」而不是「只有 10 筆」。
    """
    entries = al.entries if limit is None else al.entries[:limit]
    return [
        process_entry(e, profile_dir=profile_dir, decoder=decoder, archive=archive)
        for e in entries
    ]


def build_manifest(
    results: list[EntryResult],
    al: Allowlist,
    *,
    decoder: Path | None = None,
    external_snapshot: Path | None = None,
    limited: bool = False,
) -> dict:
    """組出 manifest（契約②，plan.md）。

    三條刻意的取捨：

    1. **不存絕對路徑**（含 `decoder`）—— 只記 `decoder_used` 的**檔名**與
       版本／來源 sha。絕對路徑進版控會讓 manifest 在別台機器上看起來「不對」
       （FR-011④）。
    2. **失敗逐筆記錄，不中斷**（FR-001／INV-COUNT）。
    3. **counts 的分母是實際處理筆數**，並與 `allowlist.count` 並列——所以
       「只跑了前 10 筆」不會被誤讀成「只有 10 筆通過」。
    """
    counts = {
        "allowlisted": len(results),
        "allowlist_total": al.count,
        "limited": limited,
    }
    for stage in STAGES:
        counts[stage] = sum(1 for r in results if getattr(r, stage))

    entries_out = []
    for r in results:
        row = {
            "jid": r.entry.jid,
            "path": r.entry.path,
            "path_posix": r.entry.path_posix,
            "size": r.entry.unpacked_size,
            "sha256": r.sha256,
            "crc32_expected": r.entry.crc32,
            "crc32_actual": r.crc32_actual,
            "stages": {s: bool(getattr(r, s)) for s in STAGES},
            "chunk_count": r.chunk_count if r.chunked else 0,
            "boundary_kind_histogram": r.boundary_kind_histogram if r.chunked else {},
            "forced_break_chunk_indexes": list(r.forced_break_chunk_indexes),
            "fail": (
                {"stage": r.fail[0], "reason": r.fail[1]} if r.fail else None
            ),
        }
        entries_out.append(row)

    forced_docs = [r for r in results if r.forced_break_chunk_indexes]

    import datetime as _dt

    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "allowlist": {
            "sha256": al.entries_sha256,
            "count": al.count,
        },
        "artifact": {"sha256": al.artifact_sha256},
        "decoder": {
            "version": _decoder.DECODER_VERSION,
            "source_url": _decoder.SOURCE_URL,
            "source_sha256": _decoder.SOURCE_SHA256,
            # 只記檔名與是否提供，不記絕對路徑（FR-011④）
            "binary_name": Path(decoder).name if decoder else None,
            "binary_provided": decoder is not None,
        },
        "chunker": {
            "module_sha256": _module_sha256(_chunk),
            # 未傳參＝用凍結預設值；把「沒有傳」這件事記下來，否則日後
            # 有人改成傳值時，這份 manifest 看不出差別。
            "params": {
                "chunk_size": _chunk.DEFAULT_CHUNK_SIZE,
                "passed_explicitly": False,
            },
        },
        "counts": counts,
        "forced_break_documents": len(forced_docs),
        "total_chunks": sum(r.chunk_count for r in results if r.chunked),
        "entries": entries_out,
        "frozen_corpus": {
            "seed_tree_sha256": seed_tree_sha256(),
            "external_snapshot": external_snapshot_fingerprint(external_snapshot),
        },
    }


def render_review_bundle(results: list[EntryResult]) -> str:
    """人可讀審閱包（FR-007／SC-006）。

    **單檔**、依 size 降冪（最長的卷宗最可能切出跨 chunk holding，先看它們）。

    每一節都給人「不用跑程式就能判斷」的東西：FORCED 斷點落在第幾個 chunk、
    邊界分布、以及可逐字對照的原文與 chunk 邊界表。
    """
    ok = [r for r in results if r.chunked]
    ok.sort(key=lambda r: (-r.entry.unpacked_size, r.entry.path))

    # 有 FORCED 斷點的卷排前面當索引表——**不重排正文**。正文的 size 降冪是
    # 有意義的資訊（最長的卷宗最可能切出多段），把它打亂只為了「好找」會讓
    # 排序本身失去意義。索引是另一件事：它回答「該從哪看起」。
    with_forced = [r for r in ok if r.forced_break_chunk_indexes]

    out = [
        "# M1 選樣審閱包（人可讀）",
        "",
        f"共 {len(ok)} 卷（已解壓驗證並切塊），正文依 `unpacked_size` 降冪。",
        "",
        "**這份檔怎麼用**：每節看兩件事 ——（1）`forced_break` 列出的 chunk 索引，",
        "那些位置是硬切出來的，**holding 有可能被切斷**；（2）`JFULL` 全文與下方",
        "chunk 邊界表，確認自己要的段落落在哪一塊。",
        "",
        "**判定寫到** `specs/006-m1-judicial-selection-profile/review-verdicts.md`",
        "（未審＝留空，不要刪列）。",
        "",
    ]

    if with_forced:
        out += [
            "---",
            "",
            f"## 索引：有 FORCED 斷點的 {len(with_forced)} 卷（先看這些）",
            "",
            "**只有這些卷**存在「holding 可能被切斷」的結構條件——切點是硬切的，",
            "不是找得到自然邊界才切的。**先審這批**，它們是跨 chunk 正例唯一可能",
            "出現的地方。剩下的卷結構都切得乾淨（`natural`／`exact`），",
            "除非你要看的是別的性質，否則不必逐卷看。",
            "",
            "| JID | size | chunk 數 | FORCED 處數 | 首個硬切點 |",
            "|---|---|---|---|---|",
        ]
        for r in sorted(
            with_forced, key=lambda r: (-len(r.forced_break_chunk_indexes), r.entry.jid)
        ):
            first = r.forced_break_chunk_indexes[0]
            out.append(
                f"| {r.entry.jid} | {r.entry.unpacked_size} | {r.chunk_count} "
                f"| {len(r.forced_break_chunk_indexes)} | chunk {first} "
                f"（offset {r.chunks[first].start_offset}） |"
            )
        out.append("")

    out += [
        "---",
        "",
        "## 全部卷宗（依 size 降冪）",
        "",
    ]

    for r in ok:
        jfull = r.doc.jfull
        hist = r.boundary_kind_histogram
        forced = r.forced_break_chunk_indexes
        out += [
            f"## {r.entry.jid}",
            "",
            f"- entry：`{r.entry.path_posix}`",
            f"- size：{r.entry.unpacked_size} bytes　sha256：`{r.sha256}`",
            f"- crc32：allowlist `{r.entry.crc32}`　實際 `{r.crc32_actual}`",
            f"- chunk 數：{r.chunk_count}",
            f"- 邊界分布：{hist}",
            f"- forced_break（chunk 索引）："
            f"{list(forced) if forced else '無'}",
            "",
            "### chunk 邊界表",
            "",
            "| # | start | end | boundary_kind |",
            "|---|---|---|---|",
        ]
        for c in r.chunks:
            kind = getattr(c.boundary_kind, "value", str(c.boundary_kind))
            mark = " **←FORCED**" if c.chunk_index in forced else ""
            out.append(
                f"| {c.chunk_index} | {c.start_offset} | {c.end_offset} | {kind}{mark} |"
            )
        out += [
            "",
            "### JFULL（逐字，勿手改；含 CRLF，marker 與原文同一行）",
            "",
            # marker 與原文同一行：見 JFULL_BEGIN 的 CRLF 陷阱說明
            f"{JFULL_BEGIN}{jfull}{JFULL_END}",
            "",
            "---",
            "",
        ]

    skipped = [r for r in results if not r.chunked]
    if skipped:
        out += [
            "## 未進入審閱的卷（失敗或 drift，如實列出）",
            "",
            "| jid | 停在階段 | 原因 |",
            "|---|---|---|",
        ]
        for r in skipped:
            stage, reason = r.fail or ("?", "未記錄原因")
            out.append(f"| {r.entry.jid} | {stage} | {reason} |")
        out.append("")

    return "\n".join(out)


def render_verdicts_template(al: Allowlist) -> str:
    """審閱結論檔的初始內容（FR-008／SC-007）。

    **未審＝空值而非省略** —— 100 列全在，判定欄留空。那樣「沒審」和
    「審了說不是」在檔案層面就不會搞混：後者會有文字，前者只有空欄。
    """
    lines = [
        "# M1 審閱結論",
        "",
        "由 P2 填寫。**未審請留空，不要刪列** —— 空值代表「還沒看」，",
        "填了代表「看過了」，兩者必須能從檔案本身分辨。",
        "",
        f"allowlist：`{al.entries_sha256[:16]}…`（{al.count} 筆）",
        "",
        VERDICTS_HEADER.rstrip("\n"),
    ]
    for e in al.entries:
        lines.append(f"| {e.jid} |  |  |  |  |")
    lines.append("")
    return "\n".join(lines)


def _describe_drift(parsed) -> str:
    """把 schema 驗證結果壓成一句可讀的原因（進 manifest 的 `fail.reason`）。

    `schema.validate` **不拋例外**（drift 是資料的狀態，不是程式的錯誤），所以
    這裡必須自己把 `Drift.reasons` 收斂成一句，否則 manifest 裡會塞進一整個
    例外物件。
    """
    reasons = tuple(getattr(parsed, "reasons", ()) or ())
    if not reasons:
        return "schema 驗證未通過（無 reasons）"
    head = "；".join(reasons[:3])
    more = f"；…共 {len(reasons)} 項" if len(reasons) > 3 else ""
    severity = getattr(getattr(parsed, "severity", None), "value", None) or getattr(
        parsed, "severity", None
    )
    return f"drift[{severity}] {head}{more}"

