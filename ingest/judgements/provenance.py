#!/usr/bin/env python3
"""判決 derived object 的 provenance chain（spec 004 T021 / FR-017）。

## 這個模組回答什麼問題

> 這個 chunk／這個引用候選，來自哪一份 artifact、哪一個 entry、哪一個
> JSON field、哪一段 byte offset？

答案必須是**五個欄位缺一不可**：

```text
artifact_sha256
entry_path
json_field
start_offset
end_offset
```

## 為什麼 missing component 是 error，不是 default

provenance 的價值在於「可以回到 source 驗證」。如果 `artifact_sha256` 缺一個
預設值，那這條鏈就斷了 —— 而斷掉的鏈看起來仍然像一份完整的 provenance，只是
無法驗證。與其那樣，不如讓它**明確拋錯**。

## 為什麼 `json_field` 永遠是 `"JFULL"`

T015 的 chunk 與 T013 的 citation candidate 都是對 `JFULL` 這個欄位做切片。
這個函式把這個事實釘成常數，而不是讓呼叫端每次猜測。

## 為什麼 artifact_sha256 要從外部傳入

Chunk 本身不帶 artifact_sha256（那是匯入批次的資訊，不是文件內容）。
CitationCandidate 的 `provenance` dict 可以選擇性地帶 `artifact_sha256`，
但本模組**不**依賴它存在；呼叫端必須明確提供，或在 candidate 的 provenance
裡放一份。兩者都沒有就是缺失，直接拋錯。

## 這個模組不做什麼

不做 citation resolution、不做 court inference、不從 `JID` 位置解析。
它只把已經存在的 derived object 轉成標準 provenance 形狀。
"""
from __future__ import annotations

from dataclasses import dataclass

# 延遲 import：避免 import 時就拉起整個 judgment 模組樹。
# 這個模組的職責很單純，不需要在 import 時建立 heavy 依賴。


JSON_FIELD = "JFULL"


class ProvenanceError(ValueError):
    """provenance chain 缺少必要欄位，或物件類型不被支援。"""


@dataclass(frozen=True)
class ProvenanceChain:
    """一筆 derived object 的完整來源鏈。frozen。"""

    artifact_sha256: str
    entry_path: str
    json_field: str
    start_offset: int
    end_offset: int

    def as_dict(self) -> dict:
        return {
            "artifact_sha256": self.artifact_sha256,
            "entry_path": self.entry_path,
            "json_field": self.json_field,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
        }


def _as_int(value, name: str) -> int:
    """把 offset 轉成 int；失敗或為負都拋 ProvenanceError。"""
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ProvenanceError(f"{name} 必須是整數，收到 {value!r}") from exc
    if n < 0:
        raise ProvenanceError(f"{name} 不可為負數，收到 {n}")
    return n


def chain(
    obj,
    *,
    artifact_sha256: str | None = None,
) -> ProvenanceChain:
    """把 derived object 轉成標準 provenance chain。

    ## 支援的物件類型

    * `chunk.Chunk`
    * `citations.CitationCandidate`

    ## 參數

    * `artifact_sha256` —— 必須提供，或存在於 `CitationCandidate.provenance`
      的 `artifact_sha256` 鍵。空字串視為缺失。

    ## 回傳

    `ProvenanceChain`（可用 `.as_dict()` 轉成 plain dict）。

    ## 缺失欄位

    任何必要欄位缺失或無效 → `ProvenanceError`。
    """
    # 區域 import：讓本模組在沒有完整 judgment tree 時也能被載入
    import chunk as _chunk
    import citations as _citations

    entry_path: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None

    if isinstance(obj, _chunk.Chunk):
        entry_path = obj.source_document
        start_offset = obj.start_offset
        end_offset = obj.end_offset
    elif isinstance(obj, _citations.CitationCandidate):
        entry_path = obj.source_document
        start_offset = obj.start_offset
        end_offset = obj.end_offset
        if artifact_sha256 is None:
            artifact_sha256 = obj.provenance.get("artifact_sha256")
    else:
        raise ProvenanceError(
            f"不支援的物件類型：{type(obj).__name__}；"
            "只接受 Chunk 與 CitationCandidate"
        )

    if not artifact_sha256:
        raise ProvenanceError("缺少 artifact_sha256")
    if not entry_path:
        raise ProvenanceError("缺少 entry_path")

    start = _as_int(start_offset, "start_offset")
    end = _as_int(end_offset, "end_offset")
    if end < start:
        raise ProvenanceError(
            f"end_offset ({end}) 不可小於 start_offset ({start})"
        )

    return ProvenanceChain(
        artifact_sha256=artifact_sha256,
        entry_path=entry_path,
        json_field=JSON_FIELD,
        start_offset=start,
        end_offset=end,
    )


def to_dict(obj, *, artifact_sha256: str | None = None) -> dict:
    """`chain(...).as_dict()` 的便利形式。"""
    return chain(obj, artifact_sha256=artifact_sha256).as_dict()
