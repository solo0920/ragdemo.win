"""前端不該把機台清單寫死在程式裡。

2026-09-27 之前這批檔案有 5 處寫死三台（worker 的 `DEFAULT_ORIGINS` 與
「API_ORIGIN 命中就偷偷展開成三台」、`+page.svelte` 的 `BACKENDS` /
`HOST_IPS` / `HOST_NAMES` / 兩處 `['x570','mbp','msi']`）。第 4 台部署的人
必須改程式才能讓自己的機器出現在 UI 上，而且會在自己只想指一台時被靜默塞進
兩台他沒有的位址。

這裡的測試是**原始碼層級**的守門，不是行為測試 —— 因為壞掉的東西正是
「常數表裡多了一個名字」，沒有任何執行期的輸入能讓它表現不同。要真的跑
worker 需要 Cloudflare 的 platform.env，本地跑不起來。

不變式拆成兩條：
  1. 前端原始碼不得出現我們的機台代號 / 網域 / 內網 IP
  2. worker 的 parseOrigins 必須真的接受 `id=url`（契約與後端一致）
     —— 這條用真的抽出函式來跑，因為格式不一致會**靜默**丟掉整段設定，
     那正是本次差一點就犯的錯。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend" / "src" / "routes" / "+page.svelte"
WORKER = ROOT / "frontend" / "src" / "routes" / "api" / "[...path]" / "+server.ts"

# 我們自己的機台代號、網域、內網位址。出現即代表有人又把清單寫回程式裡。
# ⚠️ 這個清單本身是「我們這個專案」的名單，不是通用規則 —— 它該隨專案搬家而
# 一起更新（這是刻意的：它守的是「本專案不得再列舉本專案的機器」）。
OWN_IDS = ("x570", "mbp", "msi")
OWN_DOMAINS = ("ragdemo.win",)
OWN_IPS = ("100.119.83.111", "100.64.121.9", "100.65.68.106")


def _code_only(path: Path) -> str:
    """去掉註解，只留真正會執行的程式碼。

    必要的理由：註解裡**應該**提到舊的做法（那是解釋改動理由的價值所在），
    但那會讓上面的黑名單誤判。要守的是「程式不再列舉主機」，
    不是「這個 repo 不得提及自己的主機名」。
    """
    text = path.read_text(encoding="utf-8")
    # 行註解
    text = re.sub(r"//[^\n]*", "", text)
    # 區塊註解（含 JSX 的 {/* */}，這裡只處理裸的）
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return text


# ── 不變式 1：程式碼不得列舉本專案的機器 ────────────────────

@pytest.mark.parametrize("path", [PAGE, WORKER], ids=lambda p: p.name)
def test_frontend_code_does_not_enumerate_our_hosts(path: Path) -> None:
    code = _code_only(path)
    hits: list[str] = []
    for needle in (*OWN_IDS, *OWN_DOMAINS, *OWN_IPS):
        if needle in code:
            hits.append(needle)
    assert not hits, (
        f"{path.relative_to(ROOT)} 的程式碼又出現我們的機台清單 {hits}。"
        " 機台清單是資料不是程式：後端用 HOST_API_URLS，worker 用 Pages 的"
        " API_ORIGINS，前端從 GET /status 的 known 欄位推導。"
    )


def test_worker_has_no_fallback_origins() -> None:
    """worker 不得有任何「內建後端清單」或「偷偷展開成多台」的邏輯。

    舊版 `originsOf()` 裡的 `if (DEFAULT_ORIGINS.includes(single)) return
    [...DEFAULT_ORIGINS]` 特別危險：使用者在 Pages 只設了一台，worker 卻
    會拿三台去試 —— 其中兩台他根本沒有，log 裡會出現兩個假的「連線失敗」，
    而且自動模式會嘗試打他沒有的位址。

    未設變數就必須是空清單（呼叫端回 503 說明缺什麼），不是任何內建值。
    """
    code = _code_only(WORKER)
    for banned in ("DEFAULT_ORIGINS", "api-x570", "api-mbp", "api-msi"):
        assert banned not in code, f"worker 又出現內建後端清單：{banned}"
    # 空清單必須真的導致 503，而不是某處再塞回預設值
    assert "origins.length === 0" in code, \
        "未設 API_ORIGINS 時應回 503；找不到這個判斷代表空清單會靜默通過"


def test_page_backend_picker_is_derived_from_status() -> None:
    """切換器的候選必須來自 `/status` 的 known，不能是常數表。"""
    code = _code_only(PAGE)
    assert "Object.entries(knownHosts)" in code, \
        "後端切換器應從 knownHosts 推導（knownHosts 來自 /status 的 known）"
    assert "status.known" in code, \
        "loadStatus 應把 /status 回的 known 收進 knownHosts"


def test_host_rows_are_derived_from_response_keys() -> None:
    """「主機狀態」表格的列數必須等於實際探測到的台數。

    舊版寫死 `['x570','mbp','msi']`：別台的機器一多一少，這裡就多一列假資料
    或少一列真資料 —— 而且列數不對時看起來完全正常。
    """
    code = _code_only(PAGE)
    assert re.search(r"Object\.keys\(\s*log\s*\|\|\s*\{\}\s*\)", code), \
        "hostRows 應從 log/versions 的鍵推導機台清單"
    # 版本比較也一樣：寫死三台會讓「第 4 台更新了法規」觸發不了提示
    assert re.search(r"Object\.keys\(\s*vers\s*\)", code), \
        "newestVer 應從 versions 的鍵推導"


# ── 不變式 2：worker 的解析格式必須與後端一致 ────────────────

def _extract_parse_origins() -> str:
    """把 parseOrigins 的原始碼抽出來（不重打，避免測試與實作漂移）。"""
    text = WORKER.read_text(encoding="utf-8")
    start = text.index("function parseOrigins")
    end = text.index("\n}", start) + 2
    return text[start:end]


def _run_parse_origins(raw: str, tmp_path: Path) -> list[dict]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH")
    script = tmp_path / "po.ts"
    script.write_text(
        "interface Host { id: string; url: string }\n"
        + _extract_parse_origins()
        + "\nconsole.log(JSON.stringify(parseOrigins(process.argv[2] ?? '')));\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [node, "--experimental-strip-types", str(script), raw],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        pytest.fail(f"node 執行失敗：{r.stderr[:400]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_worker_parses_id_url_format(tmp_path: Path) -> None:
    """`id=url` 必須能解析 —— 這是與後端 HOST_API_URLS 同一份契約。

    ⚠️ 這是本次實作中真實犯過的錯：先檢查 `^https?://` 再切 `id=`，
    於是 `msi=https://…` 因為開頭不是 http 而被**整段丟掉**。
    症狀極其安靜：設定明明填了，worker 卻當成沒設定。
    """
    got = _run_parse_origins("msi=https://api-msi.example.com", tmp_path)
    assert got == [{"id": "msi", "url": "https://api-msi.example.com"}]


def test_worker_parses_bare_urls_and_derives_id(tmp_path: Path) -> None:
    """純網址也要收，id 從主機名第一段推導（去掉 api- 前綴）。"""
    got = _run_parse_origins(
        "https://api-box.example.com,https://plain.example.org/", tmp_path)
    assert got == [
        {"id": "box", "url": "https://api-box.example.com"},
        {"id": "plain", "url": "https://plain.example.org"},
    ]


@pytest.mark.parametrize("raw,why", [
    ("", "空字串 = 未設定，必須是空清單"),
    ("   ", "只有空白"),
    ("msi", "純 id 沒有網址，不該產生假 peer"),
    ("msi=xxx", "右邊不是網址"),
    ("notaurl", "壞值"),
])
def test_worker_rejects_junk_without_inventing_peers(
        raw: str, why: str, tmp_path: Path) -> None:
    """壞值必須被丟棄，而不是變成一台不存在的 peer。

    丟掉比猜好：猜錯的 peer 會在 UI 的「主機狀態」多一列假的「連線失敗」，
    使用者會去查一個他根本沒有的機器。
    """
    assert _run_parse_origins(raw, tmp_path) == [], why


def test_worker_keeps_good_entries_when_one_is_bad(tmp_path: Path) -> None:
    """一段壞的不該拖垮整串（一個逗號打錯就全機失聯太脆弱）。"""
    got = _run_parse_origins(
        "notaurl,box=https://api-box.example.com,msi", tmp_path)
    assert got == [{"id": "box", "url": "https://api-box.example.com"}]


# ──────────────────────────────────────────────────────────────────────
# 下面這組是 2026-09-28 補的：把 BACKENDS 從 `const` 改成 `$:` 造成的回歸。
# 症狀是 https://ragdemo.win/ 整頁空白，而錯誤訊息（"Cannot read properties
# of undefined (reading 'find')"）被燒進 SSR 的 HTML，所以只看線上看起來像
# 「頁面壞掉」而不是「某個變數沒初始化」。
# ──────────────────────────────────────────────────────────────────────

def _onmount_span(code: str) -> tuple[int, int]:
    """回傳 onMount(...) 回呼在 script 裡的字元範圍（用大括號配對）。"""
    m = re.search(r"\bonMount\s*\(", code)
    assert m, "找不到 onMount —— 這個測試假設它還在，若被改名請同步維護"
    i = code.index("{", m.end())
    depth, j = 0, i
    while j < len(code):
        if code[j] == "{":
            depth += 1
        elif code[j] == "}":
            depth -= 1
            if depth == 0:
                return m.start(), j
        j += 1
    raise AssertionError("onMount 的大括號沒配對到")


def _top_level_call_sites(code: str, name: str) -> list[int]:
    """回傳 `name(...)` 出現在「script 頂層」（大括號深度 0）的位置。

    不能用「整行剛好是 `fn();`」來判 —— 那會把函式**內部**的合法呼叫
    （例如 `switchBackend()` 裡的 `loadModels()`）也算出來，而那些是對的：
    它們由使用者操作觸發、確定在瀏覽器裡。真正要擋的是沒有任何函式包住、
    會在組件初始化時就執行的呼叫。

    掃描時跳過註解、字串、樣板字串（含 `${}` 巢狀），因為那些裡面的大括號
    不代表區塊深度。
    """
    i, n, depth, sites = 0, len(code), 0, []
    while i < n:
        ch = code[i]
        if ch == "/" and code[i + 1:i + 2] == "/":
            j = code.find("\n", i)
            i = n if j == -1 else j
            continue
        if ch == "/" and code[i + 1:i + 2] == "*":
            j = code.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if ch in "\"'":
            q, i = ch, i + 1
            while i < n and code[i] != q:
                i += 2 if code[i] == "\\" else 1
            i += 1
            continue
        if ch == "`":
            i, tdepth = i + 1, 0
            while i < n:
                if code[i] == "\\":
                    i += 2
                    continue
                if code[i] == "$" and code[i + 1:i + 2] == "{":
                    tdepth, i = tdepth + 1, i + 2
                    continue
                if code[i] == "}" and tdepth:
                    tdepth, i = tdepth - 1, i + 1
                    continue
                if code[i] == "`" and tdepth == 0:
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth -= 1
            i += 1
            continue
        if depth == 0 and code.startswith(name + "(", i):
            prev = code[i - 1] if i else ""
            if not (prev.isalnum() or prev in "_$.'"):
                j, lvl = i + len(name), 0
                while j < n:
                    if code[j] == "(":
                        lvl += 1
                    elif code[j] == ")":
                        lvl -= 1
                        if lvl == 0:
                            break
                    j += 1
                if code[j + 1:j + 2] in ("", ";", "\n"):
                    sites.append(i)
                i = j + 1
                continue
        i += 1
    return sites


def test_page_backends_has_eager_initializer_not_only_reactive() -> None:
    """`BACKENDS` 必須有初值，不能只靠 `$:`。

    Svelte 的反應式賦值是在 `instance()` 主體跑完**之後**才求值的。任何在
    script 頂層就去讀它的東西（`base()`、樣板裡的 `{#each}`）都會拿到
    undefined —— 而那會讓整個 component 的掛載中斷，症狀是空白頁。
    """
    code = _code_only(PAGE)
    assert re.search(r"^\s*let\s+BACKENDS\s*=", code, re.M), \
        "BACKENDS 需要一個 non-reactive 的初值（`let BACKENDS = ...`）；" \
        "只有 `$: BACKENDS = ...` 會讓頂層讀取拿到 undefined"
    assert re.search(r"^\s*\$\:\s*BACKENDS\s*=", code, re.M), \
        "BACKENDS 仍需保留反應式賦值，knownHosts 進來後要能更新"


def test_page_startup_fetches_live_in_onmount_not_at_script_top_level() -> None:
    """`checkHealth` / `loadStatus` / `loadModels` 不得在 script 頂層呼叫。

    它們打的是相對網址（`/api/health`）。SvelteKit 在 SSR 階段會直接丟出
    "Cannot call `fetch` eagerly during server-side rendering"，於是 health
    徽章變紅、`/status` 與 `/models` 都不載入 —— 整頁看起來是空的。
    這兩件事都只能在瀏覽器做，所以必須在 `onMount`（或某個函式）裡。

    在函式內（例如 `switchBackend()`）呼叫是對的，那條不在此限。
    """
    code = _code_only(PAGE)
    for fn in ("checkHealth", "loadStatus", "loadModels"):
        bad = _top_level_call_sites(code, fn)
        assert not bad, (
            f"{fn}() 在 script 頂層被呼叫（字元 {bad}）——沒有任何函式包住，"
            "會在組件初始化時執行。SSR 階段 SvelteKit 禁止相對網址的 fetch，"
            "會讓整頁載不到資料。請移進 onMount。"
        )


def test_page_startup_fetches_are_actually_awaited_in_onmount() -> None:
    """光「不在頂層」不夠 —— 必須真的在 onMount 裡被呼叫到。

    有人可能把三個呼叫整段刪掉，那上一條測試會綠，但頁面就再也沒有資料了。
    這條守住它們仍然接得上。
    """
    code = _code_only(PAGE)
    lo, hi = _onmount_span(code)
    inside = code[lo:hi]
    for fn in ("checkHealth", "loadStatus", "loadModels"):
        assert re.search(rf"\b{fn}\(\)", inside), \
            f"onMount 裡應該要呼叫 {fn}() —— 沒有它的話頁面不會載入資料"


def test_page_restore_backend_only_runs_once_known_hosts_arrived() -> None:
    """還原上次選的後端必須在 knownHosts 填好之後，且只做一次。

    knownHosts 還是空時 `BACKENDS` 只有「自動」，比對永遠不成立 —— 那時還原
    等於沒做。這是 `restoreTried` 存在的原因，順便守住「只跑一次」，
    否則使用者手動切到別台後，下次 loadStatus 會把他無聲無息切回去。
    """
    code = _code_only(PAGE)
    assert "restoreTried" in code, "restoreBackend 應有「只試一次」的旗標"
    m = re.search(r"knownHosts\s*=\s*[^;]+;\s*\n\s*restoreBackend\(\)", code)
    assert m, "restoreBackend() 應緊接在 knownHosts 賦值之後（loadStatus 裡）"


# 這裡原本還有一條「用 svelte/compiler 編譯後比對 let BACKENDS = 的位置是否
# 在頂層呼叫之前」的測試。**拿掉了**，因為它測的是錯的東西：
# 把三個呼叫搬進 onMount 之後，求值順序就不再是問題（編譯後已無任何頂層呼叫），
# 那條測試對一個已經不存在的風險做 regex 比對，而且會隨 Svelte 版本的輸出格式
# 碎掉。真正守住這個不變式的是上面那三條：初值、不得在頂層、onMount 裡確實有呼叫。
# 三條都實測過會紅（見 commit 訊息）。

