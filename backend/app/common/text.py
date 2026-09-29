"""文字正規化：兩個語意不同、但過去都叫 `_cw` 的函式。

⚠️ 這兩個函式**不是**重複程式碼，別再合併成一個：
- `collapse_ws()` 壓成單一空格，給顯示用（法規名稱、備註、歷史說明）。
- `squash()` 連空格都刪掉，給「有沒有空格都算同一題」的比對用。

過去 rag.py 與 rules_store.py 各定義一個 `_cw`，同名卻語意不同（其中一個
`re.sub(r"\\s+", "")`、另一個 `re.sub(r"\\s+", " ").strip()`）。誰在重構時看到
「兩個一樣的 `_cw`」而刪掉一份，比對結果會靜默改變。分開命名就是為了防這個。
"""
import re

_WS_RUN = re.compile(r"\s+")


def collapse_ws(s: str) -> str:
    """連續空白（含全形）壓成單一空格並去頭尾。給顯示用。"""
    return _WS_RUN.sub(" ", s).strip()


def squash(s: str) -> str:
    """移除所有空白（`None` 視為空字串）。給比對用。"""
    return _WS_RUN.sub("", s or "")
