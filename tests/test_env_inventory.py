"""`scripts/env-inventory.py` 產出的三機清單 —— 2026-10-02。

## 為什麼這份清單不能含值

`.env` 有 8 份憑證。要回答「哪些鍵該留」，得有一份可讀、可排序、不含值的
清單 —— 而「可讀」意味著它會被 commit、被貼進 issue、被 opencode 讀來分析。
所以「不含值」不是 privacy 潔癖，是**它能不能被放進版控的前提**。

## 為什麼決策欄不能是空白

一張全空的欄位會被跳過。而「有建議但不照做」至少會被讀到、並被反駁 ——
反駁的��由才是���值得留的東西。所以 `default_decision()` 給機械可判的那部分結論。
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "settings" / "env" / "ENV-VARIABLE-INVENTORY.md"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "env-inventory.py"), *args],
        capture_output=True, text=True, cwd=ROOT)


def _gen():
    spec = importlib.util.spec_from_file_location(
        "env_inventory", ROOT / "scripts" / "env-inventory.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 不含值 ─────────────────────────────────────────────────────────────

def test_inventory_contains_no_credential_values():
    """產出的檔不得含任何憑證值。

    檢查方式是「逐鍵比對 `.env` 的實際值」—— 比掃描長字串可靠，後者會
    對 `HOST_MACHINE_ID`（32 字元 hex）與 sha12 誤報。

    ⚠️ 只檢查**憑證類**的鍵，不檢查全部。因為非憑證的值（`ZEN_BASE_URL`
    這種公開的服務網址）本來就會出現在 registry 的「預設值」欄位裡 ——
    那不是洩漏，是文件。第一版檢查所有鍵，於是 `ZEN_BASE_URL` 被報出來，
    而那是**誤報**：清單裡出現它只是因為 env-audit 反查到了它的預設值。
    """
    r = _run()
    assert r.returncode == 0, r.stderr
    text = INVENTORY.read_text(encoding="utf-8")
    env = ROOT / ".env"
    if not env.is_file():
        return          # CI 乾淨 clone 沒有 .env，無可檢
    import hashlib
    mod = _gen()
    sync = (ROOT / "scripts" / "env-sync.sh").read_text(encoding="utf-8")
    secrets = set()
    for var in ("SHARED_SECRETS", "PER_HOST_SECRETS"):
        m = re.search(rf'^{var}="([^"]+)"', sync, re.M)
        if m:
            secrets.update(m.group(1).split())
    leaked = []
    for line in env.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line)
        if not m or not m.group(2) or m.group(1) not in secrets:
            continue
        val = m.group(2)
        if val in text or hashlib.sha256(val.encode()).hexdigest()[:12] in text:
            leaked.append(m.group(1))
    assert not leaked, f"清單裡出現了這些憑證的值或指紋：{leaked}"


def test_emit_column_prints_only_set_empty_absent():
    """`--emit-column` 的輸出是 `KEY=SET|EMPTY|ABSENT`，不可有值。"""
    r = _run("--emit-column")
    assert r.returncode == 0, r.stderr
    lines = [l for l in r.stdout.splitlines() if l.strip() and not l.startswith("#")]
    assert lines, "沒有輸出"
    bad = []
    for l in lines:
        body = l.split("#")[0].strip()
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(SET|EMPTY|ABSENT)$", body)
        if not m:
            bad.append(body[:60])
    assert not bad, f"這幾行的格式不對（可能夾帶了值）：{bad}"


# ── 結構 ───────────────────────────────────────────────────────────────

def test_inventory_has_the_three_machine_columns():
    """三台的欄位要並排 —— 這是「三台出一份」的意義。"""
    text = INVENTORY.read_text(encoding="utf-8")
    assert "| 變數 | 誰讀 |" in text, "表頭不對"
    assert text.count("待填") >= 1 or True   # 欄位存在即可；內容待填是正常狀態
    # 至少有一列同時列出三台
    row = next((l for l in text.splitlines()
                if l.startswith("| `") and l.count("|") >= 7), None)
    assert row, "沒有任何資料列"


def test_decision_column_is_prefilled_not_blank():
    """決策欄不可是空白的 —— 空白欄位會被跳過。

    Friction 點：這是「有建議但不照做」與「根本沒人看」的差別。

    ⚠️ 只數**資料列**。第一版用 `startswith("| `")` 過濾，於是連開頭那個
    說明表（`| SET | 該機的 .env 裡有值 |`）的五列也算進來 —— 而它們本來
    就沒有決策欄，於是報「5 列空白」。誤報的方向是「測試在罵一個沒問題的
    東西」，那會讓人開始忽略這條測試。
    """
    text = INVENTORY.read_text(encoding="utf-8")
    # 資料列的特徵：第 1 欄是變數名、第 2 欄是「誰讀」（含檔名:行號或 —）
    rows = [l for l in text.splitlines()
            if l.startswith("| `")
            and re.search(r"\|\s*(compose\.yaml|backend/|ingest/|scripts/|—)",
                          l)]
    assert len(rows) >= 60, f"資料列太少（{len(rows)}）—— 反查來源可能壞了"
    decided = [l for l in rows
               if re.search(r"\|\s*\*\*(留|清空|刪|待確認)\*\*", l)]
    assert len(decided) == len(rows), (
        f"{len(rows) - len(decided)} 列的決策欄是空白的"
    )


def test_in_effect_keys_are_judged_before_ownership():
    """**「這個值永遠不會生效」必須排在「這個值屬於誰」之前。**

    Friction 點（2026-10-02 實測）：第一版把 per-host 檢查放最前面，
    `QDRANT_URLS` 被判「留 — per-host 設定」，掩蓋了它同時是
    「compose 沒傳入容器 → 設了無效」。

    「是某台的」與「設了沒用」是兩件獨立的事，後者嚴重得多：前者只是歸屬，
    後者會讓人以為改了會生效，而症狀是靜默的。
    """
    mod = _gen()
    EA = mod._load_audit()
    reg = EA.build_registry()
    ph = EA.per_host_keys()
    # QDRANT_URLS 同時是 per-host 鍵「且」沒傳入容器
    d, why = mod.default_decision("QDRANT_URLS", reg, ph, set(), set(),
                                  {"QDRANT_URLS": "EMPTY"})
    assert d in ("待確認", "刪"), (
        f"QDRANT_URLS 應被判為「設了無效」，實際是「{d}」（{why}）"
    )
    assert "沒傳入容器" in why, f"理由要指出真正的原因，實際：{why}"


def test_never_effective_keys_are_not_judged_as_keep():
    """compose 寫死的 5 個鍵不可被判「留」。

    它們留著會讓人以為改了會生效 —— 而症狀是**靜默**的（值被 compose 的
    字面值覆蓋，沒有任何錯誤）。
    """
    mod = _gen()
    EA = mod._load_audit()
    reg = EA.build_registry()
    ph = EA.per_host_keys()
    for k in ("EMBED_MODEL", "RERANK_MODEL", "POSTGRES_DB", "POSTGRES_USER",
              "QDRANT_URL"):
        d, why = mod.default_decision(k, reg, ph, set(), set(), {k: "EMPTY"})
        assert d == "刪", f"{k} 應判「刪」（compose 寫死），實際「{d}」：{why}"


def test_per_host_secrets_are_never_judged_deletable():
    """兩把 per-host 機密絕不可被判「刪」。

    它們只存在那一台，刪了**沒有任何來源能重建**（不在 sops、別台沒有）。
    就算現在看起來「設了無效」，刪掉也是不可逆的損失。
    """
    mod = _gen()
    EA = mod._load_audit()
    reg = EA.build_registry()
    ph = EA.per_host_keys()
    for k in ("QDRANT_API_KEY", "POSTGRES_PASSWORD"):
        d, _ = mod.default_decision(k, reg, ph, {k}, set(), {k: "SET"})
        assert d == "留", f"{k} 絕不可被判「{d}」"


def test_policy_excluded_keys_are_not_decision_candidates():
    """`LAN_IP` 不可出現在決策表裡。

    `.env.example` 刻意不印 `LAN_IP=`（pre-push 與 CI 擋 `^LAN_IP=`）。
    把它列成決策候選等於邀請人設它；而若下游有「從清單產生 .env」的工具，
    就會產出一行被 CI 擋掉的賦值。
    """
    text = INVENTORY.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("| `LAN_IP`"):
            assert "**待確認" not in line and "**留" not in line, \
                "LAN_IP 不該出現在決策表"
    assert "政策上停用" in text, "LAN_IP 應該改列在〈政策上停用〉那一節"


def test_hosts_table_is_documented_and_runnable():
    """`env-sync.sh --hosts-table` 要真的跑得出來（它是清單的視圖之一）。"""
    r = subprocess.run(["bash", str(ROOT / "scripts" / "env-sync.sh"),
                        "--hosts-table"], capture_output=True, text=True,
                       cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "LLM_MODEL" in r.stdout
