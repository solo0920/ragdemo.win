"""Pages worker 的 `SENSITIVE` 清單 —— **它是保護面的完整定義**。

## 為什麼這份測試必須存在

`frontend/src/routes/api/[...path]/+server.ts` 的 `guard()` 開頭是：

```ts
if (!SENSITIVE.has(path)) return null;
```

也就是**不在清單裡的路徑完全不驗證 session，直接轉發出去**。所以這個清單
漏一個項目，等於那條路徑對全網開放。

2026-10-02 實測踩到：`/settings/default-model` 沒在清單裡，而
`backend/app/main.py` 的註解卻宣稱「對外路徑已由 Pages worker 的登入 guard
收著」—— **那句話對這個端點不成立**。當時只有 GET 所以看起來無害，但同一輪
加入 `export const PUT` 之後它變成**匿名可寫**：任何人都能改掉某台主機的預設
聊天模型，症狀是「查詢突然換模型」而**不會有任何錯誤**。

## 這條測試的形狀：列舉「會改變狀態的路徑」

不是列舉「該保護什麼」（那是清單自己），而是列舉**「哪些路徑會改變東西」**——
那是從另一個角度算出来的、獨立的清單。兩份清單必須相符。

這個方向很重要：若兩份都寫在同一個函式裡、互相參照，就會有「測試通過但保護
其實不存在」的情形 —— 那正是 `test_env_sync_heredoc.py` 記錄過的教訓
（掃描器抓不到東西時整份測試靜靜空轉）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROXY = ROOT / "frontend" / "src" / "routes" / "api" / "[...path]" / "+server.ts"

# 必須登入才能呼叫的路徑。從「有哪些寫入／燒額度的端點」推出來的，
# **不是**從 guard 的清單推出來的 —— 這份獨立正是這條測試的價值。
#
# 分兩類，兩類都要擋：
#   · 會**改變狀態**（PUT／POST／DELETE 的目標）
#   · 會**動用使用者的外部資源**（不改狀態但燒額度，見 probe-clouds）
WRITING_PATHS = {
    "query",          # 會寫入 usage/quota 統計
    "ingest",         # 寫入 qdrant
    "rules",          # 新增／刪除題庫
    "eval",
    "settings/default-model",
    # 2026-10-02：後端新增的雲端 catalog 探測端點。**冪等、不改狀態**，
    # 但會真的打每個 provider 的 catalog —— 動用的是使用者的雲端額度。
    # 不擋的話任何人都能讓這台把探測打出去。
    "settings/probe-clouds",
}


def _sensitive_block() -> str:
    src = PROXY.read_text(encoding="utf-8")
    m = re.search(r"const SENSITIVE = new Set\(\[?(.*?)\]?\);", src, re.S)
    assert m, "找不到 SENSITIVE 清單 —— 改了宣告形狀請同步這裡"
    return m.group(1)


def _listed() -> set[str]:
    return set(re.findall(r"'([^']+)'", _sensitive_block()))


def test_the_proxy_file_exists_and_still_has_the_guard():
    """先確認掃描器抓得到東西 —— 否則下面全是假綠。"""
    src = PROXY.read_text(encoding="utf-8")
    assert "async function guard(" in src, "guard() 不見了 —— 保護面整個消失"
    # ⚠️ 2026-10-02：這條斷言當時寫成 `if (!SENSITIVE.has(path)) return null;`，
    # 而那段程式碼**看起來對、實際上是失效的** —— 見
    # test_the_guard_matches_nested_paths_not_only_the_first_segment。
    # 現在比對邏輯在 isSensitive()，所以這裡確認「對未列出的路徑仍直接放行」
    # 這個**語意**。⚠️ 判斷式名稱可變，所以用寬鬆比對，不要綁死字串。
    m = re.search(r"if \((.+?)\) return null;", src)
    assert m, (
        "guard() 應仍對未列出的路徑直接放行（fail-open：health／status／models "
        "必須匿名可讀）。找不到這個判斷代表形狀變了，下面兩條的前提要重新確認")
    assert m.group(1).lstrip("!").endswith("(path)"), (
        f"guard() 的放行條件 {m.group(1)!r} 看起來不是「path 是否需要登入」—— "
        "請確認它仍然只由路徑決定（換成別的條件會讓清單失效）")
    assert re.search(r"export const (GET|POST|PUT|DELETE): RequestHandler", src), \
        "找不到轉發 handler —— 這個檔的形狀變了"


def test_the_guard_matches_nested_paths_not_only_the_first_segment():
    """⚠️ 清單裡的字串必須真的被比對到。

    2026-10-02 的實際狀況：`SENSITIVE` 裡已經有 `settings/default-model`，
    但 `guard()` 的呼叫端傳的是 `parsed(path)`（只取第一段），於是比對成
    `settings`、**永遠不命中** —— 清單裡有那個字串等於沒寫。把
    `export const PUT` 加上去之後，那條路徑是**匿名可寫**，而且沒有任何錯誤。

    所以「清單裡有這個字串」不足以證明有保護；要比對的真的是完整路徑。
    這條測試釘的是**比對方式**，不是清單內容（內容由上面兩條負責）。
    """
    src = PROXY.read_text(encoding="utf-8")
    for verb in ("GET", "POST", "PUT"):
        m = re.search(rf"export const {verb}: RequestHandler", src)
        assert m, f"找不到 {verb} handler"
        # 從 handler 起算到下一個 export，涵蓋整個函式主體。
        nxt = re.search(r"export const \w+: RequestHandler", src[m.end():])
        span = src[m.start(): m.end() + (nxt.start() if nxt else len(src))]
        assert "guard(request, params.path)" in span, (
            f"{verb} handler 沒把**完整路徑**傳給 guard()。"
            "只傳第一段（parsed(path)）會讓 settings/default-model 與 "
            "settings/probe-clouds 永遠不命中 SENSITIVE —— 2026-10-02 的漏洞"
        )
        assert not re.search(r"guard\(\s*request,\s*parsed\(", span), (
            f"{verb} handler 仍把 parsed(path)（只取第一段）餵給 guard()")

    # 比對邏輯本身：⚠️ **不要 grep 原始碼，直接把函式抽出來真的跑。**
    #
    # grep 只能證明「寫了什麼」，證明不了「每個輸入的結果對不對」，而這個函式
    # 真正的要求就是結果。這一版的 grep 寫法（`SENSITIVE.has(path)`）對一個
    # **有漏的**實作回綠 —— `settings/default-model/`（尾隨斜杠）會穿過去，
    # 那個漏是三環實測出來的。**測行為，不測字串。**
    got = _run_is_sensitive([
        "query", "ingest", "eval", "rules",
        "settings/default-model", "settings/probe-clouds",
        "settings/default-model/", "settings/probe-clouds/",
        "settings/default-model/extra", "settings/probe-clouds/force",
        "health", "status", "models", "settings", "settings/xyz", "auth/me",
    ])
    for p in ("query", "ingest", "eval", "rules",
              "settings/default-model", "settings/probe-clouds",
              "settings/default-model/", "settings/probe-clouds/",
              "settings/default-model/extra", "settings/probe-clouds/force"):
        assert got[p], (
            f"`{p}` 沒被擋下 —— 匿名可呼叫。"
            "只比對單一段就會漏掉 settings/*；只比字串相等會漏掉尾隨斜杠與子路徑")
    for p in ("health", "status", "models", "settings", "settings/xyz", "auth/me"):
        assert not got[p], (
            f"`{p}` 被擋了，但它是刻意匿名可讀的 —— 同儕面板與 wait-stack.sh 依賴它。"
            "（guard() 對未列出的路徑 fail-open 是刻意的）")


def test_every_writing_path_is_login_guarded():
    """**每個會改變狀態的路徑都必須在 `SENSITIVE` 裡。**

    漏一個 = 該路徑匿名可存取。這是 2026-10-02 `settings/default-model` 的實際狀況。
    """
    listed = _listed()
    missing = sorted(WRITING_PATHS - listed)
    assert not missing, (
        f"這些路徑會改變狀態但沒有登入保護（`guard()` 對未列出的路徑直接放行）: "
        f"{missing}\n"
        f"   目前已列出: {sorted(listed)}")


def test_the_backend_comment_does_not_overclaim_protection():
    """**後端註解不可宣稱一個實際不成立的保護。**

    `main.py` 原本寫「對外路徑已由 Pages worker 的登入 guard 收著」——
    對 `/settings/default-model` 那句話不成立（它沒在 `SENSITIVE` 裡）。
    這種註解比沒有更糟：它讓讀的人**不再去查**。

    所以這條測試要求：只要註解提到 worker 的 guard，就必須在同一段裡說清楚
    這個端點**確實在**那份清單裡 —— 而且本測試已經證明它在了。
    """
    main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    for i, line in enumerate(main.splitlines(), 1):
        if "worker 的登入 guard" not in line and "worker 登入 guard" not in line:
            continue
        window = "\n".join(main.splitlines()[max(0, i - 8):i + 8])
        assert "settings/default-model" in window or "SENSITIVE" in window, (
            f"main.py:{i} 宣稱對外路徑受 worker guard 保護，但同一段沒有指明"
            f"這個端點在 `SENSITIVE` 清單裡 —— 而那正是 2026-10-02 實際不成立"
            f"的地方。註解宣稱一個沒有驗證過的保護，比沒有註解更糟："
            f"它讓讀的人不再去查。")


def test_design_doc_agrees_with_the_guard_list():
    """`DESIGN.md` 的同一句宣稱也要一致。

    兩份文件寫同一件事、其中一份過期，是這個專案反覆在收的問題
    （本專案的 env-prune 行號、.env.example 讀取位置都踩過同一型）。
    """
    doc = (ROOT / "backend" / "DESIGN.md").read_text(encoding="utf-8")
    for i, line in enumerate(doc.splitlines(), 1):
        if "worker 登入 guard" not in line and "worker 的登入 guard" not in line:
            continue
        window = "\n".join(doc.splitlines()[max(0, i - 8):i + 8])
        assert "settings/default-model" in window or "SENSITIVE" in window, (
            f"DESIGN.md:{i} 的保護宣稱沒有指明 settings/default-model 在 "
            f"`SENSITIVE` 裡 —— 與實際不符")


def test_trailing_slash_and_subpath_cannot_bypass_the_guard():
    """**尾隨斜杠與子路徑都必須被擋** —— 這是 2026-10-02 三環實測出來的漏洞。

    完整鏈路，每一環都實測過（不是推論）：

    1. SvelteKit 為 `/api/[...path]` 產生的 pattern（抄自
       `.svelte-kit/output/server/manifest-full.js`）是
       `/^\\/api(?:\\/([^]*))?\\/?$/` —— 尾隨 `\\/?` 讓
       `/api/settings/default-model/` 也匹配，而且 **`params.path` 保留那個斜杠**
       （實測 = `"settings/default-model/"`）。
    2. `SENSITIVE.has("settings/default-model/")` 是 **false** → guard **放行**。
    3. 後端對 `PUT /settings/default-model/` 回 **307**（PUT 保留方法與 body）
       → **匿名寫入成功**。curl 實測：值真的被改掉。

    所以比對**不能**是字串相等，必須是**逐段比對**：清單某條 = 本路徑的前綴段序列。

    這條同時檢查**兩個方向**：擋得住漏掉的那些，也**沒有**因為修得太寬而把刻意
    匿名可讀的路徑擋掉 —— 那會讓同儕面板整片掛掉，而且沒有任何錯誤訊息。
    """
    got = _run_is_sensitive([
        "settings/default-model/", "settings/probe-clouds/",
        "settings/probe-clouds/force", "settings/default-model/x/y",
        "settings", "settings/", "settings/xyz", "health", "models", "status",
    ])
    for p in ("settings/default-model/", "settings/probe-clouds/",
              "settings/probe-clouds/force", "settings/default-model/x/y"):
        assert got[p], f"`{p}` 穿過 guard —— 尾隨斜杠／子路徑的漏（見 docstring 的三環）"
    for p in ("settings", "settings/", "settings/xyz", "health", "models", "status"):
        assert not got[p], f"`{p}` 被誤擋 —— fail-open 是設計要求，不能收窄"
    # 別修成「只要第一段在清單就擋」—— 那會把 settings/xyz 也擋掉
    assert not got["settings/xyz"], "第一段比對會誤擋 settings/xyz"


def test_guard_actually_calls_is_sensitive():
    """**`guard()` 必須真的呼叫 `isSensitive()`。**

    ⚠️ 同一個 bug 已經發生過兩次，形狀都一樣：清單是對的、函式是對的、測試也全綠，
    但**比對邏輯沒接到實際執行的路徑上**。

    | 輪次 | 樣子 | 症狀 | 為什麼測試沒抓到 |
    |---|---|---|---|
    | 1 | `guard(request, parsed(path))` | `settings/*` 永不命中 → **匿名可寫** | 只驗 Set 的內容 |
    | 2 | `isSensitive()` 定義了但沒人呼叫 | 多一層的路徑穿過去 | 只驗函式本體寫對 |

    第 2 次特別陰險：**功能上看起來沒壞**（當時清單裡 6 條，完整路徑都剛好命中），
    所以 84 條測試全過。而 guard() 是 **fail-open** —— 漏接的症狀是匿名可呼叫，
    沒有錯誤、沒有日誌、沒有任何徵兆。

    所以這條**不驗**「`isSensitive()` 寫對了嗎」（上面那條驗），只驗**接線**。
    """
    import re as _re
    src = PROXY.read_text(encoding="utf-8")
    m = _re.search(r"async function guard\(.*?\n\}(?=\n)", src, _re.S)
    assert m, "guard() 抓不到（寫法變了？）"
    body = m.group(0)

    # ⚠️ **先剝掉註解再斷言**，否則這條測試會自己打自己：
    #   · 斷言「有呼叫 isSensitive」時，**只有註解提到**也算過（和 bug #2 同形狀）
    #   · 斷言「不得直接 SENSITIVE.has」時，**為這個 bug 寫的註解裡正好有那句**
    #     → 紅 → 有人會「修」成刪註解 → 知識沒了，bug 留著
    # 兩種結局都比沒有這條測試糟。
    code = _re.sub(r"/\*.*?\*/", " ", body, flags=_re.S)
    code = _re.sub(r"//[^\n]*", " ", code)

    assert "isSensitive(path)" in code, (
        "guard() 的**可執行碼**沒有呼叫 isSensitive() —— 那個函式就是死代碼。\n"
        "  放行條件寫成 `SENSITIVE.has(path)` 只比完整路徑，"
        "`settings/probe-clouds/force` 這類子路徑會穿過去；"
        "而 guard() 是 fail-open，穿過去就是匿名可呼叫。")
    assert not _re.search(r"SENSITIVE\.has\(\s*path\s*\)", code), (
        "guard() 又改回直接 `SENSITIVE.has(path)` —— 那是 bug #2 的形狀")

    cond = _re.search(r"if\s*\((.+)\)\s*return\s+null", code)
    assert cond, "guard() 的放行條件抓不到"
    assert "isSensitive" in cond.group(1), (
        f"放行條件 {_re.escape(cond.group(1))!r} 不是 isSensitive(...) —— "
        "放行判斷和 SENSITIVE 之間又脫鉤了")


def _run_is_sensitive(paths):
    """把**真實原始碼**裡的 `SENSITIVE` 與 `isSensitive()` 抽出來，用 node 真的跑。

    為什麼不用 grep 斷言「寫了什麼」：grep 證明不了**每個輸入的結果** —— 而這個
    函式真正的要求就是結果。真的跑一次，才會發現「寫法對」與「結果對」是兩件事
    （第一版就是寫法對、結果漏）。
    """
    import json
    import os
    import shutil
    import subprocess
    import tempfile
    if shutil.which("node") is None:
        pytest.skip("沒有 node，跑不了前端函式")

    src = PROXY.read_text(encoding="utf-8")
    set_m = re.search(r"const SENSITIVE = new Set\(\[([\s\S]*?)\]\)", src)
    fn_m = re.search(r"function isSensitive\([^)]*\)\s*:\s*boolean \{[\s\S]*?\n\}", src)
    assert set_m and fn_m, "抽不出 SENSITIVE / isSensitive()（寫法變了？）"

    fn_src = fn_m.group(0)
    for t in (": string", ": boolean"):          # TS 型別標註拿掉才能跑
        fn_src = fn_src.replace(t, "")
    args = ", ".join(json.dumps(x) for x in paths)
    script = (
        "const SENSITIVE = new Set([" + set_m.group(1) + "]);\n"
        "const isSensitive = (" + fn_src + ");\n"
        "const out = {};\n"
        f"for (const p of [{args}]) out[p] = isSensitive(p);\n"
        "console.log(JSON.stringify(out));\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(script)
        tmp = fh.name
    try:
        r = subprocess.run(["node", tmp], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(tmp)
    assert r.returncode == 0, f"node 執行失敗：\n{r.stdout}\n{r.stderr}"
    return json.loads(r.stdout)
