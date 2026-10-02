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

    # 比對邏輯本身也要兩段都看：只比對完整路徑 → query/ingest/eval/rules 失效；
    # 只比對第一段 → settings/* 失效。兩邊都漏掉其中一邊都是同型漏洞。
    assert re.search(r"SENSITIVE\.has\(\s*path\s*\)", src), (
        "isSensitive() 應比對完整路徑 —— 只比第一段會讓 settings/* 失效")
    assert re.search(r"SENSITIVE\.has\(\s*path\.split\('/'\)\[0\]\s*\)", src), (
        "isSensitive() 應也比對第一段 —— 只比完整路徑會讓 query/ingest/eval/rules 失效")


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
