"""前端 ``probe()`` 的真實實作 —— 「這台到不了」時的分類。

為什麼值得單獨一個 commit：``tests/test_frontend_hosts.py`` 之前只測
``parseOrigins`` 與 ``relay()``，``probe()`` **從來沒有被測過**。而它是
``/api/query`` 選路的唯一依據 —— 判錯就會選一台送過去一定被擋的主機。

## 這裡重複了後端 ``gateway._host_probe_log`` 的同一個教訓

兩邊的錯誤一模一樣：只看「回應碼是不是成功」，於是 Cloudflare Access
把 302 導到登入頁、runtime 跟隨後拿到 **200 + text/html** 時，都會回報
「連線成功」，而真正的查詢全被擋。修法也一樣：加 content-type 條件。

## 為什麼測試斷言 ``{ok, seen}`` 而不斷言 ``r.ok``

只斷言行為（分類），不綁機制。若有人改成別的方式修 —— 例如在呼叫端先
``HEAD`` 一次、或改用 ``redirect: "manual"`` —— 只要分類結果一樣，測試照樣綠。

## 為什麼用 ``.invalid`` 哨兵網址

harness 用 node 跑真的 ``probe()``，只把 ``globalThis.fetch`` 換掉。萬一
那個 stub 沒生效（例如實作改成繞過 ``globalThis.fetch``），舊的寫法會
**真的去打 ``api-msi.ragdemo.win``** —— 一次遙測、而且結果不可重現。

``.invalid`` 是 RFC 2606 保留 TLD、永遠不會解析。所以即使 stub 失效，
請求也只是立刻 DNS 失敗，**不可能打到 production**。這是「失敗時最無害」
的設計，不是僥倖。
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import WORKER, decl_body

# 見上方 docstring：哨兵網址，保證 stub 失效時不會打到 production。
SENTINEL = "https://sentinel.invalid/api"


def _run_probe(spec: dict, tmp_path: Path) -> dict:
    """把 probe() 真的跑一次。回傳 {ok, seen, requested}。

    spec:
      throw   bool  — fetch 丟例外（模擬網路層死／timeout）
      status  int   — 回應碼
      headers dict  — 回應標頭
      body    str   — 回應內容
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH")
    code = WORKER.read_text(encoding="utf-8")
    # probe() 依賴 cfUnconfigured()（缺 Service Token 時直接回、不發請求），
    # 所以那兩個宣告要一起抽出來，並給 `env` 一個 shim —— 實作是從
    # $env/dynamic/private 拿的，node 裡沒有那個模組解析。
    parts = [
        f"const env = {json.dumps(spec.get('env', {'CF_ACCESS_CLIENT_ID': 'id.test', 'CF_ACCESS_CLIENT_SECRET': 'secret.test'}))};",
        decl_body(code, "function cfHeaders"),
        decl_body(code, "function cfUnconfigured"),
        decl_body(code, "async function probe"),
    ]
    script = tmp_path / "probe.ts"
    script.write_text(
        "\n".join(parts) + "\n"
        "const spec = JSON.parse(process.argv[2]);\n"
        "let requested = null;\n"
        "globalThis.fetch = async (url, init) => {\n"
        "  requested = String(url);\n"
        "  if (spec.throw) throw new TypeError('fetch failed');\n"
        "  if (spec.stripContentType) {\n"
        "    // ⚠️ 不能用 `new Response('{}', {headers:{}})` 製造「沒有 content-type」——\n"
        "    // Fetch 規格會自動補 text/plain;charset=UTF-8。必須用非字串 body，\n"
        "    // 才會真的沒有 content-type 標頭（實測踩過）。\n"
        "    return new Response(new ArrayBuffer(2), { status: spec.status, headers: {} });\n"
        "  }\n"
        "  return new Response(spec.body ?? '', {\n"
        "    status: spec.status,\n"
        "    headers: spec.headers ?? {},\n"
        "  });\n"
        "};\n"
        "const out = await probe(process.argv[3]);\n"
        "console.log(JSON.stringify({ ...out, requested }));\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [node, "--experimental-strip-types", str(script), json.dumps(spec), SENTINEL],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        pytest.fail(f"node 執行失敗：{r.stderr[:500]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


# ── 四個案例：成功的只有一種 ──────────────────────────────────────────────

def test_healthy_json_is_the_only_success(tmp_path):
    """200 + application/json → 連線成功。"""
    got = _run_probe(
        {"status": 200, "headers": {"content-type": "application/json"}, "body": '{"ok":true}'},
        tmp_path)

    assert got["ok"] is True
    assert got["seen"] == "200"
    assert got["requested"] == f"{SENTINEL}/health", "probe 應打 /health"


def test_access_403_json_is_a_failure(tmp_path):
    """Access 對非 HTML 請求回 403（可能帶 JSON）→ 判失敗。

    r.ok 已經擋得住這一個，所以這條是防止**未來**有人放寬到只看 content-type。
    """
    got = _run_probe(
        {"status": 403, "headers": {"content-type": "application/json"},
         "body": '{"error":"denied"}'},
        tmp_path)

    assert got["ok"] is False
    assert got["seen"] == "403", "403 必須原樣出現在 seen 裡，否則診斷訊息沒用"


def test_login_page_200_html_is_a_failure(tmp_path):
    """Access 302 被跟隨到登入頁 → 200 + text/html → **必須判失敗**。

    這是舊 `return r.ok` 會說謊的那一種：回應碼真的是 200，只是內容不是
    後端的 /health。`r.ok === true`，所以舊邏輯會回報「連線成功」，
    接著 queryRoute 就會選中這台，然後真正的 /query 被擋。
    """
    got = _run_probe(
        {"status": 200,
         "headers": {"content-type": "text/html; charset=utf-8"},
         "body": "<html>login</html>"},
        tmp_path)

    assert got["ok"] is False, "200 + text/html 是登入頁，不是後端"
    assert "text/html" in got["seen"], f"seen 要指出是 content-type 的問題：{got['seen']!r}"


def test_no_content_type_is_a_failure(tmp_path):
    """連 content-type 都沒有 → 判失敗。代理剝掉標頭時會這樣。"""
    got = _run_probe({"status": 200, "stripContentType": True}, tmp_path)

    assert got["ok"] is False
    assert "無 content-type" in got["seen"], got["seen"]


def test_network_error_is_a_failure(tmp_path):
    """fetch 丟例外（連線被拒／timeout）→ 判失敗，且 seen 收成 network error。"""
    got = _run_probe({"throw": True}, tmp_path)

    assert got["ok"] is False
    assert got["seen"] == "network error"
