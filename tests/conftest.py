import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 讓測試可匯入 backend/app 與 ingest/laws 的純函式模組
for sub in ("backend", "ingest/laws"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ── 從 +server.ts 抽出宣告、丟給 node 跑的共用工具 ──────────────────────
#
# 為什麼放 conftest：這些工具要被三個測試檔共用（test_frontend_hosts、
# test_frontend_probe、test_frontend_cf_access）。各自複製一份只會漂移 ——
# 而漂移在這裡特別危險，因為抽取失敗的表現是「測試照樣綠」（見下方
# _decl_body 的說明：抽到截斷的宣告時，某些情況不會紅）。
#
# 抽出來單獨跑 node 的理由：`probe()` / `through()` / `queryRoute()` /
# `relay()` 的**真實實作**從來沒有被測過（只被 monkeypatch 掉或根本沒測）。
# 這些函式不能 import（依賴 $env/dynamic/private 等 SvelteKit 模組解析），
# 所以用「把宣告原樣貼到一支 .ts 腳本、node --experimental-strip-types 跑」
# 的方式測。

WORKER = ROOT / "frontend" / "src" / "routes" / "api" / "[...path]" / "+server.ts"


def balanced(code: str, start: int) -> str:
    """從 start 起取一段配對完整的宣告（給 `const X = new Set([...])` 用）。

    配對到收尾的 `]` / `}` 之後，還要**連帶吃掉**後面的 `)` —— 否則
    `new Set([...])` 會被截成 `new Set([...]`，貼到另一支腳本裡就是語法錯。
    """
    i = min(p for p in (code.find("{", start), code.find("[", start)) if p != -1)
    open_ch, close_ch = code[i], "}" if code[i] == "{" else "]"
    depth, j = 0, i
    while j < len(code):
        if code[j] == open_ch:
            depth += 1
        elif code[j] == close_ch:
            depth -= 1
            if depth == 0:
                k = j + 1
                if k < len(code) and code[k] == ")":
                    k += 1
                return code[start:k]
        j += 1
    raise AssertionError(f"從 offset {start} 起的括號沒配對到")


def decl_body(code: str, marker: str) -> str:
    """取出一個 function 宣告（含主體），主體用大括號配對。

    ⚠️ **兩個陷阱**，都實際踩過：

    1. 不能直接找第一個 `{` 當主體。宣告的**參數列**裡就有
       `platform?: { env?: Env }` 這種型別，第一個 `{` 是型別不是主體。
    2. 同樣地，**回傳型別**裡的大括號也會中招：
       `async function probe(url: string): Promise<{ ok: boolean; seen: string }>`
       —— 第一個 `{` 在 `Promise<` 後面。照舊版寫法會抽出一個**截斷的宣告**
       （少了主體），丟給 node 是 `Expected ',', got 'const'`。

    所以參數列配對完之後，若下一個非空白字元是 `:`，先跨過回傳型別
    （`<`/`>` 配對，並容忍 `{ }` 巢狀）再找主體的大括號。
    """
    assert marker in code, f"找不到宣告 {marker!r} —— 若被改名請同步維護呼叫它的測試"
    start = code.index(marker)

    depth, j = 0, code.index("(", start)
    while j < len(code):
        if code[j] == "(":
            depth += 1
        elif code[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    assert j < len(code), f"{marker} 的參數列沒配對到"

    k = j + 1
    while k < len(code) and code[k] in " \t":
        k += 1
    # 跨過回傳型別。正確的判準是「**第一個在泛型外的 `{`**」：
    #   `Response`                  → 沒有泛型，下一個 `{` 就是主體
    #   `Promise<Response>`         → `<` 進、`>` 出，之後的 `{` 是主體
    #   `Promise<{ ok: boolean }>`  → `<` 進去後那個 `{` 在泛型內，不是主體
    #   `Host[]` / `string | null`  → 沒有泛型也沒有 `{}`
    #
    # ⚠️ 兩個寫錯過的版本，症狀都是「抽出截斷宣告、node 報 Expected ','」：
    #   (a) 只在 `:` 後緊接著是 `<` 才跨 → `Promise<{...}>` 會停在 `<`，
    #       然後把回傳型別自己的 `{` 當成主體。
    #   (b) 一路掃到 `}` 才停 → 回傳型別是 `Response` 時會穿過主體的 `{`，
    #       把整個主體吃成「回傳型別」。
    if k < len(code) and code[k] == ":":
        gdepth = 0
        while k < len(code):
            ch = code[k]
            if ch == "<":
                gdepth += 1
            elif ch == ">":
                gdepth -= 1
            elif ch == "{" and gdepth == 0:
                break
            k += 1
        assert k < len(code), f"{marker} 找不到主體的大括號"

    depth, b = 0, code.index("{", k)
    while b < len(code):
        if code[b] == "{":
            depth += 1
        elif code[b] == "}":
            depth -= 1
            if depth == 0:
                return code[start:b + 1]
        b += 1
    raise AssertionError(f"{marker} 的大括號沒配對到")