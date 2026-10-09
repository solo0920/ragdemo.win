#!/usr/bin/env python3
"""chunk → Qdrant points 的 payload 建構（spec 004 T017 / FR-013, FR-014）。

## 這個檔案做什麼、不做什麼

**做**：把 `chunk.Chunk` 轉成 Qdrant point 的 payload，並決定 deterministic
point ID。

**不做**：不連線、不送向量。embedding 是「定位原文的工具」，不是原文的替代
品（FR-013）—— 而產生向量屬於這個 batch 之後的事。

這個分離與 `ingest/laws/qdrant_load.py` 的結構刻意一致：payload 建構是純函式，
可以被測；I/O 需要 Qdrant 在線，不被測。

## point ID 必須 deterministic

acceptance 的原文：**「re-running overwrites rather than duplicates」**。

若 point ID 隨機，每次重跑就是一份新資料 —— 舊的留著，新的進來，檢索會回傳
同一段文字的兩份，而使用者看不出哪一份是新的。

ID 的組成是 `sha256(entry_path | chunk_index)`（由 `chunk.Chunk.point_id` 提供）。
這個組合滿足三個條件：

* **deterministic** —— 沒有時間戳、沒有亂數、沒有行程 ID。
* **穩定** —— 同一份文件重新分塊（chunk_size 相同），ID 完全一樣。
* **唯一** —— 不同 entry 或不同 index 一定不同。

刻意**不含** `jfull_sha256`：若把它放進去，一份被重新發布的判決（內容變了）
會得到全新的 ID，而舊的 points 會變成孤兒 —— 那需要另一個刪除流程，而那是
T016 的 delete policy 決定（尚未決定）。不含它則內容變更時會**覆寫同一個
point**，那是正確行為。

## payload 裡有什麼、沒什麼

**有**（FR-014：必須帶來源實際提供的欄位）：

```
entry_path, jid, chunk_index, start_offset, end_offset,
content_hash, jyear, jdate, jcase
```

`jyear` / `jdate` 是**兩個獨立欄位**（FR-029）—— 不可合成一個，也不可互相
推導。`jcase` 是 opaque 代碼，不是案件類型。

**沒有**：

* `statute` / `statute_name` —— FR-037 禁止在偵測階段指派法條身分。
* `court` —— source 沒有這個欄位；推論它是 FR-016 禁止的 synthesize。
* `case_type` / `branch` —— 同上，`jcase` 是 opaque。
* `score` / `rank` —— ranking 屬於 T019+，而且那應該是**查詢時**算出來的，
  不是存進去的。

## citation candidate 不在這裡

T012–T014 產生了 citation candidates，但它們是**從 chunk 的文字位置推導**的
derived 資料。把它們存進 payload 會造成一個誘惑：有人以為 payload 裡的
citation 是查證過的法律引用。

實際上它們是「這段文字裡有形狀像引用的東西」。若要儲存，應該存成
`candidate` 的**位置與 matched text**，並且明確標記未解析 —— 那屬於後續的
設計決定，不是這裡該順手加的。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from chunk import Chunk  # noqa: E402

COLLECTION = "judgements"

# payload 的欄位集合 —— 這是**契約**，不是建議。
#
# 用 tuple 而不是 dict：欄位集合需要能被逐字比對，且不允許在程式中被意外
# 加入。`tests/test_judgement_qdrant_payload.py` 斷言它等於這個清單。
PAYLOAD_FIELDS: tuple[str, ...] = (
    "entry_path",
    "jid",
    "chunk_index",
    "start_offset",
    "end_offset",
    "content_hash",
    "jyear",
    "jdate",
    "jcase",
)

# payload **不得**出現的欄位。理由見模組 docstring。
# 這份清單本身是可執行的契約：日後有人加欄位時，這裡不會自動更新，所以
# 測試會紅 —— 那正是它該做的事（讓「加了一個推論欄位」變成需要說明的決定）。
FORBIDDEN_FIELDS: tuple[str, ...] = (
    "statute",
    "statute_name",
    "law",
    "court",
    "case_type",
    "branch",
    "score",
    "rank",
    "embedding",
    "summary",
)


def build_payload(chunk: Chunk, doc) -> dict:
    """`Chunk` + `JudgmentDocument` → Qdrant payload。

    純函式：不連線、不送向量。

    `doc` 提供那些**文件層級**的來源欄位（`jyear` / `jdate` / `jcase`）——
    它們不重複存在於每個 chunk 裡，但那正是 FR-014 要求 payload 帶的東西：
    檢索時不必回頭 join 文件表就能做 faceting。

    刻意**不含** `jfull`：payload 裡放全文會讓每個 point 都攜帶整份判決
    （777 KB），collection 體會爆炸。chunk 自己的 `text` 才是要檢索的片段。
    """
    return {
        "entry_path": doc.entry_path,
        "jid": doc.jid,
        "chunk_index": chunk.chunk_index,
        "start_offset": chunk.start_offset,
        "end_offset": chunk.end_offset,
        "content_hash": chunk.sha256,
        "jyear": doc.jyear,
        "jdate": doc.jdate,
        "jcase": doc.jcase,
    }


def point_id(chunk: Chunk) -> str:
    """Qdrant point ID。

    Qdrant 只接受 unsigned int 或 UUID。`chunk.Chunk.point_id` 算的是 64-hex
    字串 —— 那個**形狀**可以直接餵給 Qdrant 的 UUID 欄位（Qdrant 接受
    標準 UUID 字串），但本模組**不做**這個轉換。

    理由：ID 的**形狀**是 Qdrant 的 API 約束，而 `chunk.py` 不該知道 Qdrant
    的存在。目前直接回傳 64-hex 是可用的（Qdrant 接受 32-hex 的無 hyphen
    UUID 形式）。若日後 Qdrant 改了要求，改的是這個函式。
    """
    return chunk.point_id


def build_point(chunk: Chunk, doc) -> dict:
    """完整的 point：`{"id", "payload"}`。

    **沒有 `vector` 欄位** —— embedding 是 T017 之後的事，而 FR-013 規定
    embedding 只能用來定位原文、不能替代原文。加上它之前，這裡刻意不給
    一個空陣列去「看起來完整」：那會讓一個沒有向量的 point 被寫進去，
    而那在檢索時是靜默失效的。
    """
    return {"id": point_id(chunk), "payload": build_payload(chunk, doc)}


def assert_payload_contract(payload: dict) -> None:
    """驗證一份 payload 符合契約。

    這是**可呼叫的**，不只是測試用 —— 呼叫端在送出前可以自己驗一次，而不必
    依賴「測試有覆蓋到」。
    """
    keys = set(payload)

    # 先查禁止欄位：若一個 payload 多了 `statute_name`，報「欄位不符」是
    # 正確但不**有用**的訊息 —— 維護者會去比對欄位清單，而真正的問題是那個
    # 欄位根本不該存在。分開查讓兩種錯誤各自可讀。
    forbidden = keys & set(FORBIDDEN_FIELDS)
    if forbidden:
        raise PayloadContractError(
            f"payload 含禁止欄位 {sorted(forbidden)} —— "
            "statute/court 類欄位會被誤認為已查證的司法內容"
        )

    if keys != set(PAYLOAD_FIELDS):
        raise PayloadContractError(
            f"payload 欄位不符：多了 {sorted(keys - set(PAYLOAD_FIELDS))}，"
            f"少了 {sorted(set(PAYLOAD_FIELDS) - keys)}"
        )


class PayloadContractError(ValueError):
    """payload 不符合 T017 的契約。"""


def collection_size_hint(doc_count: int, chunks_per_doc: int) -> int:
    """預估 point 數量 —— 只是提示，不做任何保證。

    刻意不去算真實的 chunk 數（那需要跑完整個 pipeline）；這個函式的用途是
    讓容量規劃有個量級感。
    """
    return doc_count * chunks_per_doc
