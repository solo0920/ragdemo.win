#!/usr/bin/env python3
"""單一 entry 的解壓與完整性驗證（spec 004 T005 / FR-006, FR-007）。

## 這份模組回答什麼問題

> 「這一個 entry 的 bytes，是 archive 自己聲稱的那份 bytes 嗎？」

答案必須是**可證偽的**：解壓之後比對兩件事 —— 長度與 CRC32 —— 兩者都取自
archive 的 header，不是取自我們自己。CRC32 是整條 pipeline 唯一的完整性
錨點；只比長度會漏掉「同一長度、不同內容」這種最常見的換檔情境。

## 三層防線

1. **目的地不得在 `raw/` 之下** —— T002 的 `assert_outside_raw()`。這一層擋的是
   「解壓時覆寫掉 authoritative source」，那是本 feature 最不能發生的事。
2. **decoder 只能解壓，不決定成敗** —— unrar 自己會回報 CRC mismatch（exit 3），
   但那我們**不依賴**：一個換過的 decoder build 可能根本不檢查。所以我們自己
   重算 CRC32 再比一次。實測已驗證 unrar 對 fixture 的 store entry 回報
   checksum error，而我們的獨立比對給出同樣結論。
3. **entry 不得逃出目的地** —— unrar 7.13 對 `../` 這種檔名會自行清乾淨（實測：
   `../escape.json` 被寫成 `escape.json`），但那是 decoder 的行為，不是契約。
   我們在呼叫前就自己驗一次，不把安全性外包給一個第三方 binary。

## 為什麼 store method 不需要 decoder

RAR method `0x30` = store，代表 packed bytes **就是** content bytes。這讓
`tests/fixtures/` 的 fixture（全部 store）可以在**沒有任何 decoder binary** 的
機器上解壓並驗證 —— 這是 CI 能跑 T005 測試的前提。

真 artifact 的 108,409 筆檔案全部是 `0x33`（-m3），必須有 decoder。那一條路徑
由 `decoder=` 參數注入 binary 路徑，**呼叫端必須明確提供**（沿用 T003 的
`probe()` 原則：不偷偷搜尋 PATH、不假設它存在）。

## 這份不做什麼

不解壓整個 archive（那是後續的 batch）、不解析 JSON（T008）、不寫任何東西
進 `raw/`、不解釋 `JID`。回傳的 bytes 是**未經解讀的原始位元組** —— 任何
`JFULL` 的語意處理都在這個邊界之外。
"""
from __future__ import annotations

import hashlib
import struct
import subprocess
import zlib
from dataclasses import dataclass
from pathlib import Path

import artifact as _artifact
import inventory as _inventory

# RAR method 0x30 = store：packed bytes 即 content bytes，不需要解壓演算法。
METHOD_STORE = 0x30

# unrar 的退出碼。實測：3 = CRC error（即使解壓成功、檔案已寫出）。
# 我們**不**依賴它做驗證，只在診斷訊息裡引用。
_EXIT_CRC_ERROR = 3


class ExtractionError(RuntimeError):
    """解壓失敗，或解壓結果與 archive 記錄不符。"""


class VerificationFailure(ExtractionError):
    """解壓成功，但 bytes 與 archive 記錄的 size / CRC32 不符。

    與 `ExtractionError` 分開是刻意的：前者是「拿不到」，後者是「拿到了但
    不可信」。後者必須立刻中止 —— 那正是 spec 說的 "a mismatch aborts"。
    """


@dataclass(frozen=True)
class ExtractedEntry:
    """一筆已驗證的解壓結果。frozen。"""

    path: str              # archive 內的路徑（保留原始分隔符）
    data: bytes            # 已驗證的原始位元組
    size: int              # == len(data)，且 == archive 記錄的 unpacked_size
    crc32: int             # zlib.crc32(data)，且 == archive 記錄的 FILE_CRC
    declared_crc32: int    # archive header 裡記錄的值
    destination: Path      # 實際寫出的檔案
    decoder_used: str      # 'store' 或 caller 提供的 binary 路徑

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def as_dict(self, *, include_data: bool = False) -> dict:
        """不含 `data` 的表示式 —— 給 provenance / manifest 用。"""
        d = {
            "path": self.path,
            "size": self.size,
            "crc32": self.crc32,
            "crc32_hex": f"{self.crc32:08X}",
            "declared_crc32_hex": f"{self.declared_crc32:08X}",
            "sha256": self.sha256,
            "destination": str(self.destination),
            "decoder_used": self.decoder_used,
        }
        if include_data:
            d["data"] = self.data
        return d


def crc32_hex(data: bytes) -> str:
    """bytes 的 CRC32，格式 `%08X` —— 與 archive 記錄的表示一致。"""
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"


def _find_entry(archive: Path, entry_path: str) -> _inventory.Entry:
    """在 inventory 裡找到指定 entry。

    用**完全相同**的字串比對，不做 separator 正規化、不 strip、不大小寫調整 ——
    inventory 是 authoritative 的 entry 清單（T004），這裡只是查表。
    """
    for e in _inventory.inventory(archive):
        if e.path == entry_path:
            return e
    raise ExtractionError(f"entry 不存在於 archive：{entry_path}")


def _check_destination(dest: Path) -> Path:
    """確保目的地不在 `raw/` 之下，並回傳 mkdir 後的路徑。

    T002 的結構性保證。**在 mkdir 之前**呼叫 —— 萬一將來呼叫端忘了，我們連
    目錄都不該建立在 raw/ 底下。
    """
    resolved = _artifact.assert_outside_raw(dest)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _relative_output_path(entry_path: str) -> Path:
    """entry 的 archive 路徑 → 目的地底下的相對路徑。

    ## 為什麼要自己算，而不用 decoder 寫在哪就讀哪

    decoder 會建立目錄樹、可能套用它的檔名清理規則。我們需要知道**自己**
    預期檔案會落在哪，才能（a）確認它沒逃出目的地，（b）在驗證之後讀回它。
    兩邊對不上就是問題，不該默默接受 decoder 的結果。

    `..` 在這裡被丟棄（不是 resolve 後檢查）：如果 entry 名真的帶 `..`，那是
    異常輸入，正確反應是讓它無法逃出，而不是「清理後照樣接受」。
    """
    parts = [p for p in entry_path.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    if not parts:
        raise ExtractionError(f"entry 路徑無有效部分：{entry_path!r}")
    return Path(*parts)


def _read_header_at(archive: Path, offset: int) -> tuple[int, int]:
    """回傳 (head_size, packed_size) —— 定位 payload 用。

    直接複述 RAR4 file header 的固定佈局，不引用 `inventory` 的內部常數：
    這兩個模組對 header 的責任不同（inventory 列舉、extract 取位元組），
    讓 extraction 的正確性不依賴 inventory 私有實作細節。
    """
    with open(archive, "rb") as fh:
        fh.seek(offset)
        raw = fh.read(11)
    if len(raw) < 11:
        raise ExtractionError(f"header 讀取失敗 @ {offset}（檔案可能已截斷）")
    _, _, flags, head_size = struct.unpack_from("<HBHH", raw, 0)
    if not (flags & 0x8000):
        # 沒有 ADD_SIZE 就沒有 PACK_SIZE 欄位
        return head_size, 0
    (packed,) = struct.unpack_from("<I", raw, 7)
    return head_size, packed


def _extract_store(archive: Path, entry: _inventory.Entry) -> bytes:
    """store method：packed bytes 就是 content，不需 decoder。

    這條路徑是 CI 能跑 T005 測試的原因 —— fixture 全部是 store，而 CI 沒有
    unrar。
    """
    head_size, packed_size = _read_header_at(archive, entry.offset)
    if packed_size != entry.packed_size:
        raise ExtractionError(
            f"header 與 inventory 不一致：packed {packed_size} != {entry.packed_size}"
        )
    with open(archive, "rb") as fh:
        fh.seek(entry.offset + head_size)
        data = fh.read(packed_size)
    if len(data) != packed_size:
        raise ExtractionError(
            f"payload 讀取不足：得到 {len(data)} bytes，應為 {packed_size}"
        )
    return data


def _extract_with_decoder(
    archive: Path, entry: _inventory.Entry, dest: Path, decoder: Path
) -> bytes:
    """用 caller 提供的 decoder binary 解壓，回傳 bytes。

    decoder 路徑**必須**由呼叫端提供 —— 沿用 T003 的原則：不搜尋 PATH、不假設
    存在。這三台機器目前都沒有 unrar（plan.md §0.2），自行搜尋只會讓同一段程式
    在不同機器上行為不同。
    """
    bin_path = Path(decoder)
    if not bin_path.is_file():
        raise ExtractionError(f"decoder binary 不存在：{bin_path}")

    out = _relative_output_path(entry.path)
    target = dest / out
    # 解壓前的逃逸檢查：target 必須仍在 dest 之下。
    try:
        target.resolve().relative_to(dest.resolve())
    except ValueError as exc:
        raise ExtractionError(
            f"entry 會寫到目的地之外：{entry.path} → {target}"
        ) from exc

    # unrar 用 `/` 比對 entry 名，archive 裡是 `\`。
    arg = entry.path.replace("\\", "/")
    proc = subprocess.run(
        [str(bin_path), "x", "-inul", "-o+", str(archive), arg, f"{dest}/"],
        capture_output=True,
        text=True,
        check=False,
    )
    # exit 3 = CRC error。我們自己還會再驗一次，但把它寫進訊息裡 —— decoder
    # 已經知道有問題卻被忽略，是最需要被看見的情況。
    if proc.returncode == _EXIT_CRC_ERROR:
        raise VerificationFailure(
            f"decoder 回報 CRC error（exit {_EXIT_CRC_ERROR}）：{entry.path}"
        )
    if proc.returncode != 0:
        raise ExtractionError(
            f"decoder 失敗（exit {proc.returncode}）：{entry.path}"
            f"；stderr={(proc.stderr or '').strip()[:300]}"
        )
    if not target.is_file():
        raise ExtractionError(
            f"decoder 未產生預期檔案：{target}（entry={entry.path}）"
        )
    return target.read_bytes()


def extract_one(
    entry_path: str,
    *,
    archive: Path | None = None,
    dest: Path | None = None,
    decoder: Path | None = None,
) -> ExtractedEntry:
    """解壓單一 entry 並驗證其 bytes 是真實的。

    ## 參數

    * `entry_path` —— archive 內的路徑，**原樣**（反斜線分隔，與 inventory
      完全一致）。不做正規化：那是「以為自己知道格式」的第一步。
    * `archive` —— 預設 `artifact.ARTIFACT_PATH`。
    * `dest` —— 解壓目的地。必須在 `raw/` 之下**之外**（T002 強制）。
    * `decoder` —— decoder binary 路徑。store method 不需要；其他 method 必須
      由呼叫端提供，本模組不去 PATH 裡找。

    ## 驗證順序（順序有意義）

    先比長度，再比 CRC32。長度不符就代表 archive 的 header 與實際內容已經
    不一致 —— 那時算 CRC 只是多算一次。CRC 不符是最嚴重的情況（內容被換過或
    解壓有誤），必須中止。
    """
    arch = _artifact.ARTIFACT_PATH if archive is None else Path(archive)
    if not arch.is_file():
        raise ExtractionError(f"archive 不存在：{arch}")
    if dest is None:
        raise ExtractionError(
            "必須提供 dest —— 解壓要寫檔，而寫入位置不該由本模組猜測"
        )
    dest_path = _check_destination(Path(dest))

    entry = _find_entry(arch, entry_path)
    if entry.is_dir:
        raise ExtractionError(
            f"entry 是目錄，不能當檔案解壓：{entry_path}"
        )

    if entry.method == METHOD_STORE:
        data = _extract_store(arch, entry)
        decoder_used = "store"
        target = dest_path / _relative_output_path(entry.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    else:
        if decoder is None:
            raise ExtractionError(
                f"entry {entry_path} 的 method=0x{entry.method:02x} 需要 decoder，"
                "但未提供 decoder 路徑。本模組不自行搜尋 PATH —— 請傳入 binary 路徑"
            )
        data = _extract_with_decoder(arch, entry, dest_path, Path(decoder))
        target = dest_path / _relative_output_path(entry.path)
        decoder_used = str(decoder)

    # ── 驗證：兩項都必須與 archive 記錄一致 ──
    if len(data) != entry.unpacked_size:
        raise VerificationFailure(
            f"長度不符：解壓得到 {len(data)} bytes，"
            f"archive 記錄 {entry.unpacked_size}（{entry_path}）"
        )
    computed = zlib.crc32(data) & 0xFFFFFFFF
    if computed != entry.crc32:
        raise VerificationFailure(
            f"CRC32 不符：解壓結果 {computed:08X}，"
            f"archive 記錄 {entry.crc32:08X}（{entry_path}）"
        )

    return ExtractedEntry(
        path=entry.path,
        data=data,
        size=len(data),
        crc32=computed,
        declared_crc32=entry.crc32,
        destination=target,
        decoder_used=decoder_used,
    )


def extract_many(
    entry_paths: list[str],
    *,
    archive: Path | None = None,
    dest: Path | None = None,
    decoder: Path | None = None,
) -> list[ExtractedEntry]:
    """依序解壓多個 entry。任一筆驗證失敗就立刻中止。

    不做「收集所有錯誤再一起回報」：spec 說 "a mismatch aborts the pipeline"。
    繼續解壓已知不可信的 archive 只會浪費時間並可能產出更多壞資料。
    """
    return [
        extract_one(p, archive=archive, dest=dest, decoder=decoder)
        for p in entry_paths
    ]
