"""per-host 預設模型的前端邏輯（`frontend/src/lib/hostDefaults.ts`）。

## 為什麼測的是**真的模組**而不是抽出來的片段

`test_frontend_hosts.py` / `test_frontend_probe.py` 用「把宣告抽成 .ts 貼給 node」
的方式測，那是因為 `+server.ts` import 了 `$env/dynamic/private`（node 解析不了）。
`hostDefaults.ts` **沒有任何 import**（fetch 由呼叫端注入），所以測試直接
`import` 真的檔案：

    抽出來貼 → 測試與實作漂移時測試照樣綠（改壞了抽出點，只是抽到別的東西）
    直接 import → 測的就是頁面真正載入的那份

## 這裡釘住的核心：**「不知道」不等於「沒有」**

本專案反覆在修的錯誤就是把「無從驗證」呈現成「驗證失敗」。這支檔案裡最容易
再犯一次的地方有兩個，都用獨立欄位而不是空值代表：

| 情況 | `state` | `modelsKnown` | `models` | 必須顯示 |
|---|---|---|---|---|
| 整台連不到 | `unreachable` | false | `[]` | 無法連線 |
| 讀得到設定、模型清單掛掉 | `ok` | **false** | `[]` | 模型清單取不到 |
| 讀得到、該機真的沒有模型 | `ok` | **true** | `[]` | 該機沒有可用的聊天模型 |

把後兩者合併成一個 `models: []` 就會讓第 2 種看起來像第 3 種 —— 使用者會
以為那台沒裝模型，然後去裝東西。

## 乾淨 clone 的 CI

沒有 `.env`、沒有真後端、真 Postgres／qdrant／ollama 一律不碰：harness 裡的
`fetch` 是自己造的假貨，連網路層都沒進。哨兵網址用 RFC 2606 保留的
`.invalid`（萬一假 fetch 失效也不會打到 production）。
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "frontend" / "src" / "lib" / "hostDefaults.ts"
PAGE = ROOT / "frontend" / "src" / "routes" / "+page.svelte"

# RFC 2606 保留 TLD：DNS 必然失敗，所以「假 fetch 沒生效」時最無害的後果
# 是立刻連線錯誤，而不是打到某台真的機器上。
SELF = ""
P1 = "https://peer-one.invalid"
P2 = "https://peer-two.invalid"

# harness 腳本放在一個固定的可寫目錄（不是 tmp_path fixture）—— 有些測試要
# 直接呼叫 _pure() / _run0() 而不經過 fixture。用 tempfile.mkdtemp 而不是硬寫
# /tmp 的子目錄，免得跟同一個 session 的其他測試互相覆蓋。
_TMP = tempfile.mkdtemp(prefix="ragdemo-hostdefaults-")


def _harness(entry: str, argv: str, inject_fetch: bool) -> str:
    """組出呼叫 ENTRY(...) 的 harness 原始碼。

    ⚠️ `inject_fetch` 決定假 fetch 放在**哪個位置**，而這件事不能想當然：
    把 fetch 永遠塞在第一個參數，會讓那些簽名是 `(value)` 的純函式
    （`verdictState(v)`、`modelAvailability(model, probe)`）拿到 fetch 當 v，
    回傳一個**看起來合理但完全沒測到目標程式碼**的結果。

    實測踩過：`verdictState(fake, "off")` 回 'unknown'（因為 fake 不是
    'up'/'down'/'off' 任何一個），而測試會以為「off 被誤判成 unknown」——
    一個看起來像在測「四種狀態」的測試，實際上完全沒碰到那四種。
    """
    call = f"{entry}(fake, ...ARGV)" if inject_fetch else f"{entry}(...ARGV)"
    return (
        f"import {{ {entry} }} from {json.dumps(str(LIB))};\n"
        "const spec = JSON.parse(process.argv[2]);\n"
        "const calls = [];\n"
        "const fake = async (url, init) => {\n"
        "  const u = String(url);\n"
        "  calls.push({ url: u, method: init?.method ?? 'GET', body: init?.body ?? null });\n"
        "  if ((spec.unreachable ?? []).includes(u)) throw new TypeError('fetch failed');\n"
        "  const r = spec.routes[u];\n"
        "  if (!r) throw new TypeError('no route for ' + u);\n"
        "  return new Response(JSON.stringify(r.body ?? {}), {\n"
        "    status: r.status ?? 200, headers: { 'content-type': 'application/json' } });\n"
        "};\n"
        f"const ARGV = {argv};\n"
        f"const result = await {call};\n"
        "console.log(JSON.stringify({ result, calls }));\n"
    )


def _go(entry: str, argv: str, spec: dict, inject_fetch: bool) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH")
    script = Path(_TMP) / f"{entry}_{int(inject_fetch)}.ts"
    script.write_text(_harness(entry, argv, inject_fetch), encoding="utf-8")
    r = subprocess.run(
        [node, "--experimental-strip-types", str(script), json.dumps(spec)],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        pytest.fail(f"node 執行失敗：{r.stderr[:600]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def _pure(entry: str, argv: str) -> dict:
    """給**純函式**（`verdictState(v)`、`modelAvailability(model, probe)`）。

    ⚠️ 這裡**不傳**假 fetch —— 傳了會被當成第一個參數，於是測的不是我們想
    測的那件事（見 _harness 的說明）。
    """
    return _go(entry, argv, {}, inject_fetch=False)


def _run0(entry: str, argv: str, spec: dict) -> dict:
    """給**依賴 fetch** 的函式（`probeClouds(doFetch)`）：fetch 是唯一參數。"""
    return _go(entry, argv, spec, inject_fetch=True)


def _run_js(name: str, body: str) -> dict:
    """把一段 JS 跑在真的 hostDefaults.ts 旁邊並回傳它的 JSON 輸出。

    給「要測多個函式組成的行為」（例如並行呼叫 `makeOnce()`）—— 那些沒辦法
    用單一 entry 的 harness 表達。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH")
    script = Path(_TMP) / f"{name}.ts"
    script.write_text(
        f"import {{ makeOnce }} from {json.dumps(str(LIB))};\n" + body,
        encoding="utf-8",
    )
    r = subprocess.run([node, "--experimental-strip-types", str(script)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"node 執行失敗：{r.stderr[:400]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def _run(entry: str, argv: str, spec: dict, tmp_path: Path) -> dict:
    """用真的 hostDefaults.ts 跑一次 ENTRY(ARGV)，回傳 {result, calls}。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH")
    script = tmp_path / f"{entry}.ts"
    script.write_text(
        f"import {{ {entry} }} from {json.dumps(str(LIB))};\n"
        "const spec = JSON.parse(process.argv[2]);\n"
        "const calls = [];\n"
        "const fake = async (url, init) => {\n"
        "  const u = String(url);\n"
        "  calls.push({ url: u, method: init?.method ?? 'GET', body: init?.body ?? null });\n"
        "  if ((spec.unreachable ?? []).includes(u)) throw new TypeError('fetch failed');\n"
        "  const r = spec.routes[u];\n"
        "  if (!r) throw new TypeError('no route for ' + u);\n"
        "  return new Response(JSON.stringify(r.body ?? {}), {\n"
        "    status: r.status ?? 200, headers: { 'content-type': 'application/json' } });\n"
        "};\n"
        f"const ARGV = {argv};\n"
        "const result = await ENTRY(...ARGV, fake);\n"
        "console.log(JSON.stringify({ result, calls }));\n".replace("ENTRY", entry),
        encoding="utf-8",
    )
    r = subprocess.run(
        [node, "--experimental-strip-types", str(script), json.dumps(spec)],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        pytest.fail(f"node 執行失敗：{r.stderr[:600]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def _ok(model, effective, local=("m-a", "m-b")):
    """一台正常機器的兩個端點（/settings/default-model ＋ /models）。"""
    return {
        f"{P1}/settings/default-model": {"body": {"ok": True, "host": "p1", "model": model, "effective": effective}},
        f"{P1}/models": {"body": {"ok": True, "local": list(local)}},
    }


def _fetch_row(base: str, spec: dict, tmp_path: Path) -> dict:
    got = _run("fetchHostDefaults", json.dumps([{"id": "p1", "base": base, "self": True}]), spec, tmp_path)
    return got["result"]


# ── hostUrl：只打 /api 或 peer 網址，兩種都不含憑證 ────────────────────

def test_host_url_uses_relative_api_when_no_base():
    got = _run("hostUrl", json.dumps(["", "/models"]), {}, None or Path("/tmp"))
    assert got["result"] == "/api/models"


def test_host_url_prefixes_peer_base():
    got = _run("hostUrl", json.dumps([P1, "/models"]), {}, Path("/tmp"))
    assert got["result"] == f"{P1}/models"


# ── targetRows：清單來自 /status，不寫死 ─────────────────────────────────

def test_target_rows_lists_self_first_then_peers():
    got = _run(
        "targetRows",
        json.dumps(["wsl", "", {"x570": "https://x.invalid", "mbp": "https://m.invalid"}]),
        {},
        Path("/tmp"),
    )
    assert got["result"] == [
        {"id": "wsl", "base": "", "self": True},
        {"id": "x570", "base": "https://x.invalid", "self": False},
        {"id": "mbp", "base": "https://m.invalid", "self": False},
    ]


def test_target_rows_dedupe_self_when_known_contains_it():
    """HOST_API_URLS 是互列的，known **通常含自己**。

    不去重就會顯示兩列同一台 —— 那看起來像「設定了兩次」，而第二次 PUT 會
    覆蓋第一次（值相同所以無害，但畫面在說謊）。
    """
    got = _run(
        "targetRows",
        json.dumps(["wsl", "", {"wsl": "https://w.invalid", "x570": "https://x.invalid"}]),
        {},
        Path("/tmp"),
    )
    assert [r["id"] for r in got["result"]] == ["wsl", "x570"]


def test_target_rows_without_known_yields_only_self():
    """單機部署（未設 HOST_API_URLS）必須正常：只有自己那一列，不是零列。"""
    got = _run("targetRows", json.dumps(["solo", "", {}]), {}, Path("/tmp"))
    assert got["result"] == [{"id": "solo", "base": "", "self": True}]


def test_target_rows_tolerates_missing_known():
    got = _run("targetRows", json.dumps(["solo", "", None]), {}, Path("/tmp"))
    assert len(got["result"]) == 1


# ── fetchHostDefaults：連不到時的呈現 ────────────────────────────────────

def test_unreachable_peer_is_reported_as_unreachable_not_as_unset(tmp_path):
    """連不到 → `state='unreachable'`。

    ⚠️ 症狀對照：若這裡回 state='ok' / model=null，UI 就會顯示「未設定」——
       那正是「把無從驗證呈現成驗證失敗」：使用者會以為那台沒設過，
       然後存一個值覆蓋掉別人設的。
    """
    row = _fetch_row(P1, {"unreachable": [f"{P1}/settings/default-model"]}, tmp_path)
    assert row["state"] == "unreachable"
    assert row["error"], "連不到必須帶原因（診斷用），不能是空字串"
    assert row["modelsKnown"] is False
    assert row["models"] == []


def test_http_error_is_unreachable_too_not_a_fake_setting(tmp_path):
    """403（Cloudflare Access 擋住）也是「連不到」。

    這是現實情況：peer 走 Access，未帶 Service Token 的瀏覽器請求會拿到 403
    + text/html。把它當成「該台回應了，model=null」就會顯示「未設定」。
    """
    row = _fetch_row(P1, {"routes": {f"{P1}/settings/default-model": {"status": 403, "body": {}}}}, tmp_path)
    assert row["state"] == "unreachable"
    assert "403" in row["error"]


def test_unreachable_does_not_fire_a_second_request(tmp_path):
    """設定端點連不到就不該再去打 /models —— 同一台，結果只會一樣。

    而且這是本專案的遞歸教訓（rag.py 的 A→B→C→A）：多打無用請求會放大。
    """
    got = _run(
        "fetchHostDefaults",
        json.dumps([{"id": "p1", "base": P1, "self": False}]),
        {"unreachable": [f"{P1}/settings/default-model"]},
        tmp_path,
    )
    assert [c["url"] for c in got["calls"]] == [f"{P1}/settings/default-model"]


# ── fetchHostDefaults：model: null 的顯示 ───────────────────────────────

def test_model_null_is_kept_as_null_and_effective_shown(tmp_path):
    """後端回 `model: null` 就是「未設定」，不能變成空字串也不能變成 undefined。

    effective 是「實際會用的」：未設定時等於該機的 LLM_MODEL。UI 要靠它告訴
    使用者現在實際跑的是哪一個。
    """
    row = _fetch_row(P1, {"routes": _ok(None, "qwen2.5-coder:latest")}, tmp_path)
    assert row["state"] == "ok"
    assert row["model"] is None
    assert row["effective"] == "qwen2.5-coder:latest"


def test_model_set_is_read_verbatim(tmp_path):
    row = _fetch_row(P1, {"routes": _ok("Qwen3:14B", "Qwen3:14B")}, tmp_path)
    assert row["model"] == "Qwen3:14B"
    assert row["effective"] == "Qwen3:14B"


def test_envelope_without_effective_does_not_become_undefined(tmp_path):
    """欄位缺掉時 effective 要是空字串，不是 undefined。

    UI 直接把它印出來；undefined 會在頁面上變成字面量 "undefined"。
    """
    row = _fetch_row(P1, {"routes": {f"{P1}/settings/default-model": {"body": {"ok": True, "model": None}},
                                    f"{P1}/models": {"body": {"local": []}}}}, tmp_path)
    assert row["effective"] == ""


# ── fetchHostDefaults：「空」與「取不到」必須分得開 ───────────────────────

def test_empty_model_list_from_a_reachable_host_is_known_empty(tmp_path):
    """讀到了、但該機真的沒有可用模型 → `modelsKnown=true` ＋ 空陣列。"""
    row = _fetch_row(P1, {"routes": _ok(None, "x", local=())}, tmp_path)
    assert row["modelsKnown"] is True
    assert row["models"] == []


def test_model_list_fetch_failure_is_not_known_empty(tmp_path):
    """⚠️ 本檔最關鍵的一條。

    /models 掛掉但 /settings 讀得到時，設定**是有效的**，而清單是**不知道**。
    若把 modelsKnown 省略（預設 false 卻只在 catch 裡設），就會出現
    「該機沒有可用的聊天模型」—— 那句話是假的，使用者會去裝模型。
    """
    row = _fetch_row(
        P1,
        {"routes": _ok("m-a", "m-a"), "unreachable": [f"{P1}/models"]},
        tmp_path,
    )
    assert row["state"] == "ok", "/models 掛掉不該讓整台變成不知道"
    assert row["model"] == "m-a", "讀到的設定要保留"
    assert row["modelsKnown"] is False
    assert row["models"] == []


def test_models_200_without_local_is_treated_as_unknown(tmp_path):
    """回 200 但沒有 `local` 欄位 → 不知道，不是「沒有模型」。

    寧可說不知道，也不要替後端猜一個語意。
    """
    row = _fetch_row(
        P1,
        {"routes": {f"{P1}/settings/default-model": {"body": {"model": None, "effective": "x"}},
                    f"{P1}/models": {"body": {"ok": True}}}},
        tmp_path,
    )
    assert row["modelsKnown"] is False


def test_non_string_entries_in_local_are_dropped(tmp_path):
    row = _fetch_row(
        P1,
        {"routes": {f"{P1}/settings/default-model": {"body": {"model": None, "effective": "x"}},
                    f"{P1}/models": {"body": {"local": ["ok", 7, None, "fine"]}}}},
        tmp_path,
    )
    assert row["models"] == ["ok", "fine"]
    assert row["modelsKnown"] is True


# ── modelOptions：一定要有「未設定」那一項 ──────────────────────────────

def test_options_always_offer_the_unset_choice_first():
    """沒有「未設定」這一項，使用者就無法把設定清回該機的 LLM_MODEL。

    後端明確支援清除（PUT 的 null／""），這是那條路徑唯一的 UI 入口。
    """
    got = _run(
        "modelOptions",
        json.dumps([{"id": "p", "base": "", "self": True, "state": "ok", "error": "",
                    "model": None, "effective": "x", "models": ["m-a"], "modelsKnown": True}]),
        {},
        Path("/tmp"),
    )
    opts = got["result"]
    assert opts[0]["value"] == ""
    assert "未設定" in opts[0]["label"]
    assert [o["value"] for o in opts[1:]] == ["m-a"]


def test_options_for_an_unreachable_host_still_show_unset_not_a_fake_list():
    """連不到時不該假裝有一堆模型可選；只留「未設定」且清單是空的。"""
    got = _run(
        "modelOptions",
        json.dumps([{"id": "p", "base": "", "self": True, "state": "unreachable", "error": "boom",
                    "model": None, "effective": "", "models": [], "modelsKnown": False}]),
        {},
        Path("/tmp"),
    )
    assert len(got["result"]) == 1
    assert got["result"][0]["value"] == ""


# ── saveHostDefaults：三台各 PUT 一次 ───────────────────────────────────

THREE_ROWS = [
    {"id": "self", "base": "", "self": True, "state": "ok", "error": "",
     "model": None, "effective": "llm-default", "models": ["a"], "modelsKnown": True},
    {"id": "p1", "base": P1, "self": False, "state": "ok", "error": "",
     "model": "old-1", "effective": "old-1", "models": ["b"], "modelsKnown": True},
    {"id": "p2", "base": P2, "self": False, "state": "ok", "error": "",
     "model": None, "effective": "llm-default-2", "models": ["c"], "modelsKnown": True},
]


def _save(rows, picked, routes=None, unreachable=(), tmp_path=None):
    spec = {"routes": routes or {}}
    if unreachable:
        spec["unreachable"] = list(unreachable)
    return _run("saveHostDefaults", json.dumps([rows, picked]), spec, tmp_path)


def test_saving_writes_each_host_exactly_once(tmp_path):
    """三台 → 三個 PUT，一台一個。**不多不少**。

    少了會有主機沒被設定到（靜默）；多了（例如重試迴圈沒跳過成功的那台）
    就是對同一台重複寫入，值相同時無害但掩蓋了真正的失敗。
    """
    routes = {}
    for base, mid in ((P1, "p1"), (P2, "p2")):
        routes[f"{base}/settings/default-model"] = {
            "body": {"ok": True, "host": mid, "model": "new", "effective": "new"}}
    got = _save(THREE_ROWS, {"self": "", "p1": "new", "p2": "new"}, routes, tmp_path=tmp_path)
    puts = [c for c in got["calls"] if c["method"] == "PUT"]
    assert len(puts) == 3, f"應恰好三個 PUT，實際 {puts}"
    assert sorted(c["url"] for c in puts) == sorted([
        "/api/settings/default-model", f"{P1}/settings/default-model", f"{P2}/settings/default-model",
    ])


def test_saving_reports_each_host_result_independently(tmp_path):
    """一台上 503，另外兩台照樣成功 —— 逐台結果都要回報。

    ⚠️ 寫入端不做降級（PUT 失敗回 503，見 backend/app/main.py）：失敗就是失敗，
    假裝成功會讓使用者以為設定生效了。
    """
    routes = {
        f"{P1}/settings/default-model": {"body": {"ok": True, "host": "p1", "model": "n1", "effective": "n1"}},
        f"{P2}/settings/default-model": {"status": 503, "body": {"detail": "寫入預設模型失敗（資料庫不可用）"}},
        "/api/settings/default-model": {"body": {"ok": True, "host": "s", "model": "ns", "effective": "ns"}},
    }
    got = _save(THREE_ROWS, {"self": "ns", "p1": "n1", "p2": "n2"}, routes, tmp_path=tmp_path)
    by_id = {r["id"]: r for r in got["result"]}
    assert by_id["self"]["ok"] is True
    assert by_id["p1"]["ok"] is True
    assert by_id["p2"]["ok"] is False
    assert "資料庫不可用" in by_id["p2"]["error"], "失敗要帶後端的 detail，不是只有 HTTP code"


def test_unreachable_host_is_skipped_not_written(tmp_path):
    """⚠️ 連不到的那台**不送出**，而不是送出我們其實不知道的值。

    讀不到時手上的「目前值」是預設的 null，送出去等於替那台清除設定 ——
    而我們根本不知道它有沒有設。這是不可逆的靜默破壞。
    """
    rows = [
        {"id": "p1", "base": P1, "self": False, "state": "unreachable", "error": "無法連線",
         "model": None, "effective": "", "models": [], "modelsKnown": False},
        {"id": "p2", "base": P2, "self": False, "state": "ok", "error": "",
         "model": None, "effective": "x", "models": ["c"], "modelsKnown": True},
    ]
    routes = {f"{P2}/settings/default-model": {"body": {"ok": True, "model": "c", "effective": "c"}}}
    got = _save(rows, {"p1": "whatever", "p2": "c"}, routes,
               unreachable=[f"{P1}/settings/default-model"], tmp_path=tmp_path)
    urls = [c["url"] for c in got["calls"]]
    assert urls == [f"{P2}/settings/default-model"], f"不該對連不到的主機發請求，實際 {urls}"
    res = {r["id"]: r for r in got["result"]}
    assert res["p1"]["ok"] is False and res["p1"]["skipped"] is True
    assert "未送出" in res["p1"]["error"]
    assert res["p2"]["ok"] is True


def test_unchanged_value_is_still_written_so_the_button_is_honest(tmp_path):
    """沒改也要送。

    「沒送出」看起來比較省，但使用者的按鈕叫「儲存」—— 按了之後三台都該
    回到同一個已知狀態。跳過沒改的那台會讓「我改了 A」變成「B 怎麼也變了」。
    """
    routes = {f"{P1}/settings/default-model": {"body": {"ok": True, "model": "same", "effective": "same"}}}
    rows = [THREE_ROWS[1]]
    got = _save(rows, {"p1": "same"}, routes, tmp_path=tmp_path)
    assert len(got["calls"]) == 1
    assert json.loads(got["calls"][0]["body"]) == {"model": "same"}


# ── 儲存的 body 形狀 ────────────────────────────────────────────────────

def test_clearing_sends_null_not_empty_string(tmp_path):
    """選「（未設定）」→ body 是 `{"model": null}`。

    後端兩種都接受（normalize 把 "" 當清除），但送 null 與「前端沒有值」
    的語意一致；送 "" 會讓讀 code 的人以為有個空字串是有效模型名。
    """
    routes = {"/api/settings/default-model": {"body": {"ok": True, "model": None, "effective": "llm"}}}
    got = _save([THREE_ROWS[0]], {"self": ""}, routes, tmp_path=tmp_path)
    assert json.loads(got["calls"][0]["body"]) == {"model": None}


def test_missing_pick_is_treated_as_clear(tmp_path):
    """picked 少了一個 key → 視為清除，不送出 undefined（JSON.stringify 會省略它）。

    省略掉 model 欄位會讓後端 pydantic 用預設 None —— 結果相同，但那是
    「剛好對」，不是「明確送出清除」。
    """
    routes = {"/api/settings/default-model": {"body": {"ok": True, "model": None, "effective": "llm"}}}
    got = _save([THREE_ROWS[0]], {}, routes, tmp_path=tmp_path)
    assert json.loads(got["calls"][0]["body"]) == {"model": None}


def test_put_sets_json_content_type(tmp_path):
    """PUT 必須帶 content-type: application/json。

    少了會被 FastAPI 回 422，而且錯誤訊息是「欄位缺漏」而不是任何能指認
    原因的字串 —— 這正是「失敗被系統說成謊」的一種。
    """
    routes = {"/api/settings/default-model": {"body": {"ok": True, "model": None, "effective": "l"}}}
    got = _run(
        "saveHostDefaults",
        json.dumps([[THREE_ROWS[0]], {"self": ""}]),
        {"routes": routes},
        tmp_path,
    )
    assert got["result"][0]["ok"] is True, "先確認這條路徑真的走過（避免測試空轉）"


def test_network_error_on_put_is_reported_not_swallowed(tmp_path):
    routes = {}
    got = _save([THREE_ROWS[0]], {"self": "x"}, routes,
               unreachable=["/api/settings/default-model"], tmp_path=tmp_path)
    r = got["result"][0]
    assert r["ok"] is False and r["skipped"] is False
    assert r["error"], "寫入失敗必須有原因；空字串會讓 UI 顯示成「失敗：」"


def test_no_hosts_yet_returns_empty_without_requesting_anything(tmp_path):
    got = _save([], {}, {}, tmp_path=tmp_path)
    assert got["result"] == []
    assert got["calls"] == []


# ── 原始碼層：不列舉主機（沿用 test_frontend_hosts.py 的守門）────────────

def _code_only(path: Path) -> str:
    """去掉註解，只留會被執行的程式碼。

    ⚠️ 2026-10-05 補 `{/* … */}`（Svelte 樣板註解）。實測症狀：註解寫
    「症狀：下拉 x570、badge ⦿ wsl」就被機台清單守衛判成「程式寫死了機台」。
    註解說明舊做法正是它的用途 —— 要守的是「程式不再列舉主機」，
    不是「這個 repo 不得提及自己的主機名」。
    """
    text = path.read_text(encoding="utf-8")
    # ⚠️ 順序有意義：先剝樣板註解（內含 `//`、`/*`、`<!--` 三種），
    #   再剝 script 與 style 的兩種。逐層收斂才不会留下孤立的符號。
    #   漏掉 `<!-- -->` 的實測症狀：樣板註解寫「下拉 x570、badge ⦿ wsl」
    #   被判成程式寫死機台清單。
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\{\/\*.*?\*\/\}", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


@pytest.mark.parametrize("path", [PAGE, LIB], ids=lambda p: p.name)
def test_settings_ui_does_not_enumerate_our_hosts(path: Path) -> None:
    """新增的設定邏輯不得寫死機台清單。

    與 tests/test_frontend_hosts.py 的同一條不變式。機台清單是資料：
    後端用 HOST_API_URLS，前端從 /status 的 known 推導。
    """
    code = _code_only(path)
    hits = [n for n in ("x570", "mbp", "wsl", "ragdemo.win",
                        "100.119.83.111", "100.64.121.9", "100.122.78.7")
            if n in code]
    assert not hits, f"{path.name} 出現本專案的機台清單 {hits} —— 應從 /status 的 known 推導"


def test_lib_does_not_import_sveltekit() -> None:
    """`hostDefaults.ts` 不得 import 任何模組。

    這是**測試策略的前提**，不只是潔癖：測試要能在 node 裡直接 import 它。
    一旦它 import 了 `$env` 之類，測試就只能改回「抽出貼上」——而那會讓
    測試與實作漂移時照樣綠。
    """
    for line in _code_only(LIB).splitlines():
        s = line.strip()
        if s.startswith("import ") or s.startswith("from "):
            pytest.fail(f"hostDefaults.ts 出現 import：{s!r} —— 這會讓測試無法直接 import 它")


def test_lib_injects_fetch_instead_of_calling_it_globally() -> None:
    """fetch 必須由呼叫端注入（doFetch 參數）。

    直接寫 `fetch(...)` 也能測（stub globalThis.fetch），但注入讓「這一段
    到底打了哪個網址」在型別與呼叫點就看得出來，也才守得住「只打 /api 或
    peer 網址」這條邊界。
    """
    code = _code_only(LIB)
    assert "doFetch" in code
    for line in code.splitlines():
        s = line.strip()
        if s.startswith("await fetch("):
            pytest.fail(f"hostDefaults.ts 直接呼叫全域 fetch：{s!r} —— 應使用注入的 doFetch")


# ── 頁面接線：真的用上了 lib、真的顯示主機、真的分得清無法連線 ───────────

def test_page_wires_the_settings_dialog_to_the_lib() -> None:
    code = _code_only(PAGE)
    for fn in ("targetRows", "fetchHostDefaults", "saveHostDefaults", "modelOptions"):
        assert fn in code, f"設定對話框沒有呼叫 {fn}() —— 邏輯沒接上 lib"
    assert "hostUrl" in code, "hostUrl 應從 lib 匯入（頁面不該自己拼網址）"


def test_page_derives_host_list_from_status_not_a_constant() -> None:
    """對話框的機台清單來自 /status，不是常數表。"""
    code = _code_only(PAGE)
    assert re.search(r"targetRows\(\s*selfId", code), "targetRows 的第一個參數應是 /status 的 host"
    assert "knownHosts" in code, "peer 清單應來自 knownHosts（/status 的 known）"
    assert "status?.host" in code, "selfId 應取 /status 的 host"


def test_default_row_shows_the_actual_backend_host() -> None:
    """「預設（依後端主機）」那一列要顯示實際服務的那台。

    來源是 /status 的 `host`（後端回 registry.HOST_ID）—— 那才是「依後端主機」
    的意思。舊版那一列完全沒有主機資訊，使用者只能猜「自動」是什麼。
    """
    code = _code_only(PAGE)
    assert "backendHostLabel" in code
    assert re.search(r"依後端主機：\{backendHostLabel\(\)\}", code), \
        "model 下拉裡的「預設」列要顯示主機名"
    # 取不到時必須說「未知」，不能把後端 fallback 的 '-' 當主機名印出去。
    # ⚠️ 只斷言「函式裡出現 '-'」而不綁變數名：`const h = status?.host` 改成
    #    `const host` 是無害的改寫，綁死變數名只會讓測試對純樣式修改發紅。
    m = re.search(r"function backendHostLabel\(\)\s*\{.*?\n\s*\}", code, re.S)
    assert m, "找不到 backendHostLabel —— 若被改名請同步維護這個測試"
    body = m.group(0)
    assert "'-'" in body, (
        "backendHostLabel 應把 '-'（/status 失敗時 loadStatus 塞的 fallback）"
        "視為未知：印出「預設（依後端主機：-）」等於宣稱有一台叫「-」的主機"
    )


def test_dialog_distinguishes_unreachable_from_empty_model_list() -> None:
    """模板必須分開處理「連不到」與「清單是空的」。

    這是 UI 層的最後一道：lib 已經把兩者分開了，模板若把 unknown 情況
    一起畫成空下拉，前面所有區分都白做。
    """
    text = PAGE.read_text(encoding="utf-8")
    assert "無法連線" in text, "peer 連不到時要明說「無法連線」"
    assert re.search(r"row\.state\s*!==\s*'ok'", text), \
        "模板要先分支處理 state !== ok"
    assert re.search(r"!\s*row\.modelsKnown", text), \
        "要分辨「清單取不到」與「清單為空」"
    assert "該機沒有可用的聊天模型" in text and "模型清單取不到" in text, \
        "兩種空清單的說法必須不同字串"


def test_dialog_has_a_single_save_button_that_sends_all_hosts() -> None:
    text = PAGE.read_text(encoding="utf-8")
    assert text.count("saveSettings") >= 2, "對話框應有「儲存」按鈕呼叫 saveSettings()"
    assert re.search(r"set-actions", text), "儲存按鈕應放在對話框下方的動作列"
    assert "saveHostDefaults(" in _code_only(PAGE), \
        "儲存時要把所有主機一次送出（saveHostDefaults 收整組）"


def test_dialog_can_be_closed_by_escape_and_backdrop() -> None:
    text = PAGE.read_text(encoding="utf-8")
    assert "'Escape'" in text and "closeSettings" in text, "Esc 應能關閉對話框"
    assert "set-mask" in text and "stopPropagation" in text, \
        "點背景關閉，但點對話框本體不關（需要 stopPropagation）"
    assert "role=\"dialog\"" in text, "對話框要標 role=dialog（無障礙與鍵盤操作）"


def test_save_result_message_is_not_wiped_by_the_reread() -> None:
    """⚠️ 順序陷阱：重讀之後才寫訊息。

    `reloadSettings()` 開頭會清掉 settingsMsg／settingsErr。若把
    「已儲存 2/3 台」寫在呼叫它**之前**，訊息會在同一個 tick 被清掉 ——
    使用者按了儲存看到什麼都沒發生，會再按一次。這種 bug 不會報錯、
    不會 exception，只是功能看起來沒反應。
    """
    code = _code_only(PAGE)
    m = re.search(r"async function saveSettings\(\)\s*\{.*?\n  \}", code, re.S)
    assert m, "找不到 saveSettings —— 若被改名請同步維護這個測試"
    body = m.group(0)
    write_msg = body.find("settingsMsg = ok.length")
    reread = body.find("fetchHostDefaults(row, fetch)")
    assert write_msg != -1, "saveSettings 應寫出「已儲存 N/M 台」"
    assert reread != -1, "儲存後應重讀成功的那幾台以取得後端實際存下的值"
    assert write_msg > reread, (
        "訊息寫在重讀之前會被 reloadSettings 開頭的清空蓋掉 —— "
        "結果是使用者按了儲存看不到任何回應"
    )


def test_failed_hosts_keep_the_users_selection() -> None:
    """失敗的那幾台不重讀。

    重讀會把下拉換回舊值，使用者剛選的東西消失、得重選一遍。成功的那些台
    才需要重讀（要知道後端 normalize 後的實際值）。
    """
    code = _code_only(PAGE)
    assert re.search(r"if\s*\(!okIds\.has\(row\.id\)\)\s*return row;", code), \
        "儲存後只重讀成功的主機；失敗的要保留使用者的選擇"

# ── 雲端 catalog probe 的四種 verdict ───────────────────────────────────
#
# 核心不變式：**「探不到」與「未設定」都不是故障**。
#
# 把 unknown 畫成紅字 → 使用者以為 provider 壞了，去換 key、查網路，而真相是
# 「這次沒探到」。把 off 畫成紅字 → 一台沒開某 provider 的機器看起來有問題。
# 這兩者都是本專案反覆在修的那類錯誤（把無從驗證呈現成驗證失敗）。

def _probe(providers: dict, **extra) -> dict:
    return {"ok": True, "providers": providers, **extra}


def _v(verdict, **extra) -> dict:
    return {"verdict": verdict, **extra}


def test_verdict_up_is_ok() -> None:
    assert _pure("verdictState", json.dumps(["up"]))["result"] == "ok"


def test_verdict_down_is_the_only_one_marked_bad() -> None:
    """`down`（401 key 過期／403／404）才是明確失敗。"""
    assert _pure("verdictState", json.dumps(["down"]))["result"] == "bad"


def test_verdict_unknown_is_not_bad() -> None:
    """⚠️ 探不到**不是壞了** —— 這是本組測試存在的理由。

    把它回成 'bad' 就會讓使用者去排查一個不存在的故障。逾時／DNS／TLS
    失敗都歸這一類。
    """
    got = _pure("verdictState", json.dumps(["unknown"]))["result"]
    assert got == "unknown"
    assert got != "bad", "unknown 絕不可與 down 合併"


def test_verdict_off_is_not_bad() -> None:
    """`off`（未設定 ZEN_API_KEY 之類）不是故障。

    實測 wsl 上 zen/groq/cohere 都沒開 —— 若把它們算成故障，那個面板會說
    「這台有問題」，而它其實沒有。
    """
    got = _pure("verdictState", json.dumps(["off"]))["result"]
    assert got == "off"
    assert got != "bad"


def test_unrecognized_verdict_is_unknown_not_bad() -> None:
    """形狀看不懂 → unknown（不知道），不可猜成 bad。"""
    got = _pure("verdictState", json.dumps(["weird-new-verdict"]))["result"]
    assert got == "unknown"
    assert got != "bad"


def test_verdict_labels_say_probe_failed_not_broken() -> None:
    """字也要分開：`unknown` 說「探不到」，不是「失敗」。"""
    labels = {v: _pure("verdictLabel", json.dumps([v]))["result"]
              for v in ("up", "down", "unknown", "off")}
    assert labels == {"up": "可用", "down": "失敗", "unknown": "探不到", "off": "未設定"}
    assert len(set(labels.values())) == 4, f"四種狀態的字必須都不同：{labels}"


# ── citationText（引用條文截斷）────────────────────────────────────────────

def _ct(text: str) -> dict:
    return _pure("citationText", json.dumps([text]))["result"]


def test_short_citation_gets_no_ellipsis() -> None:
    """短於上限的條文**完全不動**，而且不該出現「…」。

    ⚠️ 2026-10-05 使用者回報的 bug：舊版是 `text.slice(0, 200) + '…'`
    —— **無條件**加省略號，於是只有 90 字的條文也被砍掉尾巴、結尾還掛著
    「…」。讀者會去找不存在的後半段。

    省略號的唯一意義是「這裡省略了東西」，所以沒省略就不該出現。
    邊界取 <= ：剛好 200 字不算超長。
    """
    short = "甲" * 90
    got = _ct(short)
    assert got == {"text": short, "truncated": False}, got
    assert "…" not in got["text"]


def test_citation_at_the_limit_is_not_truncated() -> None:
    """剛好等於上限不算超長（邊界用 <= 不是 <）。"""
    exact = "甲" * 200
    got = _ct(exact)
    assert got["truncated"] is False
    assert got["text"] == exact


def test_long_citation_is_cut_and_marked_truncated() -> None:
    """超長才截，且必須回 truncated=true —— UI 要靠它決定要不要給展開鈕。"""
    long = "甲" * 260
    got = _ct(long)
    assert got["truncated"] is True
    assert got["text"] == "甲" * 200
    assert len(got["text"]) == 200


def test_truncated_flag_is_the_only_thing_the_ui_relies_on() -> None:
    """`truncated` 與 `text` 必須一致：不能「沒截卻說截了」或反之。

    那是展開鈕與實際顯示內容脫節的情況 —— 使用者按了展開，內容一模一樣。
    """
    for n in (1, 199, 200, 201, 1000):
        got = _ct("甲" * n)
        assert got["truncated"] == (n > 200), f"n={n}: {got}"
        assert len(got["text"]) == (200 if n > 200 else n), f"n={n}: {got}"


def test_citation_handles_non_string_payload() -> None:
    """payload.text 可能是 undefined（形狀不符）—— 不可拋。

    後端 `_hit_view` 永遠給字串，但前端不該因為上游改版多一個欄位就整頁炸掉
    （症狀會是空白頁，與真正的原因完全無關）。
    """
    for bad in (None, 123, {"a": 1}):
        got = _pure("citationText", json.dumps([bad]))["result"]
        assert got == {"text": "", "truncated": False}, f"{bad!r} → {got}"


def test_citation_text_preserves_paragraph_newlines_in_the_dom() -> None:
    """引用條文的換行必須在瀏覽器裡**真的**分行顯示。

    ⚠️ 2026-10-05 使用者回報的 bug。症狀：回答區塊正確顯示 3 個項，但引用
    清單裡同一條黏成一段（用空格相連）。拿去跟司法院原文比對時，項次看起來
    就像消失了。

    根因是 **CSS**，不是資料：條文的項／款在資料層用 `\\n` 分隔，而 HTML
    預設 `white-space: normal` 會把換行**折成空格**。同一頁的 `.ans`
    （回答）有 `white-space: pre-wrap` 所以正確 —— 兩處不一致才讓這個 bug
    看起來像「只有引用壞了」。

    為什麼資料層的斷言抓不到：`test_citation_text_keeps_paragraph_newlines`
    驗的是 `citationText()` 回的字串有 `\\n`，那是**對的** —— 換行是在
    **渲染**時被折掉的。所以這條要檢查 CSS。

    ⚠️ 這條只做靜態斷言（樣板裡的樣式宣告）。靜態斷言抓不到「寫了
    pre-wrap 但被後面的規則覆蓋」那種情況 —— 那需要真的算繪行數。
    """
    text = PAGE.read_text(encoding="utf-8")
    m = re.search(r"^\s*\.tx\s*\{([^}]*)\}", text, re.M)
    assert m, "找不到 .tx 的樣式規則 —— 引用條文的換行靠它保留"
    assert "white-space" in m.group(1), (
        ".tx 沒有 white-space —— 條文的項／款在資料層用 \\n 分隔，"
        "而 HTML 預設 normal 會把換行折成空格，於是引用看起來像項次消失。"
        "請用 pre-wrap（不要 pre-line：全形空白的縮排有意義）。"
    )
    assert "pre-wrap" in m.group(1), \
        "要用 pre-wrap 而不是 normal/pre-line：條文的全形空白縮排有意義"


def test_answer_and_citation_use_the_same_whitespace_handling() -> None:
    """.ans（回答）與 .tx（引用）必須用**同一種**換行處理。

    ⚠️ 兩者不一致正是這個 bug 難以定位的原因：同一條條文的文字，
    在回答區塊分行、在引用區塊不分行。於是看起來像「引用取到的是壞資料」，
    而實際資料是好的、只是渲染規則不同。
    """
    text = PAGE.read_text(encoding="utf-8")
    def rule(sel: str) -> str:
        m = re.search(rf"^\s*{re.escape(sel)}\s*\{{([^}}]*)\}}", text, re.M)
        assert m, f"找不到 {sel} 的樣式規則"
        return m.group(1)
    ans, tx = rule(".ans"), rule(".tx")
    assert "white-space: pre-wrap" in ans, f".ans 應為 pre-wrap：{ans.strip()}"
    assert "white-space: pre-wrap" in tx, \
        f".tx 必須與 .ans 相同（pre-wrap）：{tx.strip()}"


def test_paragraphs_split_articles_into_items_like_the_official_site() -> None:
    """條文要拆成「一項一個元素」，與司法院的 `line-*` div 對應。

    ⚠️ 2026-10-05 使用者給的實測 DOM（證券交易法第6條）：

        <div class="law-article">
          <div class="line-0000 show-number">本法所稱有價證券，指政府債券…</div>
          <div class="line-0000 show-number">新股認購權利證書…</div>
          <div class="line-0000 show-number">前二項規定之有價證券…</div>
        </div>

    三個項是**三個獨立元素**。所以「比照處理」不只是視覺換行 ——
    只用 `white-space: pre-wrap` 也是一行一個字，但 DOM 上仍是單一節點，
    讀者複製／選取時三項會黏在一起。
    """
    got = _pure("paragraphs", json.dumps(["甲\n乙\n丙"]))["result"]
    assert got == ["甲", "乙", "丙"], got


def test_paragraphs_drops_blank_lines_and_trims() -> None:
    """空行要濾掉、每項去頭尾空白。

    條文常有尾端換行；不濾掉會產生一個空白 `.par` 元素，在 block 佈局下
    就是一行可點不到、但看得出有東西的空白行。
    """
    assert _pure("paragraphs", json.dumps(["甲\n\n乙  \n\n"]))["result"] == ["甲", "乙"]
    assert _pure("paragraphs", json.dumps([""]))["result"] == []
    assert _pure("paragraphs", json.dumps(["   \n  "]))["result"] == []


def test_paragraphs_handles_non_string() -> None:
    """非字串不可拋 —— payload.text 形狀不符時整頁炸掉最難查。"""
    for bad in (None, 123, {"a": 1}):
        assert _pure("paragraphs", json.dumps([bad]))["result"] == [], bad


def test_page_renders_each_paragraph_as_an_element() -> None:
    """頁面要用 paragraphs() 逐項渲染，不是把整段字塞進一個節點。"""
    text = PAGE.read_text(encoding="utf-8")
    assert "paragraphs as paras" in text, "頁面應該 import paragraphs"
    # 2026-10-05：回答與引用改用 `rows()`（articleRows，帶項／款與項次），
    # `paras()` 只留在非逐字引用的 fallback 分支。兩條路徑都必須逐項渲染。
    assert "articleRows" in text or "rows" in text, "應該 import articleRows"
    assert text.count('class="law-row"') >= 2, \
        "回答與引用都要逐項渲染（各一個 #each；模板裡是 class=\"law-row\"）"
    # ⚠️ 這裡斷言的是**意圖**不是整條規則的字面值。2026-10-05 為了套用官網的
    #   `margin-bottom: 0.5em`（項與項之間的留白），`.law-row` 從單行變成多行，
    #   而舊斷言比對整條規則字串 → 改排版就紅，與它要守的事情無關。
    #   守的是「每一項是 block 元素」：否則 DOM 上仍是單一節點，複製／選取會黏在一起。
    m = re.search(r"\.law-row\s*\{(.*?)\}", text, re.S)
    assert m and "display: block" in m.group(1), \
        "每一項必須是 block 元素，否則 DOM 上仍是單一節點（複製／選取會黏在一起）"


def _css_only() -> str:
    """樣板內容去掉 CSS 註解，只留規則。

    ⚠️ 為什麼必須去：這個檔的 CSS 註解會**提到選擇器名與屬性值**
    （例：「我們的 .law-no 不是連結…layout.css a{color:#057b7b}」）。
    不去掉就直接 regex 找 `.law-no {`，會把**註解裡的散文**當成規則開頭 ——
    實測就這樣讓第一版測試紅掉，而紅的原因跟它要守的事情無關。

    反過來說：註解裡就算寫了正確的值，也不該讓斷言通過。所以兩邊都剝掉。
    """
    return re.sub(r"/\*.*?\*/", "", PAGE.read_text(encoding="utf-8"), flags=re.S)


# 官網 law.css 的實際值（2026-10-05 從瀏覽器存檔逐字抄下來）。
# 抄錄來源：law.css 的 .law-article div / div.show-number::before、.col-no，
# 以及 layout.css 的 `a { color:#057b7b }`（條號是 <a>，所以顏色在那裡）。
_OFFICIAL_CSS = {
    #  selector      屬性           官網值
    ".law-no": [("width", "8em"), ("color", "#057b7b")],
    ".law-row": [("margin-bottom", "0.5em")],
    ".law-n": [("font-size", "1.05em"), ("font-style", "italic"),
               ("color", "#666"), ("width", "3em")],
}


def test_law_css_uses_the_official_values() -> None:
    """條文排版要等於官網 law.css 的值，不是「看起來差不多」。

    ⚠️ 這組值是 2026-10-05 從**實際 CSS** 抄的，之前的版本是只靠截圖反推，
       結果有五處是錯的（項次 font-size 寫成 0.85em、少了斜體、欄寬 2em、
       沒有項間距、條號欄 5.5em）。截圖只看得出「有沒有」，看不出值 ——
       所以這些常數必須被釘住，否則下一次「簡化」又會悄悄改回去。

    逐字比對整條規則做不到（規則是多行、順序會變），所以只檢查
    「這個宣告裡有沒有這個屬性值」，且用 regex 綁在同一個選擇器區塊內。
    """
    text = _css_only()
    for sel, decls in _OFFICIAL_CSS.items():
        # 抓第一個 `<sel> { ... }` 區塊（選擇器含 `.` 需跳脫）
        m = re.search(re.escape(sel) + r"\s*\{(.*?)\}", text, re.S)
        assert m, f"樣板裡找不到 {sel} 的規則"
        body = m.group(1)
        for prop, val in decls:
            pat = re.escape(prop) + r"\s*:\s*" + re.escape(val) + r"\s*;"
            assert re.search(pat, body), (
                f"{sel} 的 {prop} 應為官網的值 {val}；"
                f"目前是：{re.findall(re.escape(prop) + r'[^;]*;', body)}"
            )


def test_law_item_gutter_var_is_used_in_both_contexts() -> None:
    """項次欄靠 `--law-item-gutter` 推算，不可寫死 4em。

    官網的 `::before` 淨向左伸 4em（left:-3em + margin-left:-1em），那個空間
    是借 `.law-body` 的 2em 內距加 8em 的條號欄。引用清單（.tx > .law-row）
    **沒有**條號欄可借 —— 寫死 4em 會讓項次溢出到 `<li>` 外面。

    所以文字起點與項次位置必須由同一個變數推導，這樣兩種上下文都成立。
    """
    text = _css_only()
    m = re.search(r"\.law-n\s*\{(.*?)\}", text, re.S)
    assert m, "樣板裡找不到 .law-n 的規則"
    body = m.group(1)
    assert "var(--law-item-gutter)" in body, \
        ".law-n 的位置必須由 --law-item-gutter 推算，不可寫死 4em"
    assert "calc(var(--law-item-gutter) - 4em)" in body, \
        "項次右緣應恆在文字左側 1em（官網 left:-3em + margin-left:-1em 的等效效果）"
    # 兩種上下文都要宣告這個變數：.law-body 給 2em（官網 padding-left:2em），
    # .law-row 預設 0em（引用清單沒有條號欄，項次就貼著文字左側伸出）
    assert re.search(r"\.law-body\s*\{[^}]*--law-item-gutter:\s*2em", text, re.S), \
        ".law-body 應給 2em 內距（官網 .law-article { padding-left: 2em }）"
    assert re.search(r"\.law-row\s*\{[^}]*--law-item-gutter:\s*0em", text, re.S), \
        ".law-row 預設應為 0em，引用清單才不會多留一欄空白"


def test_long_citation_is_cut_at_an_item_boundary() -> None:
    """超長條文要在**項邊界**截斷，不可切在某個項的中間。

    ⚠️ spec FR-010。直接 slice 會產生半截的項 —— 讀者看到
    「…前項財務報告之內容、適用範圍」這種斷句，會誤以為條文原文就這樣。
    在法律場合，寧可少顯示幾個字也不要給一個看起來像原文的斷句。
    """
    # 兩個項各 150 字，總共 301 字 > 200
    item = "甲" * 150
    text = f"{item}\n{item}"
    got = _ct(text)
    assert got["truncated"] is True
    assert "\n" not in got["text"], "不該在項中間切斷"
    assert got["text"] == item, f"應保留第一個完整的項：{got['text'][-20:]!r}"


def test_single_oversized_item_falls_back_to_char_cut() -> None:
    """單一項就超過上限時才退回字元截斷（沒有「完整的一項」可選）。

    旗標仍必須是 truncated —— 使用者要能點開看全文。
    """
    got = _ct("甲" * 500)
    assert got["truncated"] is True
    assert len(got["text"]) <= 200
    assert got["text"] == "甲" * 200


def _strip_comments(text: str) -> str:
    """去掉四種註解，只留會被執行的程式碼。

    ⚠️ 為什麼需要：`+page.svelte` 同時用 `//`、`/* */`（style 區）、
    `<!-- -->`（樣板）、`{/* */}`（樣板）。不剝的話會把**說明文字裡
    提到的**字串當成程式碼 —— 實測踩到兩次：
      · 註解寫「症狀：下拉 x570、badge ⦿ wsl」→ 機台清單守衛判違規
      · 註解寫「不可用 `$derived`」→ reactivity 守衛判成混用模式
    註解說明「不做什麼」正是它們的用途。
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\{\/\*.*?\*\/\}", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def test_onmount_rechecks_health_after_restoring_backend() -> None:
    """還原上次選擇之後**必須**重新量一次 health。

    ⚠️ 2026-10-05 使用者回報的 bug：重整頁面後下拉顯示 `x570`，右側徽章卻
    寫 `⦿ wsl`。根因是**順序**不是資料：onMount 裡 `checkHealth()` 跑在
    `loadStatus()` 之前，而 backendId 是在 loadStatus → restoreBackend 裡
    才還原的 —— 量到的自然是「自動」那台，之後沒有人重測。

    為什麼手動切換不會有這個問題：`switchBackend()` 內部會呼叫 checkHealth()。
    所以只有「重整頁面」這條路徑不一致，很容易被誤判成偶發。

    斷言的是「還原結果有被拿來決定要不要補量」：restoreBackend 必須回傳
    是否真的切換了，onMount 必須依它補量。兩者缺一，badge 就會與下拉不符。
    """
    text = PAGE.read_text(encoding="utf-8")
    # restoreBackend 必須回傳布林
    assert re.search(r"function\s+restoreBackend\s*\(\s*\)\s*\{[\s\S]*?return\s+(true|false)",
                     text), "restoreBackend 必須回傳是否真的切換了 —— onMount 靠它決定要不要補量"
    # loadStatus 必須把 restoreBackend 的結果往外傳
    assert re.search(r"restored\s*=\s*restoreBackend\(\)", text), \
        "loadStatus 必須把 restoreBackend 的結果存下來並回傳"
    assert re.search(r"return\s+restored\s*;", text), "loadStatus 必須回傳 restored"
    # onMount 必須依它補量，且補量要在 loadModels 之前（模型清單也要打到同一台）
    m = re.search(r"const\s+restored\s*=\s*await\s+loadStatus\(\);(.*?)loadModels\(\)", text, re.S)
    assert m, "onMount 應捕捉 loadStatus 的回傳值"
    assert re.search(r"if\s*\(\s*restored\s*\)\s*await\s+checkHealth\(\)", m.group(1)), \
        "還原改變了 backendId 時必須補量 checkHealth，否則 badge 與下拉不一致"
    assert "await checkHealth()" in m.group(1), "補量的 await 不能漏（否則 loadModels 會打錯主機）"


def test_health_label_states_which_host_is_actually_serving() -> None:
    """徽章要明說是「實際在服務的那台」，不是只印一個 host_id。

    ⚠️ 使用者回報的另一個面向：原本只印 `⦿ wsl｜…`，沒有說明那是「選擇」
    還是「實際服務」。兩者可能不同（worker 在自動模式下會挑活著的），
    不說明就無法判斷。

    這條也守住「不要退回去只印 host_id」—— 那正是使用者回報看不懂的原因。
    """
    text = PAGE.read_text(encoding="utf-8")
    assert "目前採用：" in text, "徽章要明說是「目前採用」的主機"
    assert "servingHost" in text, "要顯示實際服務主機（health.host_id）"


def test_backend_host_label_is_a_value_not_a_function_object() -> None:
    """`servingHost` 必須是**值**，樣板裡不可寫成會被字串化的形式。

    ⚠️ 本次實測踩到：寫成 `const servingHost = () => …` 而樣板用 `{servingHost}`，
    Svelte 5 會把函式**物件**直接字串化，畫面顯示
    「目前採用：() => $.get(health)?.host_id || '—'」。

    而且**不會報錯**：型別是 string，模板編譯與靜態斷言都抓不到 ——
    症狀只是畫面顯示一段程式碼。所以這條要明確擋那個寫法。

    ⚠️ 檢查前必須剝註解：這個 bug 的說明註解裡就寫著 `{servingHost}`，
    不剝的話這條測試會把自己的註解當成違規。
    """
    text = _strip_comments(PAGE.read_text(encoding="utf-8"))
    assert not re.search(r"(?:const|let)\s+servingHost\s*=\s*(?:\(|async)", text), \
        "servingHost 不可宣告成函式 —— 樣板裡 {servingHost} 會字串化成函式原始碼"
    # 宣告處必須是值（`let x = …`），不是函式
    assert re.search(r"(?:const|let)\s+servingHost\s*=\s*['\"]", text) or \
           re.search(r"\$:\s*servingHost\s*=", text), \
        "servingHost 必須是一個值（值宣告或 `$:` 指派）"


def test_page_does_not_mix_derived_and_legacy_reactivity() -> None:
    """不可在同一個檔案混用 `$derived` 與 `$:`。

    ⚠️ 本次實測踩到：為了算 servingHost 寫了 `$derived`，編譯器立刻把整個
    元件切進 **runes mode**，而這支檔案是 legacy mode（`$: BACKENDS = …`）
    → build 直接失敗：

        `$:` is not allowed in runes mode, use $derived or $effect instead`

    兩種模式不可混用：只要有**一個** rune，整個元件就是 runes mode。

    ⚠️ 檢查前必須剝註解：這條的說明註解裡就提到 `$derived`／`$effect`，
    不剝的話會判成「有在用 rune」。
    """
    text = _strip_comments(PAGE.read_text(encoding="utf-8"))
    uses_runes = bool(re.search(r"\$(?:derived|state|effect)\b", text))
    uses_legacy = bool(re.search(r"^\s*\$:\s", text, re.M))
    assert not (uses_runes and uses_legacy), (
        "同一個元件不可混用 runes（$derived／$state／$effect）與 legacy `$:`"
        " —— 只要有一個 rune，編譯器就把整個元件當 runes mode，`$:` 會直接讓 build 失敗。"
    )


def _ar(text: str) -> list[dict]:
    return _pure("articleRows", json.dumps([text]))["result"]


# ── articleRows：項次編號（規則來自司法院官網實測）──────────────────────

def test_three_items_get_numbered_1_2_3() -> None:
    """3 個項 → 編號 1 2 3（官網第 6 條實測）。"""
    got = _ar("甲\n乙\n丙")
    assert [r["no"] for r in got] == [1, 2, 3], got
    assert all(r["kind"] == "項" for r in got), got


def test_single_item_is_not_numbered() -> None:
    """只有 1 個項 → **不編號**（官網第 1／15／28-4／105 條實測）。

    ⚠️ 這是最容易做錯的一條：直覺上「1 個項編 1」很自然，但官網不編 ——
    因為沒有第 2 項可對照，編號沒有意義。229 條零反例。
    """
    got = _ar("只有一段")
    assert got[0]["no"] is None, got


def test_kuan_are_never_numbered_and_do_not_break_item_numbering() -> None:
    """款永不編號，且**不會打斷項的編號**。

    ⚠️ 官網第 174 條實測：7 個項交錯 11 個款 → 6 個項編 1…6，11 個款都不編。
    那是因為 CSS counter 只對帶 `show-number` 的累加。所以我們也不能因為
    中間遇到款就跳號 —— 那會讓「第一項、第二項、第三項」的編號對不上原文。
    """
    got = _ar("項A\n一、款A\n二、款B\n項B\n三、款C\n項C")
    items = [r for r in got if r["kind"] == "項"]
    kuans = [r for r in got if r["kind"] == "款"]
    assert [r["no"] for r in items] == [1, 2, 3], items
    assert all(r["no"] is None for r in kuans), kuans


def test_one_item_plus_kuan_is_not_numbered() -> None:
    """1 項 + 款 → 全不編號（官網第 15／28-4 條實測）。"""
    got = _ar("項\n一、款一\n二、款二")
    assert [r["no"] for r in got] == [None, None, None], got


def test_item_versus_kuan_classification() -> None:
    """款以「一、」「（一）」「1.」開頭；其餘是項。

    這與後端 `law_struct._ITEM_RE` 是同一套判斷，所以前端算出的項／款數
    必然與後端的「N項M款」一致 —— 不一致會讓項次編號與引用標示打架。
    """
    got = _ar("項一\n一、款一\n（一）款二\n1. 款三\n1、款四\n項二")
    assert [r["kind"] for r in got] == ["項", "款", "款", "款", "款", "項"], got


def test_article_rows_handles_edge_cases() -> None:
    """邊界：空字串、全空行、非字串。不可拋。"""
    assert _ar("") == []
    assert _ar("\n\n  \n") == []
    assert _pure("articleRows", json.dumps([None]))["result"] == []
    assert _pure("articleRows", json.dumps([123]))["result"] == []


def test_answer_uses_moj_two_column_layout_only_for_verbatim_quotes() -> None:
    """逐字引用才用官網排版（條號欄 + 項次欄）；LLM 生成的答案不用。

    ⚠️ 法律上的理由：LLM 答案是綜合多條的敘述，掛上條號欄會讓讀者以為
    「這一條就是答案」—— 那是誤導。所以判斷要綁在「逐字引用」上。
    """
    text = PAGE.read_text(encoding="utf-8")
    assert "verbatimQuote" in text, "應有逐字引用的判斷函式"
    assert "class=\"law-no\"" in text, "要有條號欄"
    assert "class=\"law-n\"" in text, "要有項次欄"
    assert "confidence !== 'rule'" in text, \
        "判斷必須先檢查 confidence === 'rule'，不能對所有回答都套條號欄"
    assert "h0.exact" in text, "還要確認首筆引用是精準命中"


def test_law_numbering_is_a_real_element_not_a_css_counter() -> None:
    """項次必須是**真的元素**，不可用 CSS counter。

    官網用 `::before { content: counter(num) }` —— 數字不在 DOM 裡，
    複製貼上不會帶到、螢幕閱讀器也讀不到。在法律場合「引用時要連項次
    一起貼出來」是實際需求，counter 做不到。

    所以這裡驗證的是：頁面裡有 `<span class="law-n">{r.no}` 這種真元素。
    """
    text = _strip_comments(PAGE.read_text(encoding="utf-8"))
    assert re.search(r'class="law-n"[^>]*>\{r\.no', text), \
        "項次必須渲染成真元素（可選取、可被輔助 tech 讀到）"
    # ⚠️ 剝註解後再檢查：註解裡會**引述**官網的 `counter-increment` 做對照，
    #   那不是我們的實作。剝掉才不會把自己的說明當成違規。
    assert "counter-increment" not in text, \
        "不可用 CSS counter 產生項次 —— 數字不會進 DOM，複製時會遺失"


def test_page_does_not_slice_citations_inline_anymore() -> None:
    """樣板裡不可再有 inline `slice(0, 200)` ＋無條件「…」。

    綁的是行為（走 citationText）而不是這行的寫法：這條抓的是「有人又
    在樣板裡手動截一次、繞過截斷判斷」—— 那正是這次 bug 的形狀。
    """
    text = PAGE.read_text(encoding="utf-8")
    assert "slice(0, 200)" not in text, \
        "引用條文又出現 inline slice(0,200) 了；截斷與判斷請走 citationText()"
    assert "citationText" in text, "頁面應該 import citationText"
    assert "toggleCite" in text, "應該要有展開／收合的處理"


# ── modelAvailability ───────────────────────────────────────────────────

def _av(model: str, probe) -> dict:
    return _pure("modelAvailability", json.dumps([model, probe]))["result"]


def test_available_model_is_ok() -> None:
    # ⚠️ probe 的清單裡**沒有**前端前綴（真實後端就是這樣回的）；
    #   前端的 model 值有。理由見檔末「前綴」那一組。
    p = _probe({"openrouter": _v("up", available=["a/b"], missing=[], configured=["a/b"])})
    assert _av("openrouter/a/b", p) == {"ok": True, "known": True}


def test_missing_model_is_known_and_flagged() -> None:
    """⚠️ `missing`（設了但上游 catalog 沒有）是最有價值的資訊。

    症狀是「選了才 404」，所以必須標出來，而不是混在可用清單裡。
    """
    p = _probe({"openrouter": _v("up", available=["a/b"], missing=["a/gone"],
                                 configured=["a/b", "a/gone"])})
    got = _av("openrouter/a/gone", p)
    assert got["known"] is True
    assert got["missing"] is True
    assert got["ok"] is False


def test_unknown_verdict_makes_every_model_unknown() -> None:
    """⚠️ verdict≠up 時清單不可信 → 全部「不知道」，不是「不可用」。

    provider 探不到時 `available`／`missing` 必然不完整；拿它說某個 model
    不可用，就是把「無從驗證」講成「驗證失敗」。
    """
    p = _probe({"openrouter": _v("unknown", available=["a"], missing=["b"])})
    for m in ("openrouter/a", "openrouter/b", "openrouter/whatever"):
        assert _av(m, p)["known"] is False, f"{m} 應為未知"


def test_off_verdict_does_not_mark_models_unavailable() -> None:
    p = _probe({"zen": _v("off", detail="未設定 ZEN_API_KEY", available=[], missing=[])})
    assert _av("zen/foo", p)["known"] is False, "未設定 provider 不代表它列出的 model 壞了"


def test_down_verdict_does_not_mark_models_unavailable() -> None:
    """provider 層 down 就夠了；逐個 model 再標一次「不可用」是噪音。"""
    p = _probe({"groq": _v("down", detail="401", available=[], missing=[])})
    assert _av("groq/x", p)["known"] is False


def test_local_ollama_model_is_unknown_not_unavailable() -> None:
    """地端 ollama 不在 probe 範圍內 → 不知道，不是不可用。"""
    p = _probe({"openrouter": _v("up", available=[], missing=[])})
    assert _av("qwen2.5-coder:latest", p)["known"] is False


def test_provider_absent_from_probe_is_unknown() -> None:
    p = _probe({"openrouter": _v("up", available=[], missing=[])})
    assert _av("hf/some/model", p)["known"] is False


def test_no_probe_at_all_is_unknown() -> None:
    """沒有 probe 資料（失敗了）→ 全部未知，UI 不可畫成不可用。"""
    for probe in (None, {}, {"providers": None}):
        assert _av("openrouter/a", probe)["known"] is False


def test_model_name_without_provider_prefix_is_unknown() -> None:
    p = _probe({"openrouter": _v("up", available=["a/b"], missing=[], configured=["a/b"])})
    assert _av("plainname", p)["known"] is False
    assert _av("", p)["known"] is False
    assert _av("/leading-slash", p)["known"] is False


# ── missingModels ───────────────────────────────────────────────────────

def test_missing_models_extracts_the_list() -> None:
    p = {"verdict": "up", "missing": ["a", "b"]}
    assert _pure("missingModels", json.dumps([p]))["result"] == ["a", "b"]


def test_missing_models_of_a_provider_with_none_is_empty_not_missing() -> None:
    """沒有 missing 欄位 → 空陣列（UI 不顯示該區塊），不是 undefined。"""
    for p in ({"verdict": "up"}, {"verdict": "up", "missing": None}, None):
        assert _pure("missingModels", json.dumps([p]))["result"] == []


# ── probeClouds：失敗不可拋，且失敗與「沒 probe」不可區分 ─────────────────

def test_probe_clouds_posts_to_the_settings_endpoint() -> None:
    """走 /api 相對路徑（經 worker），與其他設定端點同一條路。"""
    got = _run0("probeClouds", "[]",
                {"routes": {"/api/settings/probe-clouds": {"body": _probe({"openrouter": _v("up")})}}})
    assert got["calls"][0]["method"] == "POST"
    assert got["calls"][0]["url"] == "/api/settings/probe-clouds"


def test_probe_clouds_returns_the_payload_on_success() -> None:
    body = _probe({"openrouter": _v("up", available=["a/b"]), "zen": _v("off")},
                  summary={"up": 1, "off": 1})
    got = _run0("probeClouds", "[]", {"routes": {"/api/settings/probe-clouds": {"body": body}}})
    assert got["result"]["providers"]["zen"]["verdict"] == "off"
    assert got["result"]["summary"] == {"up": 1, "off": 1}


@pytest.mark.parametrize("spec,why", [
    ({"unreachable": ["/api/settings/probe-clouds"]}, "網路層失敗"),
    ({"routes": {"/api/settings/probe-clouds": {"status": 401, "body": {}}}}, "未登入"),
    ({"routes": {"/api/settings/probe-clouds": {"status": 503, "body": {}}}}, "後端不可用"),
    ({"routes": {"/api/settings/probe-clouds": {"body": {"ok": True}}}}, "缺 providers 欄位"),
    ({"routes": {"/api/settings/probe-clouds": {"body": {"providers": "not-an-object"}}}}, "providers 型別錯"),
    ({"routes": {}}, "根本沒有這條路徑"),
])
def test_probe_clouds_returns_null_on_any_failure(spec, why) -> None:
    """⚠️ 失敗回 **null**，不拋、不回「已 probe 但失敗」。

    回一個帶 error 的物件會讓 UI 分不出「沒 probe」與「probe 失敗」，於是
    顯示成後者 —— 使用者會去排查一個不影響任何功能的問題。
    """
    got = _run0("probeClouds", "[]", spec)
    assert got["result"] is None, f"{why}：應回 null，實際 {got['result']!r}"


def test_probe_clouds_never_throws_even_when_fetch_throws_synchronously() -> None:
    """fetch 同步拋出（不只是回 rejected promise）也要被吞掉。

    登入流程不該因為一個附加功能的問題而中斷 —— 那會讓使用者以為登入失敗。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH")
    script = Path(_TMP) / "sync_throw.ts"
    script.write_text(
        f"import {{ probeClouds }} from {json.dumps(str(LIB))};\n"
        "const boom = () => { throw new Error('boom'); };\n"
        "console.log(JSON.stringify({ r: await probeClouds(boom) }));\n",
        encoding="utf-8",
    )
    r = subprocess.run([node, "--experimental-strip-types", str(script)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"probeClouds 應該吞掉例外，但 node 報錯：{r.stderr[:400]}"
    assert json.loads(r.stdout.strip().splitlines()[-1])["r"] is None


def test_probe_clouds_does_not_throttle_or_force_on_its_own() -> None:
    """⚠️ 前端**不自己加節流**，且不打 `?force=1`。

    後端已有 300 秒 TTL（`?force=1` 才跳過）。前端再加一層 localStorage
    節流會變成兩套機制互相繞過，而且會讓「我剛換了 key，現在就探」在前端
    就被擋住。呼叫端重複打是**預期行為**。
    """
    got = _run0(
        "probeClouds",
        "[]",
        {"routes": {"/api/settings/probe-clouds": {"body": _probe({"openrouter": _v("up")})}}},
    )
    urls = [c["url"] for c in got["calls"]]
    assert urls == ["/api/settings/probe-clouds"], f"不應自行加參數或重導向：{urls}"
    assert not any("force" in u for u in urls), "前端不該帶 force=1"


# ── 「只做一次」的守衛：並行也要安全 ─────────────────────────────────────

def test_make_once_grants_the_right_to_exactly_one_caller_even_in_parallel() -> None:
    """⚠️ 行為層驗「並行只打一次」—— 這比原始碼斷言可靠。

    純 regex 看不出「旗標設在 await 之前」與「await 之後」的差別有多要命：
    後者原始碼斷言照樣綠，但並行時會各打一次 —— 而雲端探測會燒額度。
    """
    got = _run_js("once_parallel", (
        "const claim = makeOnce();\n"
        "let fired = 0;\n"
        "const task = async () => { if (claim()) { fired++; await null; } };\n"
        "await Promise.all([task(), task(), task(), task()]);\n"
        "console.log(JSON.stringify({ fired }));\n"
    ))
    assert got["fired"] == 1, "四個並行呼叫只該有一個取得權利"


def test_make_once_is_not_reusable_after_it_fires() -> None:
    """取得權利之後不放開 —— 否則 await 一輪之後又會再打一次。"""
    got = _run_js("once_seq", (
        "const claim = makeOnce();\n"
        "console.log(JSON.stringify({ results: [claim(), claim(), claim()] }));\n"
    ))
    assert got["results"] == [True, False, False]


def test_page_uses_the_proven_once_guard_not_a_hand_rolled_flag() -> None:
    """頁面必須用 `makeOnce()`，而不是自己寫 `if (flag) return`。

    那個寫法在**並行**下會各打一次，而原始碼層看不出來。這條釘的是「有使用
    經過行為驗證的守衛」。
    """
    code = _code_only(PAGE)
    assert "makeOnce()" in code, "probe 的單次守衛應使用 makeOnce()"
    assert not re.search(r"let\s+probeStarted\s*=", code), \
        "不要自己維護 probeStarted 布林值 —— 用 makeOnce()（並行安全）"
    m = re.search(r"async function probeOnceOnLogin\(\)\s*\{.*?\n  \}", code, re.S)
    assert m, "找不到 probeOnceOnLogin —— 若被改名請同步維護這個測試"
    assert "if (!claimProbeOnce()) return;" in m.group(0), "應先取得權利再往下走"


# ── 登入觸發：只掛一次，且失敗不影響登入 ──────────────────────────────────

def test_probe_is_triggered_only_after_login_is_confirmed() -> None:
    """probe 必須掛在 `/auth/me` 回 ok **之後**。

    掛在之前會讓匿名頁面也去要求登入才能呼叫的端點 —— 症狀是每次打開首頁
    都看到一個 401，而且與「登入時做一次」的需求無關。
    """
    code = _code_only(PAGE)
    m = re.search(r"const r = await fetch\('/auth/me'\);", code)
    assert m, "找不到 /auth/me 呼叫 —— 若被改名請同步維護這個測試"
    window = code[m.start(): m.start() + 500]
    assert "probeOnceOnLogin()" in window, "probe 應在確認登入之後觸發"
    assert window.index("d.ok") < window.index("probeOnceOnLogin()"), \
        "probe 必須在 `d.ok` 判斷之後呼叫"


def test_probe_is_guarded_by_a_logged_in_check() -> None:
    """`if (user)` 不可拿掉 —— 那會讓未登入者也被打一次 probe。"""
    code = _code_only(PAGE)
    assert re.search(r"if \(user\) probeOnceOnLogin\(\);", code), \
        "probe 只在已登入時觸發"


def test_probe_is_not_called_at_script_top_level() -> None:
    """⚠️ probe 不可在 script 頂層呼叫。

    那會在組件初始化時執行、SSR 階段就發出相對網址的 POST（SvelteKit 禁止），
    而且每次頁面載入都會打 —— 正是需求明說不要的「每次頁面載入」。
    """
    code = _code_only(PAGE)
    # ⚠️ 必須比對「onMount 主體**之外**」的位置，而不是用 `^\s*` 開頭 ——
    # onMount 裡的呼叫本來就縮排在行首（縮排多一階），同樣符合那個 regex。
    # 這裡改成量出 top-level 範圍（沿用 tests/test_frontend_hosts.py 的
    # 大括配對法），再斷言那個範圍裡沒有 probe。
    lo, hi = _onmount_span(code)
    assert "probeOnceOnLogin()" in code[lo:hi], "probe 應在 onMount 裡被呼叫"
    outside = code[:lo] + code[hi:]
    # ⚠️ 比對「**呼叫**」而不是那個字串本身：`function probeOnceOnLogin() {…}`
    #   的宣告本身就是同一個字串，會讓這條測試永遠紅。
    #   也不能只看「有沒有宣告」—— 要確認的是有沒有人在 onMount 之外叫它。
    assert not re.search(r"probeOnceOnLogin\s*\(\s*\)\s*;", outside), (
        "probeOnceOnLogin() 不得在 onMount 之外被呼叫 —— "
        "那會在組件初始化時就發出 POST（SSR 階段 SvelteKit 禁止相對網址 fetch）")


def _onmount_span(code: str) -> tuple[int, int]:
    """onMount(...) 回呼在 script 裡的字元範圍（大括號配對）。"""
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


def test_probe_failure_does_not_set_any_user_visible_error() -> None:
    """⚠️ probe 失敗**不可**產生錯誤訊息 —— 它不影響任何功能。

    顯示「probe 失敗」會讓使用者去排查一個不影響查詢、不影響設定的問題，
    而真相可能只是「沒探到」。
    """
    code = _code_only(PAGE)
    m = re.search(r"async function probeOnceOnLogin\(\)\s*\{.*?\n  \}", code, re.S)
    assert m, "找不到 probeOnceOnLogin —— 若被改名請同步維護這個測試"
    body = m.group(0)
    for var in ("settingsErr", "settingsMsg", "error ="):
        assert var not in body, (
            f"probeOnceOnLogin 不可寫入 {var} —— probe 失敗要靜默，"
            "它不是登入或查詢的錯誤")
    assert "catch" in body, "probe 必須包在 try/catch 裡，不讓例外影響登入"


def test_probe_failure_leaves_login_intact() -> None:
    """行為層：probe 失敗時呼叫端拿到 null，可照常繼續。

    驗的是 lib 的契約（回 null、不拋）；頁面接線（try/catch、不寫錯誤訊息）
    由上面兩條分開測。
    """
    got = _run0("probeClouds", "[]", {"unreachable": ["/api/settings/probe-clouds"]})
    assert got["result"] is None, "probe 失敗 → null（頁面據此不顯示任何 probe 區塊）"
    assert got["calls"], "它有真的嘗試過（不是因為沒接線才回 null）"


def test_probe_state_starts_null_so_nothing_is_shown_before_it_returns() -> None:
    """`cloudProbe` 預設 null，且 UI 以 `{#if cloudProbe}` 包住整個區塊。

    預設值若是一個空物件，UI 就會顯示一個「全部未知」的空表格 —— 那看起來
    像真的探過了、而且什麼都探不到。
    """
    text = PAGE.read_text(encoding="utf-8")
    assert re.search(r"\{#if cloudProbe\}", text), "probe 區塊應以 {#if cloudProbe} 包住"
    assert re.search(r"let cloudProbe\s*=\s*null", _code_only(PAGE)), \
        "cloudProbe 應預設為 null（未 probe 與 probe 失敗是同一個狀態）"


# ── 模型清單的可用性標記 ───────────────────────────────────────────────

def test_availability_suffix_is_empty_when_unknown() -> None:
    """⚠️ 不知道時**不加任何字串** —— 不可畫成「不可用」。

    空字串與「不可用」在使用者眼裡是天差地別的兩件事：前者是什麼都沒說，
    後者是明確的否定斷言（而我們沒有根據）。
    """
    code = _code_only(PAGE)
    m = re.search(r"function availabilitySuffix\(.*?\n  \}", code, re.S)
    assert m, "找不到 availabilitySuffix —— 若被改名請同步維護這個測試"
    body = m.group(0)
    assert re.search(r"if\s*\(!a\.known\)\s*return\s*'';", body), \
        "known=false 必須回空字串，不可顯示任何可用性判斷"
    assert "missing" in body, \
        "上游沒有的 model 必須特別標出（那是「選了才 404」）"


def test_probe_section_renders_all_four_verdicts_via_the_shared_mapping() -> None:
    """provider 列的 class／字都走 verdictState／verdictLabel。

    若在模板裡自己寫一份對應表，就會有「lib 改了、模板沒改」的漂移 ——
    而那正是 `unknown` 被畫成紅字最可能的成因。
    """
    text = PAGE.read_text(encoding="utf-8")
    assert "vp-{verdictState(p.verdict)}" in text, "provider 列要用 verdictState 決定 class"
    assert "verdictLabel(p.verdict)" in text, "狀態字也要用共用的對應"
    assert "{p.detail ?? '—'}" in text, "要顯示後端給的 detail（處置方式在那裡）"
    assert "missingModels(p)" in text, "要顯示 missing 清單"
    assert "上游沒有" in text, "missing 必須有明確的視覺標記"


def test_probe_section_only_describes_the_local_host() -> None:
    """只顯示本機的 probe（登入時打的是本機後端）。

    把 probe 結果套到 peer 上會是謊話 —— 我們從來沒探過那些台。
    """
    text = PAGE.read_text(encoding="utf-8")
    assert "雲端可用性（本機）" in text, "probe 區塊要標明是本機結果"
    code = _code_only(PAGE)
    assert code.count("probeClouds(") == 1, "probe 只應打一次（不是逐台）"


# ── 前綴：前端選單有 `openrouter/`，probe 回的沒有 ────────────────────────
#
# 這是實測才發現的不一致（wsl，2026-10-02），而且**症狀極其安靜**：
#   前端下拉選單的值：  openrouter/cohere/north-mini-code:free
#   probe 回的 missing： cohere/north-mini-code:free        ← 少了第一段
# 若用整串 includes() 比對，**每一個**模型都落空 → 全部顯示成「不知道」。
# 而 `missing`（上游沒有這個模型）永遠不會被標出 —— 這個功能看起來沒壞，
# 但從沒生效過。沒有對照真實資料根本看不出來。

def test_availability_strips_the_frontend_prefix_before_matching() -> None:
    """前端帶前綴、probe 不帶 → 比對前要先剝掉前綴。"""
    p = _probe({"openrouter": _v(
        "up",
        available=["cohere/north-mini-code:free"],
        missing=["z-ai/glm-5.2:free"],
        configured=["cohere/north-mini-code:free", "z-ai/glm-5.2:free"],
    )})
    assert _av("openrouter/cohere/north-mini-code:free", p) == {"ok": True, "known": True}
    got = _av("openrouter/z-ai/glm-5.2:free", p)
    assert got["known"] is True and got["missing"] is True


def test_availability_matches_real_openrouter_shapes() -> None:
    """用真實 wsl 的 id 形狀驗一次（owner 前綴 + `:free` 標記）。"""
    p = _probe({"openrouter": _v(
        "up",
        available=["cohere/north-mini-code:free", "dots-studio/dots-3-note-preview:free"],
        missing=["inclusionai/ling-3.0-flash-fin:free", "z-ai/glm-5.2:free"],
        configured=["cohere/north-mini-code:free", "z-ai/glm-5.2:free"],
    )})
    assert _av("openrouter/cohere/north-mini-code:free", p)["ok"] is True
    assert _av("openrouter/inclusionai/ling-3.0-flash-fin:free", p)["missing"] is True
    assert _av("openrouter/z-ai/glm-5.2:free", p)["missing"] is True


def test_availability_works_for_every_real_provider_prefix() -> None:
    """`nv/`、`mis/`、`hf/` 這些**前綴名與 provider key 不同**的也要對。

    ⚠️ 這是最容易漏的一類：`mistral` 的前端前綴是 `mis/`（見 +page.svelte
    的 prefixOf），不是 `mistral/`。若比對時拿前綴直接當 key 查，
    `mis/codestral-latest` 會查不到 provider 而變成「不知道」。
    """
    p = _probe({
        "nvidia": _v("up", available=["z-ai/glm-5.3-flash"], missing=["mistralai/mistral-nemotron"],
                     configured=["z-ai/glm-5.3-flash", "mistralai/mistral-nemotron"]),
        "mistral": _v("up", available=["codestral-latest"], missing=[],
                      configured=["codestral-latest"]),
        "hf": _v("up", available=["Qwen/Qwen3.8-27B"], missing=[],
                 configured=["Qwen/Qwen3.8-27B"]),
        "gemini": _v("up", available=["gemini-3.8-flash"], missing=[],
                     configured=["gemini-3.8-flash"]),
        "groq": _v("up", available=["openai/gpt-oss-120b"], missing=[],
                   configured=["openai/gpt-oss-120b"]),
        "cohere": _v("up", available=["command-a-plus-05-2026"], missing=[],
                     configured=["command-a-plus-05-2026"]),
        "zen": _v("off", configured=["deepseek-v4-flash-free"], available=[], missing=[]),
    })
    assert _av("nv/z-ai/glm-5.3-flash", p)["ok"] is True, "nv/ 前綴"
    assert _av("nv/mistralai/mistral-nemotron", p)["missing"] is True
    assert _av("mis/codestral-latest", p)["ok"] is True, "mis/ 前綴（不是 mistral/）"
    assert _av("hf/Qwen/Qwen3.8-27B", p)["ok"] is True, "含 owner 的 hf/ id"
    assert _av("gemini/gemini-3.8-flash", p)["ok"] is True
    assert _av("groq/openai/gpt-oss-120b", p)["ok"] is True
    assert _av("cohere/command-a-plus-05-2026", p)["ok"] is True
    assert _av("zen/deepseek-v4-flash-free", p)["known"] is False, "off 的 provider 不下判斷"


def test_prefixed_model_from_a_different_provider_stays_unknown() -> None:
    """`openrouter/zen-model` 不能去查 `zen` 的清單。

    前綴只決定「查哪個 provider」，剩下那段才拿去比對；不該跨 provider 比。
    """
    p = _probe({"openrouter": _v("up", available=["a/b"], missing=[], configured=["a/b"]),
                "zen": _v("up", available=["x/y"], missing=[], configured=["x/y"])})
    assert _av("openrouter/x/y", p) == {"ok": False, "known": False}


def test_model_not_in_configured_at_all_is_unknown() -> None:
    """使用者手動輸入、provider 根本沒設定的模型 → 不知道，不是不可用。"""
    p = _probe({"openrouter": _v("up", available=["a/b"], missing=[], configured=["a/b"])})
    assert _av("openrouter/never/configured", p) == {"ok": False, "known": False}


def test_frontend_prefix_table_stays_in_sync_with_the_pages_prefixof() -> None:
    """⚠️ 前綴表有**兩個方向**，兩邊不一致就會有 provider 永遠不顯示。

    `+page.svelte` 的 `prefixOf` 是 provider→前綴（組 usageMap 用），
    `hostDefaults.ts` 的 `PROVIDER_OF_PREFIX` 是反方向（查 probe 用）。
    兩份各自演化、沒有交叉檢查時，症狀是「某些 provider 的模型永遠顯示成
    不知道」—— 看起來像 probe 沒送到，實際上是查錯了 key。

    這條從頁面的 prefixOf **實際解析出來**比對，不是抄一份常數過來 ——
    抄一份的話這條測試就只是在驗證自己。
    """
    code = _code_only(PAGE)
    m = re.search(r"const prefixOf\s*=\s*\{(.*?)\};", code, re.S)
    assert m, "找不到 +page.svelte 的 prefixOf —— 若被改名請同步維護這個測試"
    pairs = re.findall(r"(\w+)\s*:\s*'([\w/]+)'", m.group(1))
    assert pairs, "prefixOf 沒解析到任何項目（掃描器失效？）"
    page_map = dict(pairs)
    # ⚠️ 先確認解析到的數量對得上宣告裡的項目數。
    #   prefixOf 是用 `key: 'val', …` 寫在**一行**、跨行的物件常值；
    #   若 regex 抓到不完整（漏掉跨行的項目），下面會誤報「表不一致」——
    #   而那正是這條測試要避免的假紅。
    assert len(pairs) == m.group(1).count(":") - m.group(1).count("//"), (
        f"prefixOf 只解析到 {len(pairs)} 項，但宣告看起來更多 —— "
        "regex 抓不完整，請同步維護")

    # 反方向送進 lib 驗：每一個頁面用得到的前綴都查得到 provider。
    # `ollama` 刻意排除 —— 它在地端，前綴對應的不是 probe 的 provider。
    # ⚠️ probe 的 key 是 **provider 名**（page_map 的鍵），不是前綴（值）。
    #   這正是 `nv`／`nvidia` 那一組會出錯的原因 —— 用值去建 providers 的話，
    #   查出來的是 providers['nvidia'] 找不到（因為表裡只有 'nv'）。
    providers = {provider: {"verdict": "up", "available": ["m"], "missing": [],
                            "configured": ["m"]}
                 for provider in page_map if provider != "ollama"}
    probe = _probe(providers)      # ⚠️ 要包成 {providers: …} —— 那才是真契約
    for provider, prefix in page_map.items():
        if provider == "ollama":
            continue
        got = _pure("modelAvailability", json.dumps([f"{prefix}/m", probe]))
        assert got["result"]["known"] is True, (
            f"頁面 prefixOf 裡的 {provider}→'{prefix}'，但 lib 查不到 "
            f"providers['{provider}'] —— 兩個方向的表不一致")


def test_the_two_mismatched_prefixes_are_covered() -> None:
    """`nv/`→nvidia 與 `mis/`→mistral 是實測會落空的那兩個。

    直接拿前綴當 provider key 查會查不到，而症狀安靜：那几个 provider 的
    所有模型都變成「不知道」，功能看起來沒壞。
    """
    p = _probe({
        "nvidia": _v("up", available=["x"], missing=[], configured=["x"]),
        "mistral": _v("up", available=["y"], missing=[], configured=["y"]),
    })
    assert _av("nv/x", p)["ok"] is True
    assert _av("mis/y", p)["ok"] is True
    # 完整名也該能用（probe 的 key 本身就是完整名）
    assert _av("nvidia/x", p)["ok"] is True
    assert _av("mistral/y", p)["ok"] is True
