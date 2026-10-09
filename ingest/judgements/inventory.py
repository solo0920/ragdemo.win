#!/usr/bin/env python3
"""RAR container 的 entry 清單 —— 只走 archive header，不解壓（spec 004 T004）。

## 為什麼要自己走 header

因為這一步的全部價值就是「沒有 decoder 也做得到」。`decoder-proof.md` 記錄了
host 佈局的現實：三台機器原本都沒有任何 RAR 工具。而 inventory 回答的問題是
「這份 artifact 裡面有什麼」—— 這只跟 container 格式有關，跟解壓無關。

拿到 entry 清單之後，上游任何異常（entries 數量變了、大小對不上、出現新格式）
都能在**不解壓任何一個檔案**的情況下被看見。這是後面每一階段的第一道防線。

## 這是 inventory，不是 extraction

刻意不做的事：

* 不解壓、不寫任何檔案
* 不讀 entry 內容，所以**不碰 JSON**（schema 驗證是 T008，不是這裡）
* 不判斷 entry 是不是判決、哪一法院、哪一天
* 不去重、不排序挑選、不猜 `JID`

`is_dir` 讀的是 RAR header 的目錄旗標，不是從檔名有沒有斜線去猜。

## 格式依據（RAR 4）

marker 之後每個 block 都以共用的 base header 開頭：

    crc(2) type(1) flags(2) head_size(2)   = 7 bytes

接著若 `flags & 0x8000`（ADD_SIZE present）多一個 `add_size(4)`。file header
(0x74) 的 add_size 就是 packed size —— 必須走過它才知道下一個 block 在哪。

file header 在 base header 之後的欄位順序：

    +7  pack_size(4)      （if flags & 0x8000）
    +7  unp_size(4)       （if flags & 0x8000，否則 +11）
    +15 host_os(1)  +16 file_crc(4)  +20 ftime(4)
    +24 unp_ver(1)  +25 method(1)  +26 name_size(2)  +28 attr(4)
    +32 high_pack_size(4)（if flags & 0x0100 —— name 因此往後挪 4 bytes）
    +32 file_name(name_size bytes)

⚠️ 這是為 **RAR 4** 寫的。RAR 5 的 block 佈局不同（header 帶 extra area、
marker 結尾不同），遇到時本模組**明確拒絕**而不是猜。

## 這份模組怎麼被驗證的

兩個獨立的錨點，缺一不可：

1. `tests/fixtures/` 裡的合成 RAR4 —— 測試自己組出 byte-exact 的 archive，
   驗證 parser 的欄位偏移正確。
2. 真 artifact 上的 integration test（`@pytest.mark.judgement_corpus`）斷言
   **108,547 entries** —— 那個數字是 `decoder-proof.md` 用獨立方法（`unrar lt`）
   得出的，不是我們自己算出來的。

只有 (1) 會有「parser 與測試犯同一個錯」的風險；只有 (2) 則無法在沒有
283 MB artifact 的機器上跑。兩者一起才把實作釘在 reality 上。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

# RAR4 marker：`52 61 72 21 1a 07 00`（7 bytes，之後直接接 block chain）
RAR4_MARKER = b"Rar!\x1a\x07\x00"
# RAR5 marker：`52 61 72 21 1a 07 01 00`（8 bytes）
RAR5_MARKER = b"Rar!\x1a\x07\x01\x00"

_BASE = struct.Struct("<HBHH")   # crc, type, flags, head_size
_ADD_SIZE = struct.Struct("<I")

# block types
_TYPE_FILE = 0x74
_TYPE_ENDARC = 0x7B

# header flags
_FLAG_ADD_SIZE = 0x8000   # base header 之後有 add_size(4)
_FLAG_HIGH_SIZE = 0x0100  # file header 有 high_pack_size(4)，name 位移 +4
_DIR_WINDOW = 0x00E0      # 視窗旗標；== 0xE0 才算目錄
_DIR_VALUE = 0x00E0

# file header 內的欄位偏移（相對 block 起點，已含 7 bytes base header）。
#
# 這些值是**有 ADD_SIZE 旗標時**的佈局。旗標缺席時 PACK_SIZE 整個欄位不存在，
# 後面每一個欄位都前移 4 bytes —— 所以實際取值一律用 `shift` 調整，不要直接
# 用這些常數。
_OFF_PACK_SIZE = 7
_OFF_UNP_SIZE = 11      # = _OFF_PACK_SIZE + 4
_OFF_HOST_OS = 15
_OFF_FILE_CRC = 16      # archive 自記的 CRC32 —— T005 驗證解壓結果的依據
_OFF_FTIME = 20
_OFF_UNP_VER = 24
_OFF_METHOD = 25
_OFF_NAME_SIZE = 26
_OFF_ATTR = 28
_OFF_NAME = 32

_ADD_SIZE_LEN = 4

# UNP_VER 觀察值（`decoder-proof.md` 那份 artifact）：
#   29 (0x1d) 一般檔案  ·  20 (0x14) 目錄 header  ·  50 (0x32) RAR5
#
# 20 不是筆誤也不是版本 —— RAR 把目錄 header 的 UNP_VER 欄位當作目錄標記用。
# 它不在文件裡最顯眼的位置，所以特別記下來。
#
# 這個白名單的作用不是「驗證版本」，而是**擋掉欄位錯位**：offset 若算錯，
# 這個 byte 會是檔名或 crc 的一部分。與其產出一筆看起來正常的垃圾 entry，
# 不如讓它被拒絕、由呼叫端看見漏掉了東西。
_KNOWN_VERSIONS = frozenset({20, 29, 36, 50})

_READ = 1 << 18  # 256 KiB 視窗：header 密集、資料稀疏，無須整檔載入


class UnsupportedContainer(RuntimeError):
    """不是本模組支援的 RAR 版本，或根本不是 RAR。"""


@dataclass(frozen=True)
class Entry:
    """container 裡的一個 entry。frozen，且**不含內容**。"""

    path: str
    unpacked_size: int
    packed_size: int
    method: int           # 0x30 = store；0x31–0x35 = RAR 壓縮等級
    is_dir: bool
    offset: int           # header 在 archive 內的位元組位置（可重現的錨點）
    crc32: int            # archive 自己記錄的 CRC32（T005 的驗證依據）

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "unpacked_size": self.unpacked_size,
            "packed_size": self.packed_size,
            "method": self.method,
            "is_dir": self.is_dir,
            "offset": self.offset,
            "crc32": self.crc32,
        }


class _WindowReader:
    """視窗式讀取器。

    刻意不做 `read()` 全檔：283 MB 進記憶體沒有任何理由，而這裡真正需要的
    只有每個 block 開頭那幾十個 bytes。
    """

    __slots__ = ("_f", "_buf", "_base")

    def __init__(self, fh) -> None:
        self._f = fh
        self._buf = b""
        self._base = 0

    def at(self, pos: int, n: int) -> bytes:
        """回傳從 `pos` 起 `n` bytes。跨視窗時重抓。"""
        if not (self._base <= pos and pos + n <= self._base + len(self._buf)):
            self._f.seek(pos)
            self._buf = self._f.read(_READ)
            self._base = pos
        i = pos - self._base
        return self._buf[i:i + n]

    def head(self, pos: int) -> tuple[int, int, int]:
        """回傳 (type, flags, head_size)。"""
        return _BASE.unpack_from(self.at(pos, _BASE.size), 0)[1:]


def _decode_name(raw: bytes) -> str:
    """RAR 內的檔名編碼。

    先試 UTF-8，失敗退回 cp950（Big5）。這個 fallback 是**必要**的，不是
    防禦性程式碼：本 archive 的 entry 名含中文，而 RAR 4 沒有 header 層的
    編碼標記，只能靠「哪個解得動」判斷。純 UTF-8 會在非 UTF-8 建立的
    entry 上直接失敗。

    ⚠️ 這條 fallback 的正確性是 **UNKNOWN**：整份 corpus 的 entry 名編碼
    分布尚未調查（0.46% 抽樣不足以斷言）。若日後發現某个 entry 名解出來是
    亂碼，正確處置是回報、不是把 fallback 再加一層。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp950", errors="replace")


def _walk(path: Path) -> Iterator[Entry]:
    """走一遍 block chain，逐個 yield file header。

    不開啟 archive 寫入模式 —— 讀取以 'rb' 進行，raw artifact 不受影響。
    """
    size = path.stat().st_size
    with open(path, "rb") as fh:
        rd = _WindowReader(fh)

        marker = rd.at(0, 8)
        if marker.startswith(RAR4_MARKER):
            off = len(RAR4_MARKER)
        elif marker == RAR5_MARKER:
            raise UnsupportedContainer(
                "RAR5 的 block 佈局與本模組實作的 RAR4 不同；"
                "那需要另一個 parser，而不是在這裡猜"
            )
        else:
            raise UnsupportedContainer(
                f"不是 RAR archive（前 8 bytes = {marker!r}）"
            )

        while off < size:
            try:
                btype, flags, head_size = rd.head(off)
            except struct.error:
                return

            # head_size < 7 表示這已不是 base header 佈局（損毀、或另一種
            # 格式）。停下來，不猜、不無限迴圈。
            if head_size < _BASE.size:
                return

            add_size = 0
            if flags & _FLAG_ADD_SIZE:
                try:
                    (add_size,) = _ADD_SIZE.unpack_from(
                        rd.at(off + _BASE.size, _ADD_SIZE.size), 0
                    )
                except struct.error:
                    return

            if btype == _TYPE_FILE:
                entry = _read_file_header(rd, off, flags, head_size)
                if entry is not None:
                    yield entry

            if btype == _TYPE_ENDARC:
                return

            off += head_size + add_size


def _read_file_header(
    rd: _WindowReader, off: int, flags: int, head_size: int
) -> Entry | None:
    """解析一個 file header (type 0x74)；讀不出來就回 None。

    回 None 而不拋錯：單一 entry 壞掉不該讓整份清單失敗 —— 清單的用途正是
    要看出「有多少東西以及哪裡不對」，一個 entry 讀不出來本身就是訊息。

    ⚠️ 固定欄位與檔名分兩個變數讀（`hdr` / `name_raw`）。早期版本用同一個
    `raw` 讀兩次，第二次把固定欄位的位元組覆蓋成檔名字節 —— 於是
    `packed_size` 變成從檔名裡讀出來的四個字元、`method` 變成檔名的第 26 個
    byte，而 entry 數與 offset 仍然完全正確。**錯誤的形狀就是這種**：
    看起來一切正常，只有少數欄位是垃圾。
    """
    # 兩個位移都會讓後續欄位右移：
    #   ADD_SIZE 缺席 → PACK_SIZE 欄位不存在，全體前移 4
    #   HIGH_SIZE   → 多一個 high_pack_size(4)，name 額外後移 4
    has_add = bool(flags & _FLAG_ADD_SIZE)
    shift = 0 if has_add else -_ADD_SIZE_LEN
    if flags & _FLAG_HIGH_SIZE:
        shift += _ADD_SIZE_LEN

    name_off = _OFF_NAME + shift
    need = name_off + 2
    try:
        hdr = rd.at(off, need)
        (name_size,) = struct.unpack_from("<H", hdr, _OFF_NAME_SIZE + shift)
    except (struct.error, IndexError):
        return None

    # name_size 不合理、或名稱放不進宣告的 head_size → 這不是我們理解的
    # file header。硬讀會產出一筆看起來合理、實際上是垃圾的 entry。
    if not (0 < name_size <= 4096) or name_off + name_size > head_size:
        return None

    try:
        name_raw = rd.at(off + name_off, name_size)
    except (ValueError, OSError):
        return None

    # RAR 4 的檔名以 NUL 結尾，且 name_size 含該 NUL
    name = _decode_name(name_raw.split(b"\x00", 1)[0])
    if not name:
        return None

    try:
        packed = (
            _ADD_SIZE.unpack_from(hdr, _OFF_PACK_SIZE + shift)[0] if has_add else 0
        )
        (unpacked,) = _ADD_SIZE.unpack_from(hdr, _OFF_UNP_SIZE + shift)
        method = hdr[_OFF_METHOD + shift]
        ver = hdr[_OFF_UNP_VER + shift]
        # T005 要把解出來的 bytes 與「archive 自己記錄的 CRC32」比對。這個數字
        # 只存在於 header 裡 —— 這是整條 pipeline 唯一的完整性錨點，因此不能靠
        # 解壓後自己重算（那樣就只是自己對自己）。已對實測 artifact 驗證：
        # 第一筆 entry 的 FILE_CRC = CD7072B9，與解壓後 bytes 的
        # zlib.crc32 完全一致。
        (crc32,) = _ADD_SIZE.unpack_from(hdr, _OFF_FILE_CRC + shift)
    except (struct.error, IndexError):
        return None

    # UNP_VER 白名單見上方說明。作用不是驗版本，而是**擋掉欄位錯位**：
    # offset 若算錯，這一格會是 crc 或檔名的一部分。
    if ver not in _KNOWN_VERSIONS:
        return None

    return Entry(
        path=name,
        unpacked_size=unpacked,
        packed_size=packed,
        method=method,
        # 目錄旗標是三個 bits 的**組合**等於 0xE0，不是「任一 bit 為 1」。
        # 後者會把一大票一般檔案誤判成目錄。
        is_dir=(flags & _DIR_WINDOW) == _DIR_VALUE,
        offset=off,
        crc32=crc32,
    )


def inventory(path: Path | None = None) -> list[Entry]:
    """回傳 container 內的 entry 清單，**順序與 archive 內一致**。

    刻意不排序：archive 本身的順序是上游產生方式的特徵，排序等於把那個
    訊息抹掉。要確認兩次執行結果相同，直接比這個 list 就夠。
    """
    p = _default_artifact() if path is None else Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"archive 不存在：{p}")
    return list(_walk(p))


def _default_artifact() -> Path:
    """未指定路徑時的 artifact 位置。

    延遲 import：`artifact.py` 會被 `identify()` 之類的測試單獨使用，這裡不
    該讓那條 import 依賴另一個模組可被匯入。
    """
    import artifact

    return artifact.ARTIFACT_PATH
