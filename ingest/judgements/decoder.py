#!/usr/bin/env python3
"""已證明的 RAR decoder configuration（spec 004 T003 / FR-008）。

## 這份檔案做什麼 —— 以及刻意不做什麼

**只記錄。** `decoder-proof.md` 已經用實際解壓證明 UnRAR 7.13 讀得動這份
artifact。本模組把那份證明的結論釘成常數，讓 derived corpus 能記錄
「這批資料是哪個 decoder 解出來的」。

**不做選擇。** 這裡沒有任何「比較哪個 library 比較好」的邏輯，也沒有
fallback。理由：`citation-pattern-survey.md` 之後的每一次調查都顯示，
對這個 repo 的司法資料而言，decoder 選擇是**已經關閉的議題**，不是待辦。
重開它只會製造第二份可能漂移的真相。

**不安裝。** 本模組不下載、不編譯、不執行任何二進位。`probe()` 只在呼叫端
明確要求時才執行外部指令，而且是把結果讀回來，不是假設它在。

## 為什麼把 source sha256 也釘下來

因為「UnRAR 7.13 這個字串」不足以重現。同一個版本號可能來自不同的 build。
`decoder-proof.md` 記錄的是 rarlab 官方 source tarball 的 sha256
（`72a9ccca…`）；有了它，未來任何人重新 build 都能證明自己拿到的是同一份
source。這正是 spec FR-008（每個 stage 都要記 fingerprint）的用意。

## 授權

UnRAR source 的授權（`decoder-proof.md` §2 逐條記錄）允許「用於處理 RAR
archive，免費，且可隨其他軟體散布（須附授權段落）」。binary 版的試用授權
**不允許**打包進其他軟體 —— 這就是當初選 source 而非 binary 的原因。
把 binary 烤進 production API image 會違反這一點，本模組因此不提供該路徑。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# ── 已證明的 decoder 身分（decoder-proof.md §2）──────────────────────────────
#
# 這組值不是「我們選了 UnRAR」，而是「我們試過並證明它讀得動這份 artifact」。
# VERSION 是 7.13 —— 注意 tarball 檔名是 unrarsrc-7.1.10，但 version.hpp 宣告
# 7.13，而 unrar 自己的 --version 輸出才是權威（plan.md §0.2 已記錄此落差）。
DECODER_NAME = "unrar"
DECODER_VERSION = "7.13"

SOURCE_URL = "https://www.rarlab.com/rar/unrarsrc-7.1.10.tar.gz"
SOURCE_SHA256 = "72a9ccca146174f41876e8b21ab27e973f039c6d10b13aabcb320e7055b9bb98"
SOURCE_SIZE_BYTES = 268008

# 官方授權頁（引用，不是下載點）。用途是讓 provenance 讀者知道授權依據。
LICENSE_URL = "https://www.rarlab.com/license.htm"

# 能力聲明：這些是**實測結果**，不是文件轉述。
SUPPORTED_CONTAINER = "rar4"
SUPPORTED_COMPRESSION = "rar1.5(v29) -m3 -md=1m"
VERIFIED_ENTRIES = 108547
VERIFIED_2026_10_07 = True


@dataclass(frozen=True)
class DecoderInfo:
    """一份 decoder 的身分。frozen —— provenance 不該在程式中被改寫。"""

    name: str
    version: str
    source_url: str
    source_sha256: str
    license_url: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "source_url": self.source_url,
            "source_sha256": self.source_sha256,
            "license_url": self.license_url,
        }


def describe() -> DecoderInfo:
    """回傳已證明的 decoder 身分。**不執行任何外部指令。**

    分離是刻意的：`describe()` 回答「宣稱用什麼解的」，`probe()` 回答
    「現在這台機器上的那支 binary 是不是同一支」。後者需要執行，得由呼叫端
    明確要求。
    """
    return DecoderInfo(
        name=DECODER_NAME,
        version=DECODER_VERSION,
        source_url=SOURCE_URL,
        source_sha256=SOURCE_SHA256,
        license_url=LICENSE_URL,
    )


def provenance() -> dict:
    """回傳可與 derived corpus 一起存檔的 decoder provenance。"""
    info = describe()
    return {
        "decoder": info.as_dict(),
        "supports_container": SUPPORTED_CONTAINER,
        "supports_compression": SUPPORTED_COMPRESSION,
        "verified_entries": VERIFIED_ENTRIES,
        "verification_doc": "specs/004-judicial-source-fidelity/decoder-proof.md",
        # 明確記錄：binary **沒有**被打包進 image。
        # UnRAR 的 binary 版試用授權禁止打包進其他軟體；本專案走 source 路線，
        # 所以 extraction 階段需要的是「執行環境裡有一支 unrar」，不是
        # 「repo 裡附一支 unrar」。記下來是為了讓後人不必重新推導。
        "binary_bundled": False,
    }


def probe(binary: Path, timeout: int = 15) -> str:
    """執行 `binary` 並回傳它自報的版本字串。

    **呼叫端必須明確要求才會執行。** 這不是 extraction（T005），也不是
    installation —— 只是讀回一支既有 binary 的自我報告，供安裝後確認。
    路徑由呼叫端提供，本模組不去 PATH 裡搜尋、也不假設它存在。
    """
    b = Path(binary)
    if not b.is_file():
        raise FileNotFoundError(f"decoder binary 不存在：{b}")
    r = subprocess.run(
        [str(b)], capture_output=True, text=True, timeout=timeout, check=False
    )
    return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr) else ""