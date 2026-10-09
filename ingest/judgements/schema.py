#!/usr/bin/env python3
"""判決 JSON 的 8-key schema 契約與 drift 偵測（spec 004 T008 / FR-024, FR-030, FR-B02）。

## 這份模組做什麼

回答一個問題：**這份 source JSON 符合我們已驗證的 8-field 契約嗎？**

回傳 `Valid` 或 `Drift(reason)` —— **絕不拋例外**。這是刻意的：drift 是資料的
狀態，不是程式的錯誤。一個會拋例外的 validator 會讓呼叫端只能選擇「crash」
或「catch 後忽略」，而後者正是我們要避免的 —— drift 被吞掉的時候沒有人會知道。

## 契約的每一条都來自實測，不是設計

`spec.md` §Source Structure Contract 記錄了 494/494 的觀察：flat dict、8 個
key、順序穩定、全為字串、無 null。這個模組只把那些**已驗證的事實**寫成
斷言，不多加一條自己的判斷。

## 三個「合法但反直覺」的情況

這三個是 spec 明確列為 anomaly 的資料類型。把它們當成 schema error 會讓
139 筆憲法法庭資料加上 1 筆 `JYEAR="79"` 的真實判決被丟掉 —— 而它們都是合法
的司法資料：

1. **`JID` 有 5-field 與 6-field 兩種形狀。** 5-field 的 139 筆全部是憲法
   法庭（`JCCC,115,審裁,1158,20260701`，沒有最後的機構碼）。依欄位數判斷
   「這筆壞了」是 FR-028 明確禁止的 —— 那正是 positional parsing。
2. **空的 `JPDF` 不是錯誤。** 139/139 憲法法庭資料沒有 PDF（FR-030）。
3. **`JYEAR="79"` 不是錯誤。** ROC 79 年的案件出現在 ROC 115 的發布批次裡。
   這是真實資料，不是筆誤；spec 明令 MUST NOT be auto-corrected。

## 為什麼不做 semantic interpretation

`JCASE` 是一個 opaque 代碼（`重秩`、`訴`、`簡`），不是「案件類型」也不是
「法院」。本模組**只驗型別與 key 集合**，不驗 `JCASE` 的值域、不驗
`JYEAR` 的長度範圍、不驗 `JDATE` 的格式。

理由：`corpus-schema-survey.md` 觀察到 corpus-wide 有 1,336 個不同的
`JCASE`，而我們只看了 0.46%。任何值域斷言都是對未經驗證的分布下判斷 ——
而 spec 對 `court` 的位置恰好寫著 MUST NOT enter an authoritative contract。
同一個道理套用在所有欄位值上。

## 這份不做什麼

不解壓（T005）、不解讀 JSON（T009）、不做 citation detection（T012）、
不 chunk（T015）、不落地（T016）。它只接收一個已解析的 dict。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# 已驗證的 key 集合（spec §Source Structure Contract，494/494 只有這一個集合）
CANONICAL_KEYS: tuple[str, ...] = (
    "JID",
    "JYEAR",
    "JCASE",
    "JNO",
    "JDATE",
    "JTITLE",
    "JFULL",
    "JPDF",
)

# 已驗證的 key 順序。順序本身是觀察到的事實（494/494 穩定），所以驗它是
# 合理的；但**順序不符的嚴重程度低於 key 集合不符** —— 見 Severity。
CANONICAL_ORDER = CANONICAL_KEYS

# JID 的兩種合法形狀。刻意**不**解析它們的內容，只記錄「有幾個欄位」。
# 這兩個數字來自實測（6-field 主流、5-field 139 筆憲法法庭），用途是讓
# drift 報告能說「形狀改變了」，而不是讓我們去猜每個位置的意義。
JID_SHAPE_5 = 5
JID_SHAPE_6 = 6
KNOWN_JID_SHAPES = frozenset({JID_SHAPE_5, JID_SHAPE_6})


class Severity:
    """drift 的嚴重程度。

    為什麼要分級：key 集合改變會讓下游**無法讀取**這份資料（那是結構性
    破壞）；順序或值域改變只是「與我們的觀察不符」，資料仍可讀取。兩者混在
    一起會讓呼叫端無法決定該「擋掉」還是「記錄後繼續」。
    """

    STRUCTURAL = "structural"   # 資料無法被讀取 —— 必須擋
    OBSERVATIONAL = "observational"  # 仍可讀取，但與已驗證事實不符 —— 必須報


@dataclass(frozen=True)
class Drift:
    """一份文件偏離契約的理由。**不是**例外。

    `.valid` 恆為 `False`，與 `Valid.valid` 對稱 —— 讓呼叫端可以寫
    `result.valid` 而不必先做 `isinstance`。真正的判斷仍應看 `reasons`：
    `valid` 只說「不符合」，不說「哪裡不符合」。
    """

    reasons: tuple[str, ...]
    severity: str = Severity.STRUCTURAL
    observed_keys: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return False

    @property
    def reason(self) -> str:
        """單一摘要字串 —— 給 log 與報告用。"""
        return "; ".join(self.reasons)

    def as_dict(self) -> dict:
        return {
            "valid": False,
            "reasons": list(self.reasons),
            "severity": self.severity,
            "observed_keys": list(self.observed_keys),
        }


@dataclass(frozen=True)
class Valid:
    """符合契約的文件。"""

    keys: tuple[str, ...]
    key_order_stable: bool = True

    @property
    def valid(self) -> bool:
        return True

    def as_dict(self) -> dict:
        return {"valid": True, "keys": list(self.keys), "order_stable": self.key_order_stable}


# validate() 的回傳型別
Result = Valid | Drift


def jid_shape(jid: Any) -> int | None:
    """`JID` 的欄位數 —— **只回傳數字，不解讀內容**。

    這是刻意受限的介面。FR-028 禁止 positional parsing：把 `SJEM,115,重秩,...`
    拆成 court/year/case 並用它們的語意做任何判斷，是這條規則要擋的事。

    回傳「有幾個欄位」不同於「每個欄位是什麼」。前者讓我們能報告「形狀變了」，
    後者會讓我們開始猜。`jid_fields()` 那種東西刻意**不存在**於這個模組。
    """
    if not isinstance(jid, str):
        return None
    return len(jid.split(","))


def validate(doc: Any) -> Result:
    """驗證一份已解析的 JSON 對象是否符合 8-key 契約。

    ## 不會發生的情況

    * 不拋例外（即使 `doc` 是 `None`、一個 list、或一個整數）
    * 不 coerce（`JYEAR: 115` 是 drift，不是「幫你轉成字串」）
    * 不修補（多一個 key 不會被丟掉，少一個 key 不會被補上）
    * 不猜（`JCASE` 的值不驗，`JYEAR` 的長度不驗，`JDATE` 的格式不驗）

    ## 回傳

    `Valid` 或 `Drift`。`Drift.reasons` 是**全部**理由，不會在第一個錯就停 ——
    一份壞掉的文件通常同時壞好幾處，而只報第一個會讓修的人再跑一次才看到下一個。
    """
    reasons: list[str] = []
    severity = Severity.STRUCTURAL

    if not isinstance(doc, dict):
        return Drift(
            reasons=(f"top-level 不是 object，而是 {type(doc).__name__}",),
            severity=Severity.STRUCTURAL,
        )

    observed = tuple(doc.keys())

    # ── key 集合 ──
    missing = [k for k in CANONICAL_KEYS if k not in doc]
    if missing:
        reasons.append(f"缺少 key: {','.join(missing)}")
    unexpected = [k for k in doc if k not in CANONICAL_KEYS]
    if unexpected:
        reasons.append(f"多餘 key: {','.join(unexpected)}")

    # ── key 順序（observational：仍可讀取，但與觀察不符）──
    order_stable = False
    if not missing and not unexpected:
        if observed == CANONICAL_ORDER:
            order_stable = True
        else:
            # 順序漂移是 OBSERVATIONAL，不阻擋 —— 資料仍能讀取。
            # 升級為 STRUCTURAL 的話，任何 JSON serializer 的鍵順序差異都會
            # 把整份 corpus 判成壞掉，而那不代表資料有問題。
            reasons.append(f"key 順序與已驗證順序不同: {','.join(observed)}")
            severity = Severity.OBSERVATIONAL

    # ── 值型別（缺失的 key 不重複報，避免同一問題出現兩次）──
    for key in CANONICAL_KEYS:
        if key not in doc:
            continue
        value = doc[key]
        if value is None:
            reasons.append(f"{key} 是 null")
        elif not isinstance(value, str):
            reasons.append(
                f"{key} 不是字串，而是 {type(value).__name__}"
            )

    # ── JID 的合法形狀（不解析內容）──
    if isinstance(doc.get("JID"), str):
        shape = jid_shape(doc["JID"])
        if shape not in KNOWN_JID_SHAPES:
            reasons.append(
                f"JID 的欄位數 {shape} 不在已驗證的 {sorted(KNOWN_JID_SHAPES)} 之中"
            )

    # ── 空 JPDF 不是錯誤（FR-030）──
    # 刻意**不在**這裡加任何斷言。139 筆憲法法庭資料的 JPDF 都是空字串，
    # 那是結構性事實。若哪天出現「JPDF 必須非空」的規則，這批資料會被丟掉。

    # ── JYEAR 與 JDATE 獨立（FR-029）──
    # 同樣刻意不做任何比較。它們在 80/494 的抽樣中不一致 —— 那意味著任何
    # 「兩者應該一致」的斷言都會產生大量假 drift，而這裡的職責是報告真實
    # drift，不是製造。

    if reasons:
        return Drift(
            reasons=tuple(reasons),
            severity=severity,
            observed_keys=observed,
        )
    return Valid(keys=observed, key_order_stable=order_stable)


def is_valid(doc: Any) -> bool:
    """`validate(doc).valid` 的便利形式。

    兩者都可行，但之前只提供 `.valid` 屬性時，若結果是 `Drift` 就有陷阱：
    漏寫 `isinstance` 檢查的呼叫端會在 `Drift` 上拿到 `AttributeError`
    —— 而 `Drift` 原本應該是「沒有問題」的**非錯誤**回傳值。

    順帶說明為什麼 `Drift` 也要有 `.valid` 屬性：那只是為了讓兩種結果在
    同一個屬性上可比較；`.valid == False` 恆成立。這個函式是更安全的入口。
    """
    return bool(getattr(validate(doc), "valid", False))


def parse_and_validate(payload: bytes | str) -> Result:
    """解析 JSON bytes/str 後驗證。

    **不**改變任何字元：不做 BOM 去除、不做 Unicode 正規化、不做寬鬬/窄
    半形轉換。`bytes.decode("utf-8")` 之後的結果就是 validator 看到的字串。

    解析失敗回傳 `Drift`（不是例外）—— 理由同 `validate()`：一份壞掉的來源
    資料是資料的狀態。
    """
    try:
        if isinstance(payload, bytes):
            text = payload.decode("utf-8")
        else:
            text = payload
    except UnicodeDecodeError as e:
        return Drift(
            reasons=(f"不是合法的 UTF-8: {e}",),
            severity=Severity.STRUCTURAL,
        )

    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        return Drift(
            reasons=(f"JSON 解析失敗: {e}",),
            severity=Severity.STRUCTURAL,
        )

    return validate(doc)
