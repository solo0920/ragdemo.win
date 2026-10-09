#!/usr/bin/env python3
"""產生 `tests/fixtures/judgements/fixture.rar`。

**這個 RAR 不是用 `rar` 壓縮出來的**，而是按 RAR 4 格式逐 byte 組出來的
store-method archive。理由：

* CI 上沒有 `rar`（這個 repo 的三台機器連 `unrar` 都沒有，見 plan.md §0.2），
  測試不該依賴外部工具才能跑。
* 內容必須**看得見**：每個 entry 的名稱、方法值、大小、CRLF、U+3000 都在
  下面明文寫著，測試失敗時可以直接對照「預期什麼」，不必解包。
* fixture 必須小到能進版控（spec FR-006 的邊界要求）。

## 合成 fixture 的已知弱點

最大的風險是「測試與實作共用同一個錯誤假設」：parser 的 offset 若算錯，而
產生器也照同一個錯誤 offset 組 archive，測試會全綠而真 artifact 全錯。

兩道獨立防線：

1. 本產生器**依序 append 欄位**，不寫偏移常數；parser **從 block 起點的固定
   偏移讀**。兩者是不同的表達方式，不會因同一個 typo 一起壞。
2. `tests/test_judgement_inventory.py::test_real_corpus_entry_count` 在真
   artifact 上斷言 **108,547 entries** —— 那個數字由 `decoder-proof.md` 以
   `unrar lt` 獨立得出，不是本 repo 算出來的。合成 fixture 綠而真 artifact
   紅，是可能且會被看見的。

## fixture 刻意涵蓋的邊界

* **backslash 路徑**：`資料\\最高法院\\…` —— 真 corpus 108,547/108,547 全是
  反斜線分隔（FR-007 要求原樣往返）。
* **cp950 檔名**：逼 parser 走 Big5 fallback。實測 corpus 有這類名稱。
* **CRLF + U+3000**：`主　　文` —— 權威文字的形狀；`主　　文` ≠ `主文`。
* **目錄 header**：`UNP_VER=20` 且 dir 旗標 `0xE0`。實測 corpus 有 138 個。
* **`packed_size != unpacked_size`**（entry 10，512 / 1024）—— 讓兩個 size
  欄位可獨立驗證。缺了它，fixture-only 測試抓不到 `UNP_SIZE` 的 offset 錯誤，
  而 clean clone 的 CI 不會跑 corpus-gated 測試來補。
* **真實 FILE_CRC** —— T005 的驗證依據。早期版本所有 entry 的 FILE_CRC 都是
  0，那讓「解壓後重算 CRC32 再比對」永遠無法成功，fixture 等於沒有可驗的東西。
* **兩筆負向案例**（entry 11 / 12）—— 分別讓長度與 CRC 單獨不符，確保 T005 的
  兩項檢查各自有效，而不是其中一項其實從沒真的跑過。

## 重新產生

    .venv/bin/python tests/fixtures/judgements/make_fixture.py

`test_fixture_file_matches_generator` 會驗證版控中的 binary 與本檔產生器一致；
有人手改 binary 會被抓到。
"""
from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

MARKER = b"Rar!\x1a\x07\x00"

_TYPE_MAIN = 0x73
_TYPE_FILE = 0x74
_TYPE_ENDARC = 0x7B

_FLAG_ADD_SIZE = 0x8000
_DIR_VALUE = 0x00E0

_UNP_VER_RAR3 = 29   # 一般檔案
_UNP_VER_DIR = 20    # RAR 拿這個值標記目錄 header（非版本號）

HOST_OS_WINDOWS = 2


def _head_crc(rest: bytes) -> int:
    """HEAD_CRC：從 HEAD_TYPE 起算的 CRC32 低 16 bits。"""
    return zlib.crc32(rest) & 0xFFFF


def _file_crc(data: bytes, override: int | None = None) -> int:
    """FILE_CRC：archive 記錄的 CRC32。

    預設是 data 的真實 CRC32 —— 這是 T005 的驗證依據，寫 0 會讓每筆解壓都
    「驗證失敗」，fixture 等於沒有可驗的東西。

    `override` 只給**刻意錯誤**的負向案例使用（見 entry 12）。
    """
    return (zlib.crc32(data) & 0xFFFFFFFF) if override is None else override


def _main_header() -> bytes:
    # type, flags, head_size, reserved1(2), reserved2(4)
    rest = struct.pack("<BHHHI", _TYPE_MAIN, 0x0000, 13, 0, 0)
    return struct.pack("<H", _head_crc(rest)) + rest


def _file_header(
    name: bytes,
    *,
    data: bytes = b"",
    method: int = 0x30,
    unp_ver: int = _UNP_VER_RAR3,
    is_dir: bool = False,
    add_size: bool = True,
    unpacked_size: int | None = None,
    file_crc: int | None = None,
) -> bytes:
    """組一個 file header 連同其後的資料。

    欄位依序 append；`add_size=False` 時**不寫** PACK_SIZE 欄位 —— 那正是
    header 旗標沒有 0x8000 時的佈局，也是 UNP_SIZE 位置會前移的原因。

    `unpacked_size` 預設等於 `len(data)`。覆寫它（見 entry 10）是為了讓
    PACK_SIZE 與 UNP_SIZE 落在**不同的偏移**並帶不同數值 —— 兩者相等時，
    這兩個欄位讀錯位置都看不出來。

    `file_crc` 預設是 data 的真實 CRC32；`file_crc_override` 讓負向案例能
    宣告一個錯誤的 CRC32。
    """
    if is_dir:
        unp_ver = _UNP_VER_DIR
        data = b""

    flags = _FLAG_ADD_SIZE if add_size else 0x0000
    if is_dir:
        flags |= _DIR_VALUE

    name_field = name + b"\x00"
    # HEAD_SIZE = 固定欄位 + name；固定欄位大小依 add_size 而異。
    fixed = 32 if add_size else 28
    head_size = fixed + len(name_field)

    parts = [struct.pack("<BHH", _TYPE_FILE, flags, head_size)]
    if add_size:
        parts.append(struct.pack("<I", len(data)))   # PACK_SIZE
    parts += [
        struct.pack("<I", len(data) if unpacked_size is None else unpacked_size),
        struct.pack("<B", HOST_OS_WINDOWS),          # HOST_OS
        struct.pack("<I", _file_crc(data, file_crc)),# FILE_CRC（T005 的驗證依據）
        struct.pack("<I", 0),                        # FTIME
        struct.pack("<B", unp_ver),                  # UNP_VER
        struct.pack("<B", method),                   # METHOD
        struct.pack("<H", len(name_field)),          # NAME_SIZE
        struct.pack("<I", 32),                       # ATTR
        name_field,
    ]
    rest = b"".join(parts)
    return struct.pack("<H", _head_crc(rest)) + rest + data


def _endarc() -> bytes:
    rest = struct.pack("<BHH", _TYPE_ENDARC, 0x4000, 7)
    return struct.pack("<H", _head_crc(rest)) + rest


def build() -> bytes:
    """組出 fixture archive。

    Entry 順序刻意混合 store/壓縮方法與 UTF-8/cp950 編碼，讓 parser 的每個分支
    都有對應的輸入。
    """
    out = bytearray(MARKER)
    out += _main_header()

    # 1. 一般 JSON，store，中文 UTF-8 檔名，backslash 分隔
    out += _file_header(
        "資料\\最高法院\\刑事\\test,115,刑訴,1,20260101,1.json".encode("utf-8"),
        data='{"JID":"test,115,刑訴,1,20260101,1"}'.encode("utf-8"),
        method=0x30,
    )
    # 2. CRLF + U+3000 全形空格：權威文字的實際形狀（FR-010 / INV-REPRO）
    out += _file_header(
        "資料\\最高法院\\民事\\crlf,115,民訴,2,20260102,1.json".encode("utf-8"),
        data="主　　文\r\n臺灣臺北地方法院民事判決\r\n\r\n說明：一\r\n".encode("utf-8"),
        method=0x30,
    )
    # 3. cp950/Big5 檔名 —— 逼 parser 走 fallback 分支（實測 corpus 有這種）
    out += _file_header(
        "資料\\台北地院\\big5.json".encode("cp950"),
        data=b"{}",
        method=0x30,
    )
    # 4. 目錄 header（UNP_VER=20、dir 旗標 0xE0、無資料）
    out += _file_header("資料\\最高法院".encode("utf-8"), is_dir=True)
    # 5. 根目錄
    out += _file_header("資料".encode("utf-8"), is_dir=True)
    # 6. 空資料的非目錄 entry —— unpacked_size == 0 必須仍被當成檔案
    out += _file_header("資料\\empty.json".encode("utf-8"), data=b"", method=0x30)
    # 7. METHOD 0x33（-m3）：method 欄位的第二個觀察值
    out += _file_header("資料\\m3.json".encode("utf-8"), data=b'{"a":1}', method=0x33)
    # 8. 檔名含逗號與數字 —— 不得被當成可解析的欄位（T009 的前置）
    out += _file_header(
        "資料\\fields,115,民訴,77,20260103,2.json".encode("utf-8"),
        data=b"{}",
        method=0x30,
    )
    # 9. 無 ADD_SIZE 旗標：PACK_SIZE 欄位缺席 → UNP_SIZE 前移到 +7。
    #    實測 corpus 的 108,547 筆**全部**有 ADD_SIZE，所以這是純防禦性
    #    fixture；保留它是因為 offset 前移是這個 parser 最容易寫錯的地方。
    out += _file_header(
        "noaddsize.json".encode("utf-8"), data=b"", method=0x30, add_size=False
    )
    # 10. ★ packed_size != unpacked_size（正常，可解壓並通過驗證）
    #
    #     這一筆存在的唯一理由：讓 PACK_SIZE(+7) 與 UNP_SIZE(+11) 成為
    #     **兩個可獨立驗證的欄位**。
    #
    #     在它之前 fixture 全部是 store method，因此每筆的兩個值必然相等 ——
    #     那時把 UNP_SIZE 讀成 PACK_SIZE（或讀偏移 4）都不會被任何
    #     fixture-only 測試察覺。實測 corpus 是 108,409/108,547 筆
    #     packed != unpacked（entry 0：1830 → 3967，壓縮比約 2.2），
    #     而唯一的 coverage 是 corpus-gated 測試 —— clean clone 的 CI 會 skip。
    #
    #     method 用 0x30（store）：packed bytes 即 content bytes，於是 T005
    #     不需要任何 decoder binary 就能解壓這一筆。UNP_SIZE 宣告 1024 是
    #     **假的**（payload 只有 512 bytes）—— 這正是 entry 11 要抓的情況，
    #     見下。
    out += _file_header(
        "資料\\size-differs.json".encode("utf-8"),
        data=b"x" * 512,             # packed: 512 bytes on disk
        method=0x30,                 # store → 不需 decoder 即可解出 payload
        unpacked_size=1024,          # ★ 與 packed 不同的解壓長度
    )
    # 11. 負向：宣告的 unpacked_size (1024) 與實際 payload (512) 不符。
    #     這是「archive header 說謊」的情況。T005 必須**明確失敗**，不得
    #     靜默接受 —— 否則一份壞掉的 archive 會變成一份靜默錯誤的 corpus。
    #     CRC 刻意給正確值（CRC 是對 512 bytes 算的），這樣失敗原因會明確
    #     指向**長度**而不是 CRC。
    out += _file_header(
        "資料\\bad-unpackedsize.json".encode("utf-8"),
        data=b"y" * 512,
        method=0x30,
        unpacked_size=1024,          # ★ 宣稱 1024，實際 512 → 長度驗證必須失敗
    )
    # 12. 負向：宣告的 FILE_CRC 與 payload 的真實 CRC32 不符，長度則正確。
    #     這是「內容被換過」的情況 —— 長度對得上，但 bytes 不是 archive 說的
    #     那份。只驗長度會漏掉這類，spec 要求兩者都驗。
    out += _file_header(
        "資料\\bad-crc.json".encode("utf-8"),
        data=b"z" * 64,
        method=0x30,
        file_crc=0xDEADBEEF,         # ★ 刻意錯誤的 CRC32
    )

    out += _endarc()
    return bytes(out)


def build_docs_archive() -> bytes:
    """組出 `docs_fixture.rar` —— 內容是 `docs/*.json` 那些真實 8-key 文件。

    為什麼需要一個獨立的 archive：`fixture.rar` 的 entry 內容是 `{"a":1}` 之類的
    示意資料，不是 8-key 判決 JSON，所以它無法驗證 T009（schema → document）
    與 T010（JFULL lossless）。這裡把 `docs/` 裡的 fixture 文件原封不動地
    包成 archive，讓 T005 → T009 → T010 的整條路徑可以在**沒有 production RAR、
    沒有 decoder** 的情況下端到端跑完。

    payload 就是文件位元組本身（store method），所以解壓出來的 bytes 與磁碟上
    的檔案**逐 byte 相同** —— 這正是 T010 要驗證的性質。
    """
    docs = Path(__file__).resolve().parent / "docs"
    out = bytearray(MARKER)
    out += _main_header()

    # 排序：讓 archive 內容 deterministic（檔案系統列舉順序不可靠）
    for p in sorted(docs.glob("*.json")):
        payload = p.read_bytes()
        # 只包「合法且符合契約」的文件作為 archive 內容。drift_* 那些是
        # T008 的**輸入**（要被 validator 拒絕），不該是一筆能成功解壓的
        # entry —— 否則 archive 自己就含了一份壞資料。
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(parsed, dict):
            continue
        # key 集合必須完全吻合 —— 這是 T008 acceptance 的「conforming document」
        # 定義。多一個或少一個 key 都是 drift，而一份含 drift 資料的 archive
        # 不是好 fixture。
        if set(parsed) != {
            "JID", "JYEAR", "JCASE", "JNO", "JDATE", "JTITLE", "JFULL", "JPDF"
        }:
            continue
        # 每個值都必須是字串（null / int 都是 drift）
        if not all(isinstance(v, str) for v in parsed.values()):
            continue
        # key 順序要對；順序不符是 OBSERVATIONAL drift，但這裡不放行，
        # 讓 archive 只含完全合約的文件。
        if tuple(parsed) != ("JID", "JYEAR", "JCASE", "JNO", "JDATE", "JTITLE", "JFULL", "JPDF"):
            continue
        # JID 形狀必須是已驗證的 5 或 6 欄
        if len(parsed["JID"].split(",")) not in (5, 6):
            continue
        out += _file_header(f"docs\\{p.name}".encode("utf-8"), data=payload, method=0x30)

    out += _endarc()
    return bytes(out)


def main() -> None:
    here = Path(__file__).resolve().parent
    dest = here / "fixture.rar"
    payload = build()
    dest.write_bytes(payload)
    print(f"wrote {dest} ({len(payload)} bytes)")

    docs_dest = here / "docs_fixture.rar"
    docs_payload = build_docs_archive()
    docs_dest.write_bytes(docs_payload)
    print(f"wrote {docs_dest} ({len(docs_payload)} bytes)")


if __name__ == "__main__":
    main()
