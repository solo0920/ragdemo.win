import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 讓測試可匯入 backend/app 與 ingest/laws 的純函式模組
for sub in ("backend", "ingest/laws"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ── 模組層級載入 laws_flat.jsonl 的測試：沒有該檔就跳過整個檔 ────────────
#
# ## 為什麼需要這段（2026-10-10）
#
# 11 個測試檔在**檔案頂層**寫 `CORPUS = B1.StatuteCorpus.from_jsonl()`。
# 頂層 = import 時就執行，而 pytest 的 import 發生在**收集階段**。所以：
#
#   乾淨 clone（CI，沒有 laws_flat.jsonl）
#     → collection 時 FileNotFoundError
#     → 整個 pytest 退出碼非 0
#     → CI 紅，而且**其他所有測試一個都沒跑到**
#
# 症狀不是「有測試失敗」，是「測試根本沒開始跑」。2026-10-09 引入這批測試後
# CI 連續紅了 8 次，兩份 workflow（fast-ci / full-ci）都是這個原因。
#
# 本機之所以綠，是因為開發機有那個 179 MB 的檔（跑過 `sync_daily.py`）。
# **這正是 pre-push 註解裡反覆出現的那個病**：本機綠 ≠ 乾淨 clone 綠。
#
# ## 為什麼在 conftest 擋，而不是改那 11 個檔
#
# 改 11 個檔要動 11 處一模一樣的样板，而它們沒有 `import pytest`
# （11/11 確認過），得連 import 一起加。集中在一處的好處是：
#   · 新增同類測試檔時，**不需要記得**再寫一次守衛
#   · 守衛本身有單元測試（見 test_conftest_corpus_guard.py）
#
# ## 為什麼用「跳過」而不是造一個假的 laws_flat.jsonl
#
# 假的 corpus 會讓測試**綠著跑但沒驗證任何東西** —— 那比紅更糟
#（constitution VI：不得以通過的假象掩蓋未驗證）。`test_embed_batch.py:69`
# 與 `test_backup_env.py:37` 早已用同一個理由走 skipif。
#
# 這 11 個檔需要 corpus 的**全部**內容（test_b3b_eval.py:27 遍歷
# CORPUS.rows、test_b2b_resolve.py:50 用 find("民事訴訟法","第436條")），
# 造假 fixture 等於只測造出來的那部分。

#: 模組層級 `StatuteCorpus.from_jsonl()` 的測試檔。**逐一列舉，不 glob** ——
#: glob 會讓新增的檔案自動被跳過，而新增者正是最需要被提醒要加判斷的人。
CORPUS_MODULE_TESTS = frozenset({
    "test_b2b_chain.py",
    "test_b2b_extract.py",
    "test_b2b_resolve.py",
    "test_b2b_safety.py",
    "test_b2c_chain.py",
    "test_b2c_structure.py",
    "test_b2d_chain.py",
    "test_b2e_eval.py",
    "test_b2f_serve.py",
    "test_b3b_eval.py",
    "test_b4b_eval.py",
})

#: 測試函式**內部**呼叫 `b1_helpers.real_corpus()` 或 `serve_question()`
#: 而沒傳 `statute_corpus` 的檔 —— 那些會在 `b1_serve.py:436` 走到
#: `StatuteCorpus.from_jsonl()` 同樣炸掉。
#:
#: ⚠ 這一組是**量測後才補上的**，不是一次想到的。第一版只擋了上面那 11 個
#:   模組層級的檔，實測（暫時移走 laws_flat.jsonl 後跑全套）發現還有 12 個
#:   檔紅 —— 症狀不同但根因同一個。**推論模式比列舉可靠**：只要一個檔會走到
#:   `from_jsonl()` 而沒先傳 corpus，它就需要進這張表。
CORPUS_RUNTIME_TESTS = frozenset({
    # real_corpus()（b1_helpers.py:64）
    "test_b1_linking.py",
    "test_b1_evidence.py",
    # serve_question() 未傳 statute_corpus → b1_serve.py:436 走 from_jsonl()
    "test_b1_serving_demo.py",
    "test_b1_abstention.py",
    "test_b2d_abstain.py",
    "test_b3b_serve.py",
    "test_b3c_gate.py",
    "test_b4a_serve.py",
    "test_b4b_serve.py",
    "test_b4b_f1_live.py",
    "test_judgements_slice.py",
})

#: `b3b` 另外在模組層級讀 laws_meta.jsonl（同一個來源機制，缺檔會讀到 {}）。
CORPUS_META_MODULE_TESTS = frozenset({"test_b3b_eval.py"})

LAWS_FLAT = ROOT / "data" / "laws" / "laws_flat.jsonl"
LAWS_META = ROOT / "data" / "laws" / "laws_meta.jsonl"


def _needs_corpus(name: str) -> bool:
    if name in CORPUS_META_MODULE_TESTS:
        return not (LAWS_FLAT.is_file() and LAWS_META.is_file())
    if name in CORPUS_MODULE_TESTS or name in CORPUS_RUNTIME_TESTS:
        return not LAWS_FLAT.is_file()
    return False


def pytest_ignore_collect(collection_path, config):  # noqa: ARG001
    """沒有 laws_flat.jsonl 時，整個跳過那 11 個檔（不 import 它們）。

    為什麼是 `ignore_collect` 而不是 `pytest.mark.skipif`：
    skipif 是在**收集之後**才生效，而這些檔的失敗發生在**收集期間**
    （import 時執行頂層陳述句）。skipif 救不了 —— 那正是本 bug 最初的樣子：
    有人以為加個 skipif 就好，但頂層那行會先炸。

    `ignore_collect` 在 import 之前就擋掉，所以那些檔的頂層永遠不會執行。
    顯示上它們**不會**出現在報告裡（不是 "skipped"）—— 這是取捨：
    換取的是「乾淨 clone 上其他 1700+ 個測試真的跑得到」。
    """
    if _needs_corpus(collection_path.name):
        return True
    return None


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