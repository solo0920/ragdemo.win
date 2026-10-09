#!/usr/bin/env python3
"""判決文件的不可變記憶體記錄（spec 004 T009 / FR-002, FR-016, FR-028）。

## 這份模組做什麼

把一份已驗證的 source JSON 變成一個**不可變的記錄**，其中每個字串都與
來源**逐 byte 相同**。

它不做任何加工。這不是簡潔，是這份模組存在的全部理由 —— 每一個「順手」的
改動（strip、小寫化、日期格式化、從 JID 拆出 year）都會讓 downstream 無法
再回到來源，而那正是本 feature 要保證的事。

## 為什麼 `jid` 沒有 `.year` / `.case` / `.no`

`spec.md` FR-028：`JID` MUST be stored as an opaque composite string，
positional parsing is forbidden。它合法地有 5-field 與 6-field 兩種形狀，
而兩者**編碼不同的文件類別**（139 筆 5-field 全部是憲法法庭）。

一旦記錄上存在 `.year`，任何人都會用它 —— 而它對 5-field 那批根本不存在。
更糟的是它「大部分時候是對的」（108,409 筆裡只有 139 筆不是），所以錯誤
會以「偶爾怪怪的」形式出現，難以追溯。

這個模組裡**沒有**任何 `jid_fields()` 之類的函式。要拆就必須另外寫一個
模組，並且明確承擔 FR-028 的責任 —— 那不該是順手可做的事。

## 欄位為什麼是這些名字

`jid` / `jyear` / `jcase` / `jno` / `jdate` / `jtitle` / `jfull` / `jpdf`
是 source 的 key 名**去掉前綴大寫**之後的原樣，不是發明的新名字。FR-016
禁止 synthesise 一個「可能被誤認為司法內容」的 metadata 欄位；把
`JID` 叫 `jid` 是同一個字串，不是合成。

## 記錄上沒有 derived 欄位

沒有 `year`、`court`、`case_type`、`text_length`（那個在 T010 的
`LosslessText` 上）、沒有 `chunks`、沒有 `summary`。這個記錄只裝**來源說了
什麼**，不含任何推論。T022 的 citation 顯示需要更多欄位時，那是 T022 的
決定，不是這裡預先墊上去的。

## frozen 的實際意義

`@dataclass(frozen=True)` 讓 attribute assignment 直接拋錯。理由不是風格：
pipeline 中段若有程式不小心改了 `jfull`，儲存出去的內容就跟來源不一致，
而**沒有任何地方會再驗一次**（驗證發生在入口）。讓改不動是比「記得不要改」
更可靠的機制。
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

import schema as _schema


class DocumentError(ValueError):
    """建立記錄時的輸入問題（schema drift、或缺 provenance）。

    這與 `schema.Drift` 不同：schema validator 的職責是**回報**drift，
    而這個 constructor 的職責是**拒絕**建立一個不誠實的記錄。呼叫端若想
    繼續處理一份 drift 的資料，應先處理 drift，而不是繞過這裡。
    """


@dataclass(frozen=True)
class JudgmentDocument:
    """一份判決的來源欄位，逐 byte 原樣。

    frozen：attribute assignment 會拋 `FrozenInstanceError`。
    """

    # ── 來源欄位（原樣，型別 str）──
    jid: str        # opaque composite；**不可**位置解析（FR-028）
    jyear: str      # 案件年份；與 jdate 獨立，不可互相推導（FR-029）
    jcase: str      # opaque 代碼；不是 court、不是案件類型
    jno: str
    jdate: str
    jtitle: str
    jfull: str      # 唯一 authoritative 全文來源
    jpdf: str       # 可能為空，且那是合法的（FR-030）

    # ── provenance（來源定位，不是司法內容）──
    entry_path: str      # archive 內的路徑，反斜線，與 inventory 一致
    sha256: str          # 該 entry 的解壓 bytes 的 SHA-256

    @property
    def keys(self) -> tuple[str, ...]:
        """來源 JSON 的 key 名 —— 記錄自身的對應表，不是新發明的名字。"""
        return _schema.CANONICAL_KEYS

    def source_dict(self) -> dict[str, str]:
        """還原成 source JSON 的樣子（key 名大寫、值原樣）。

        這是**往返**工具，不是正規化：`source_dict()["JFULL"] == jfull`
        恆成立。之所以提供，是因為下游需要一個能交給 JSON serializer 的
        形狀，而讓它們各自拼 key 名只會產生拼錯的機會。
        """
        return {
            "JID": self.jid,
            "JYEAR": self.jyear,
            "JCASE": self.jcase,
            "JNO": self.jno,
            "JDATE": self.jdate,
            "JTITLE": self.jtitle,
            "JFULL": self.jfull,
            "JPDF": self.jpdf,
        }

    def as_provenance(self) -> dict:
        """不含 `jfull` 的識別資訊 —— 給 manifest / log 用。

        刻意不含 `jfull`：一份 listing 若帶全文，108,409 筆就是幾百 MB，
        而且 listing 的用途是「有哪些文件」，不是「它們說了什麼」。
        """
        return {
            "jid": self.jid,
            "entry_path": self.entry_path,
            "sha256": self.sha256,
            "jyear": self.jyear,
            "jcase": self.jcase,
            "jno": self.jno,
            "jdate": self.jdate,
            "jtitle": self.jtitle,
            "has_jpdf": self.jpdf != "",
        }

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        """記錄自身的欄位名（不含 properties）。"""
        return tuple(f.name for f in fields(cls))


def from_document(
    doc: dict[str, Any],
    *,
    entry_path: str,
    sha256: str,
) -> JudgmentDocument:
    """從**已驗證**的 JSON dict 建立記錄。

    ## 為什麼內部再做一次驗證

    呼叫端可能已經跑過 `schema.validate()`，那麼這次是重複的。但記錄的
    契約是「每個字串都與來源逐 byte 相同」—— 若是因為呼叫端忘了驗證而建立
    一份帶 `None` 的記錄，那個錯誤會一路走到儲存層才爆，而且爆在離原因
    很遠的地方。重驗的成本是幾次 dict lookup。

    `entry_path` 與 `sha256` 是**必填**：沒有它們的記錄無法被追溯回來源，
    而 FR-008 要求每個 stage 都留下可追溯的 fingerprint。

    不接受「產生預設值」的選項 —— 那正是 FR-016 禁止的 synthesize。
    """
    result = _schema.validate(doc)
    if not result.valid:
        raise DocumentError(f"文件不符 8-key 契約：{result.reason}")

    for name, value in (("entry_path", entry_path), ("sha256", sha256)):
        if not isinstance(value, str) or not value:
            raise DocumentError(f"{name} 必須是非空字串")

    # 逐欄位取值，不做 `doc.get(...) or ""` 之類的預填。
    return JudgmentDocument(
        jid=doc["JID"],
        jyear=doc["JYEAR"],
        jcase=doc["JCASE"],
        jno=doc["JNO"],
        jdate=doc["JDATE"],
        jtitle=doc["JTITLE"],
        jfull=doc["JFULL"],
        jpdf=doc["JPDF"],
        entry_path=entry_path,
        sha256=sha256,
    )


def from_extracted(
    extracted,
    *,
    entry_path: str | None = None,
) -> JudgmentDocument:
    """從 T005 的 `ExtractedEntry` 建立記錄。

    `extracted` 是 `extract.ExtractedEntry`（或任何具有 `.data` / `.path` /
    `.sha256` 的物件）。這裡做的是「解析 JSON + 驗證 + 建立記錄」三步，
    **不解讀**內容 —— `JFULL` 仍然是那個字串。

    刻意不在這裡接受 `text` / `content` 之類的替代屬性名：那會讓呼叫端
    傳錯東西時不易察覺。
    """
    import json

    data = extracted.data
    if not isinstance(data, (bytes, bytearray)):
        raise DocumentError(
            f"extracted.data 必須是 bytes，收到 {type(data).__name__}"
        )

    try:
        doc = json.loads(bytes(data).decode("utf-8"))
    except UnicodeDecodeError as e:
        raise DocumentError(f"解壓結果不是合法 UTF-8：{e}") from e
    except json.JSONDecodeError as e:
        raise DocumentError(f"JSON 解析失敗：{e}") from e

    return from_document(
        doc,
        entry_path=entry_path if entry_path is not None else extracted.path,
        sha256=extracted.sha256,
    )
