"""Cloudflare Access Service Token 有沒有真的送到**三個** fetch 站點。

## 為什麼要單獨一個檔案

`+server.ts` 有三個對外的 `fetch`：

| 站點 | 函式 | 誰在用 |
|---|---|---|
| `through()` | 一般路徑轉發 | `GET` 與非 query 的 `POST` |
| `probe()` | 選路前的 `/health` | `queryRoute()` |
| `queryRoute()` | `POST /query` | `POST`（`'query'` 分支） |

**它們彼此不通。** `+server.ts` 的 `POST` handler 對 `'query'` 直接改走
`queryRoute()`，不經過 `through()`。所以只在一處加 token 的後果是：
登入正常、規則頁正常、`/status` 正常，**只有查詢壞掉**。

這不是假想 —— 它就是這個改動一開始漏掉的那一處。所以「三個站點各自都帶了
header」必須是三條獨立斷言，不能只測一個。

## 為什麼斷言「header 值」而不是「怎麼合併」

驗收標準是**行為**：某個 fetch 出去的請求上，該有的標頭在、且別人的標頭沒
被蓋掉。用 spread 或用 `Object.assign` 都合算對；反過來，用整個取代就錯。
所以測試只看結果，不看實作手法。
"""
import json
import shutil
import subprocess

import pytest
from conftest import WORKER, balanced, decl_body

SENTINEL = "https://sentinel.invalid/api"
ORIGINS = "https://api-x570.ragdemo.win,https://api-mbp.ragdemo.win"

CF_ID = "id.test-access-client-id"
CF_SECRET = "secret.test-client-secret"
ENV_OK = {"CF_ACCESS_CLIENT_ID": CF_ID, "CF_ACCESS_CLIENT_SECRET": CF_SECRET}

# queryRoute 的依賴鏈比 probe/through 深得多（要 hostsOf → parseOrigins、
# relay → DROP_ON_PROXY、json/jsonError），整組抽進來跑。
# ⚠️ marker 一律帶上 `(`。否則 `"function json"` 會是 `"function jsonError"` 的
# 前綴、匹配到錯的位置，把 jsonError 抽兩次 —— 症狀是
# `SyntaxError: Identifier 'jsonError' has already been declared`。
_DECLS = [
    ("function parseOrigins(", "decl"),
    ("function hostsOf(", "decl"),
    ("function dead(", "decl"),
    ("const DROP_ON_PROXY", "balanced"),
    ("async function relay(", "decl"),
    ("function jsonError(", "decl"),
    ("function json(", "decl"),
    ("function cfHeaders(", "decl"),
    ("function cfUnconfigured(", "decl"),
    ("async function through(", "decl"),
    ("async function probe(", "decl"),
    ("async function queryRoute(", "decl"),
]


def _run(entry: str, spec: dict, tmp_path) -> dict:
    """把某個函式真的跑一次。回傳 {status, body, calls}。

    spec:
      env        dict  — $env/dynamic/private 的內容
      authHeader str   — 給 through() 的 authorization（/rules 寫入用）
      upstream   int   — 假上游的回應碼
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH")
    code = WORKER.read_text(encoding="utf-8")
    parts = [f"const env = {json.dumps(spec.get('env', ENV_OK))};", "export {};"]
    for marker, how in _DECLS:
        parts.append(decl_body(code, marker) if how == "decl" else balanced(code, code.index(marker)))

    calls: list = []
    harness = """
const spec = JSON.parse(process.argv[2]);
const calls = [];
globalThis.fetch = async (url, init) => {
  // new Headers() 會把名稱正規化成小寫，而 HTTP header 大小寫不敏感。
  // 這裡刻意用小寫鍵：斷言的是「伺服器收到這個標頭」，不是「程式碼寫了
  // 這個大小寫」—— 後者是綁機制。
  const h = {};
  if (init?.headers) for (const [k, v] of new Headers(init.headers)) h[k.toLowerCase()] = v;
  calls.push({ url: String(url), method: init?.method ?? 'GET', headers: h });
  // deadHosts 讓某一台回 502（Cloudflare tunnel 離線的典型代碼），用來驅動
  // through() 的 failover 分支。
  const host = new URL(String(url)).host;
  const status = (spec.deadHosts ?? []).includes(host) ? 502 : (spec.upstream ?? 200);
  return new Response(JSON.stringify({ ok: true, answer: 'x', hits: [], law_version: {} }),
                      { status, headers: { 'content-type': 'application/json' } });
};
const out = await ENTRY(ARGV);
// probe() 回傳普通物件 {ok, seen}，through()/queryRoute() 回傳 Response。
// 兩種都要能斷言，所以統一序列化成 {status, body}。
const isResp = typeof out?.text === 'function';
const body = isResp ? await out.text() : JSON.stringify(out);
console.log(JSON.stringify({ status: isResp ? out.status : 200, body, calls }));
"""
    script = tmp_path / f"{entry}.ts"
    script.write_text(
        "\n".join(parts) + "\n" + harness.replace("ENTRY", entry).replace("ARGV", spec["argv"]),
        encoding="utf-8",
    )
    r = subprocess.run(
        [node, "--experimental-strip-types", str(script), json.dumps(spec)],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        pytest.fail(f"node 執行失敗（{entry}）：{r.stderr[:600]}")
    out = json.loads(r.stdout.strip().splitlines()[-1])
    try:
        out["json"] = json.loads(out["body"])
    except Exception:
        out["json"] = None
    return out


# ── 條件 1：三個站點各自都帶了 header（三條獨立斷言）────────────────────

def test_through_sends_the_token(tmp_path):
    got = _run("through", {"argv": "'GET','status',undefined,{env:{API_ORIGINS:'" + ORIGINS + "'}}"}, tmp_path)

    assert got["calls"], "through() 應該有發出請求"
    for c in got["calls"]:
        assert c["headers"].get("cf-access-client-id") == CF_ID, c
        assert c["headers"].get("cf-access-client-secret") == CF_SECRET, c


def test_probe_sends_the_token(tmp_path):
    got = _run("probe", {"argv": f"'{SENTINEL}'"}, tmp_path)

    assert len(got["calls"]) == 1, got["calls"]
    assert got["calls"][0]["headers"].get("cf-access-client-id") == CF_ID
    assert got["calls"][0]["headers"].get("cf-access-client-secret") == CF_SECRET


def test_query_route_sends_the_token(tmp_path):
    argv = f"new Request('{SENTINEL}/query'), {{env:{{API_ORIGINS:'{ORIGINS}'}}}}"
    got = _run("queryRoute", {"argv": argv}, tmp_path)

    query_calls = [c for c in got["calls"] if c["url"].endswith("/query")]
    assert query_calls, f"queryRoute 應該有對 /query 發出請求：{got['calls']}"
    for c in query_calls:
        assert c["headers"].get("cf-access-client-id") == CF_ID, c
        assert c["headers"].get("cf-access-client-secret") == CF_SECRET, c


def test_token_goes_to_every_probed_origin_not_just_the_first(tmp_path):
    """token 會送給**每一台被探測到**的主機，不是只有第一台。

    只護住一台時 failover 會把請求送到未護住的那台，驗證等於不存在。這是
    設定面的一致性要求（Step 3 三台同時開），程式端能保證的只有「不偷工
    減料只送第一台」—— 這條就是那個保險。

    用 `queryRoute` 而不是 `through()`：`through()` 第一台健康就 `return`，
    根本不會碰第二台（那是正確的 failover 行為，不是缺陷）。而 `queryRoute`
    對**所有**主機並行 probe，所以每台都一定會被發到。
    """
    argv = f"new Request('{SENTINEL}/query'), {{env:{{API_ORIGINS:'{ORIGINS}'}}}}"
    got = _run("queryRoute", {"argv": argv}, tmp_path)

    health = [c for c in got["calls"] if c["url"].endswith("/health")]
    hosts = {c["url"].split("/")[2] for c in health}
    assert hosts == {"api-x570.ragdemo.win", "api-mbp.ragdemo.win"}, hosts
    for c in health:
        assert c["headers"].get("cf-access-client-id") == CF_ID, c
        assert c["headers"].get("cf-access-client-secret") == CF_SECRET, c


def test_token_follows_failover_to_the_next_origin(tmp_path):
    """第一台 dead 時，failover 到的第二台**也要**帶 token。

    這一條是「只護住一台就等於沒護」的程式端對應症狀：第一台 502 被當離線、
    請求轉到第二台 —— 如果第二台沒開 Access，請求就在那裡被擋，而 log 會顯示
    第一台離線、第二台… 也是離線，看起來像兩台都掛了。
    """
    argv = f"'GET','status',undefined,{{env:{{API_ORIGINS:'{ORIGINS}'}}}}"
    got = _run("through", {"argv": argv, "deadHosts": ["api-x570.ragdemo.win"]}, tmp_path)

    hosts = [c["url"].split("/")[2] for c in got["calls"]]
    assert hosts == ["api-x570.ragdemo.win", "api-mbp.ragdemo.win"], hosts
    for c in got["calls"]:
        assert c["headers"].get("cf-access-client-id") == CF_ID, c


# ── 條件 2：authorization 沒被蓋掉（through 的 merge 順序）──────────────

def test_authorization_survives_the_cf_header_merge(tmp_path):
    """CF header 必須 merge 進既有 headers，不能整個取代。

    through() 會先放 `authorization`（/rules 寫入用的管理 token）再加 CF 的
    header。若 CF 的寫法是取代而不是 merge，這把 token 會被蓋掉，
    /rules 寫入變 401 —— 而且症狀是「只有寫入壞掉」，不會有人想到是
    Access 的改動造成的。
    """
    hdrs = f"new Headers({{authorization:'Bearer admin-token.test'}})"
    argv = f"'POST','rules','{{}}',{{env:{{API_ORIGINS:'{ORIGINS}'}}}},{hdrs}"
    got = _run("through", {"argv": argv}, tmp_path)

    for c in got["calls"]:
        assert c["headers"].get("authorization") == "Bearer admin-token.test", c
        assert c["headers"].get("cf-access-client-id") == CF_ID, c
        assert c["headers"].get("content-type") == "application/json", c


# ── 條件 3：缺 secret 時說人話（而不是「所有後端皆無法連線」）───────────

@pytest.mark.parametrize("missing", ["CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET"])
def test_missing_token_secret_returns_503_naming_the_var(missing, tmp_path):
    """缺 token → 503 且 detail 點名缺哪個變數。

    沒有這道檢查的話症狀是：三台全「連線失敗」、detail 說
    「所有後端皆無法連線（x570: 200 但非 JSON…）」—— 而三台後端其實都活著。
    那是同一種「系統主動說謊」，而且要花兩小時才查得到是 Pages 變數沒設。
    """
    env = {k: v for k, v in ENV_OK.items() if k != missing}
    argv = f"'GET','status',undefined,{{env:{{API_ORIGINS:'{ORIGINS}'}}}}"
    got = _run("through", {"env": env, "argv": argv}, tmp_path)

    assert got["status"] == 503, got["body"]
    assert missing in got["json"]["detail"], got["json"]
    assert not got["calls"], f"缺 token 時不該發出請求（否則就是白打三台）：{got['calls']}"


@pytest.mark.parametrize("missing", ["CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET"])
def test_query_route_missing_token_is_503_not_a_host_failure(missing, tmp_path):
    """queryRoute 必須自己檢查一次 —— 它**不經過** through()。

    只在 through() 檢查的話，/query 會變成「所有後端皆無法連線」而不是
    「Pages 變數沒設」。這是這個改動最容易被漏掉的一處。
    """
    env = {k: v for k, v in ENV_OK.items() if k != missing}
    argv = f"new Request('{SENTINEL}/query'), {{env:{{API_ORIGINS:'{ORIGINS}'}}}}"
    got = _run("queryRoute", {"env": env, "argv": argv}, tmp_path)

    assert got["status"] == 503, got["body"]
    assert missing in got["json"]["detail"], got["json"]
    assert "無法連線" not in got["json"]["detail"], "不該說成後端連線問題"
    assert not got["calls"], got["calls"]


def test_probe_reports_missing_token_without_faking_a_diagnosis(tmp_path):
    """probe() 缺 token 時不該發請求，更不該回「200 但非 JSON」冒充診斷。"""
    argv = f"'{SENTINEL}'"
    got = _run("probe", {"env": {}, "argv": argv}, tmp_path)

    assert not got["calls"], "缺 token 時不該對外發請求"
    assert "Service Token" in got["json"]["seen"], got["json"]
