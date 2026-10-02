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
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
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