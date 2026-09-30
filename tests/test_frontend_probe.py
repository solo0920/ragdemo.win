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

WORKER = Path(__file__).resolve().parents[1] / "frontend/src/routes/api/[...path]/+server.ts"

# 見上方 docstring：哨兵網址，保證 stub 失效時不會打到 production。
SENTINEL = "https://sentinel.invalid/api"


def _decl_body(code: str, marker: str) -> str:
    """取出一個宣告（含主體）。

    與 ``test_frontend_hosts._decl_body`` 的差異：**回傳型別裡的大括號**。

    那個版本在參數列配對完之後直接找第一個 ``{`` 當主體。它對
    ``relay(r, origin): Promise<Response>`` 這類沒問題，但 ``probe`` 的回傳
    型別是 ``Promise<{ ok: boolean; seen: string }>`` —— 第一個 ``{`` 在
    ``Promise<`` 後面，會抽出一個**截斷的宣告**（少了主體），丟給 node 就是
    ``Expected ',', got 'const'``。

    所以這裡在參數列之後多走一步：若下一個非空白字元是 ``:``，先跨過回傳
    型別（含 ``<``/``>`` 配對）再找主體的大括號。
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
    if k < len(code) and code[k] == ":":
        # 跨過回傳型別。<> 配對，並容忍 { } 巢狀（Promise<{...}>）。
        gdepth = 0
        while k < len(code):
            ch = code[k]
            if ch in "<{":
                gdepth += 1
            elif ch in ">}":
                gdepth -= 1
                if gdepth == 0:
                    break
            k += 1
        assert k < len(code), f"{marker} 的回傳型別沒配對到"
        k += 1
        while k < len(code) and code[k] in " \t":
            k += 1

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
    probe = _decl_body(code, "async function probe")

    script = tmp_path / "probe.ts"
    script.write_text(
        probe + "\n"
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
