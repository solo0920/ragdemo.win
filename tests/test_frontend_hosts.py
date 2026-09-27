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
