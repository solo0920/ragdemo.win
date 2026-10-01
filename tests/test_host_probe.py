"""gateway._host_probe_log / _host_law_versions 的**真實實作**。

這兩個函式在 Cloudflare Access 上線前沒有任何測試執行過：test_rag_engine.py
的三處（:420/:483/:504）都是 `monkeypatch.setattr` 把它**替換掉**。也就是說
「面板顯示的 peer 連線狀態」從來沒有被驗證過 —— 而它正是 Access rollout
期間唯一能讓人判斷「三台是不是都護住了」的工具。

## 為什麼測試只斷言「結果」不斷言「機制」

`follow_redirects=True` 刻意留在生產程式碼裡，且**不在測試裡被斷言**。
302→200 HTML 那條案例靠 MockTransport 依被請求的 URL 回不同狀態碼，
讓 httpx **自己**走完 redirect 鏈 —— 測試因此綁定的是行為，不是實作細節。
若有人改成 `follow_redirects=False` ＋正確判定式（302 直接撞 status != 200
→ 判失敗），結果等價，測試照樣綠。這是刻意的：不要讓「用哪種方式修」
變成測試必須跟著改的東西。
"""
import httpx
import pytest

from app import gateway

SELF = "wsl"
THREE = {
    "x570": "https://api-x570.ragdemo.win",
    "mbp": "https://api-mbp.ragdemo.win",
    "wsl": "https://api-wsl.ragdemo.win",
}
X570 = "api-x570.ragdemo.win"
MBP = "api-mbp.ragdemo.win"
LOGIN = "https://access.example.com/login"

OK = "連線成功"
NG = "連線失敗"

# 在 patch 之前抓住真的 AsyncClient：patch 掉之後 gateway.httpx.AsyncClient
# 就是下面的 `make` 自己，make 裡再呼叫它會無限遞迴（RecursionError 會被
# gateway 的 `except Exception` 吞掉，看起來像「所有 peer 離線」）。
_REAL_CLIENT = httpx.AsyncClient


def _deny(request: httpx.Request) -> httpx.Response:
    """預設 handler：拒絕一切。確保漏掉 install() 的測試不會打到真實網路。"""
    return httpx.Response(500, json={"detail": f"未 mock 的請求: {request.url}"})


def _access_login_page(request: httpx.Request) -> httpx.Response:
    """Access 擋下的真實鏈：/health → 302 → 登入頁 200 + text/html。

    ⚠️ redirect 目標也必須由這裡回答。若在中間攔截（舊版測試犯過的錯），
    登入頁會變成 500，舊的 `status_code < 500` 就會正確地判失敗 ——
    測試看起來是綠的，實際上完全沒測到 302 那條路徑。
    """
    if request.url.path == "/health":
        return httpx.Response(302, headers={"location": LOGIN})
    return httpx.Response(200, text="<html>login</html>",
                          headers={"content-type": "text/html; charset=utf-8"})


def _only(host: str, response: httpx.Response) -> callable:
    """只讓 `host` 回 `response`，其他主機回 500（用來標記「沒被 mock」）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == host:
            return httpx.Response(response.status_code,
                                 headers=response.headers,
                                 content=response.content)
        return httpx.Response(500, json={"detail": "not mocked"})

    return handler


@pytest.fixture
def seen(monkeypatch):
    """攔下 gateway 發出的所有 HTTP 請求。

    回傳 (install, hits)。預設先裝一個拒絕一切的 handler，測試必須呼叫
    install() 換成自己的 —— 忘記換最多是拿到 500，不會打到真實網路。
    """
    hits: list[str] = []
    state = {"handler": _deny}

    async def wrapped(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return state["handler"](request)

    def make(*a, **kw):
        kw["transport"] = httpx.MockTransport(wrapped)
        return _REAL_CLIENT(*a, **kw)

    monkeypatch.setattr(gateway.httpx, "AsyncClient", make)

    def install(handler):
        state["handler"] = handler

    return install, hits


@pytest.fixture
def three_hosts(monkeypatch):
    """把 gateway 當成 MSI，HOST_API 含三台（含自己）。"""
    monkeypatch.setattr(gateway, "HOST_API", THREE)
    monkeypatch.setattr(gateway, "HOST_ID", SELF)


# ── _host_probe_log 的判定式 ────────────────────────────────────────────
# 涵蓋三種「主機沒掛、但到不了」的真實失敗模式。舊的 `status_code < 500`
# 會把下面每一種都回報成 OK。

@pytest.mark.asyncio
async def test_login_page_redirect_is_a_failure(seen, three_hosts):
    """Access 的 302 被跟隨到登入頁 → 拿到 200 + text/html → 必須判失敗。

    這是舊判定式最隱蔽的一種：回應碼真的是 200，只是內容不是 API 的。
    """
    install, hits = seen
    install(_access_login_page)

    out = await gateway._host_probe_log()

    assert out["x570"] == NG
    # 確認案例真的走了一趟 redirect，而不是被擋掉後直接判失敗（那樣會空轉）
    assert any(LOGIN in u for u in hits), hits
    assert any(u.endswith("/health") for u in hits), hits


@pytest.mark.asyncio
async def test_access_403_json_is_a_failure(seen, three_hosts):
    """Access 對非 HTML 請求回 403（且可能帶 JSON）→ 必須判失敗。

    釘住「content-type 檢查單獨不夠」：403 + application/json 過得了
    content-type 那一關，只有 status == 200 擋得住。若有人把判定式改回
    只看 content-type，這個測試會紅。
    """
    install, _ = seen
    install(lambda r: httpx.Response(
        403, json={"error": "access denied"},
        headers={"content-type": "application/json"}))

    out = await gateway._host_probe_log()

    assert out["x570"] == NG


@pytest.mark.asyncio
async def test_healthy_json_is_a_success(seen, three_hosts):
    install, _ = seen
    install(_only(X570, httpx.Response(200, json={"ok": True})))

    out = await gateway._host_probe_log()

    assert out["x570"] == OK
    assert out["mbp"] == NG, "沒被 mock 的主機應判失敗，不是靜默當成功"


@pytest.mark.asyncio
async def test_self_is_never_probed(seen, three_hosts):
    """自己那列不該發任何 HTTP 請求 —— 值本來就是硬寫的。"""
    install, hits = seen
    install(_only(X570, httpx.Response(200, json={"ok": True})))

    await gateway._host_probe_log()

    assert hits, "應該有對 peer 的請求，否則這個測試沒測到東西"
    assert not any("api-wsl" in u for u in hits), hits


@pytest.mark.asyncio
async def test_self_entry_is_hardcoded_success(seen, three_hosts):
    """釘住一個**已知且刻意**的行為：自己恆為「連線成功」，從未被探測。

    ⚠️ 這是繼承的限制，不是這一步要修的東西。gateway.py:207 的
    `out[HOST_ID] = "連線成功"` 是硬寫的（docstring 有記錄理由：「有回應
    本身就是活著的證據」）。後果是：**tunnel 整條斷掉時，面板會顯示
    「我好好的、peer 全掛」**，而實際上是共用的公網路徑全斷 —— 讀法會誤導。

    真要修得把「本機可達」與「公網可達」拆成兩個維度，那是功能不是修 bug。
    留這顆測試是為了讓下一個人看到這個行為時，知道它是**被決定過的**。
    """
    install, _ = seen
    install(_only(X570, httpx.Response(200, json={"ok": True})))

    out = await gateway._host_probe_log()

    assert out[SELF] == OK
    assert set(out) == set(THREE), "三台都要在（單機部署時面板不能整個消失）"


# ── _host_law_versions：跳過自己 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_law_versions_skips_self(seen, three_hosts, monkeypatch):
    """自己那台的版數直接讀磁碟，不該繞一圈公網去問自己。

    純粹的浪費消除：原本 `for u in HOST_API.values()` 會對含自己的
    HOST_API 全部發請求，而自己的結果在下一行就被丟掉換成磁碟值。
    """
    install, hits = seen
    install(_only(X570, httpx.Response(
        200, json={"law_version": {"update_date": "2026-09-01"}},)))
    monkeypatch.setattr(gateway, "_LAW_VERSION_FN", lambda: {"update_date": "2026-09-11"},
                        raising=False)

    out = await gateway._host_law_versions()

    assert not any("api-wsl" in u for u in hits), hits
    assert out[SELF] == "2026-09-11", "自己的版數必須來自磁碟（注入點），不是 HTTP"
    assert out["x570"] == "2026-09-01", "peer 的版數仍要真的去探"


@pytest.mark.asyncio
async def test_law_versions_asks_peers_with_probe_zero(seen, three_hosts, monkeypatch):
    """probe=0 是防止 A→B→C→A 遞迴的既有不變條件，別在改 self-skip 時弄掉。"""
    install, hits = seen
    install(_only(X570, httpx.Response(
        200, json={"law_version": {"update_date": "2026-09-01"}},)))
    monkeypatch.setattr(gateway, "_LAW_VERSION_FN", lambda: {"update_date": "2026-09-11"},
                        raising=False)

    await gateway._host_law_versions()

    assert hits, "應該有請求"
    assert all("probe=0" in u for u in hits), hits
