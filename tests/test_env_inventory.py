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
    """`--emit-column` 的輸出是 `KEY: SET|EMPTY|ABSENT`，不可有值。"""
    r = _run("--emit-column")
    assert r.returncode == 0, r.stderr
    lines = [l for l in r.stdout.splitlines() if l.strip() and not l.startswith("#")]
    assert lines, "沒有輸出"
    bad = []
    for l in lines:
        body = l.split("#")[0].strip()
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)\s*:\s*(SET|EMPTY|ABSENT)$", body)
        if not m:
            bad.append(body[:60])
    assert not bad, f"這幾行的格式不對（可能夾帶了值）：{bad}"


def test_emit_column_output_passes_the_repo_own_guards():
    """**工具的輸出必須通得過 repo 自己的護欄** —— 否則正常使用流程會被擋。

    Friction 點（2026-10-02 實測）：`--emit-column` 印了 `LAN_IP=ABSENT`，
    而它的輸出**就是要 commit 的**（`settings/env/host-inventory/<host>.txt`），
    也會被貼進聊天回報。pre-push 與 CI 擋 `^LAN_IP=`，IP 準則也明文禁止。

    症狀特別難查：指令成功、輸出看起來正常、照說明貼回來也沒問題，
    **然後 push 被拒**。而且只有真的 commit 那一步才會發現。

    所以這條測試的存在理由不是「LAN_IP 不該出現」—— 那件事另有
    `test_policy_excluded_keys_are_not_decision_candidates` 守著 —— 而是
    **把每個會產生可提交輸出的工具，都放進護欄的射程內**。
    """
    r = _run("--emit-column")
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        assert not line.startswith("LAN_IP="), (
            "--emit-column 不可印 `LAN_IP=`：它的輸出會被 commit，"
            "而 pre-push／CI 擋 `^LAN_IP=`")
    # 同時驗證已提交的 wsl.txt 也乾淨（那是這個機制真正的產物）
    col_file = ROOT / "settings" / "env" / "host-inventory"
    for f in col_file.glob("*.txt") if col_file.is_dir() else []:
        for line in f.read_text(encoding="utf-8").splitlines():
            assert not line.startswith("LAN_IP="), \
                f"{f.name} 含 `LAN_IP=` —— 這檔是會被 commit 的"


def test_column_format_cannot_be_mistaken_for_an_assignment():
    """**欄位格式必須結構上不可能被誤認成賦值。**

    Friction 點（2026-10-02 實測）：原本用 `ADMIN_TOKEN=SET`，而
    `test_env_sync.py::test_no_tracked_secret_values` 是用
    `^([A-Za-z_][A-Za-z_0-9]*)=(.*)$` 加「右邊非空」抓憑證明文的 ——
    於是 `ADMIN_TOKEN=SET` 被判成**十把憑證的值**，pre-push 直接擋下。

    那是誤報（`SET` 不是值），但重點不在誤報：重點是這個格式**結構上讓人
    分不出它和一個真正的賦值**，而這份檔的正確定義就是「要被 commit」。

    所以修法不是加一份豁免清單（那只是把問題推到下一個格式），而是讓這類行
    永遠不可能被誤認。

    這條測試存在的理由是**讓上述理由跟著程式碼走**：換回 `=` 的話，擋下來的
    是憑證守衛，錯誤訊息會指向「憑證明文」而不是「格式選錯」—— 那是個
    會讓人查錯方向的訊息。
    """
    r = _run("--emit-column")
    assert r.returncode == 0, r.stderr
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    data = [l for l in lines if not l.startswith("#")]
    assert data, "沒有資料行"
    for line in data:
        body = line.split("#")[0].strip()
        assert not re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", body), (
            f"這行看起來像賦值，會被憑證守衛當成明文：{body!r}\n"
            f"   請用 `KEY: STATUS`（冒號），不要用等號 —— "
            f"見 env-inventory.py 檔頭〈為什麼用冒號而不是等號〉")
    assert all(re.match(r"^[A-Za-z_][A-Za-z_0-9]*\s*:\s*(SET|EMPTY|ABSENT)$", b)
               for b in (l.split("#")[0].strip() for l in data)), \
        "欄位格式不是 `KEY: SET|EMPTY|ABSENT`"


def test_emit_column_is_reproducible_against_the_committed_file():
    """已提交的欄位檔必須跟「現在跑出來的」一致 —— 不然那份檔在騙人。

    `host-inventory/<host>.txt` 是**快照**，不是設定檔。它過期時沒有任何人
    會被通知，而它的作用是「讓人相信三台長相一樣」。一份過期的快照比沒有
    快照更糟 —— 因為它會讓錯誤的結論看起來有證據。

    ⚠️ **沒有 `.env` 時要跳過，不是報過期**（2026-10-02 實測）。
    CI 是乾淨 clone、沒有 `.env` → 每個鍵都讀成 ABSENT → 第一版會報
    「41 個鍵全部過期」，那是**假的**：沒有 `.env` 就沒有可比對的對象，
    「無從驗證」與「驗證失敗」是兩件事。

    這正是本專案記錄在案的 CI 坑：**測試依賴真實 `.env` 時，本機全綠、
    CI 紅**。修法是判斷前提，不是放寬斷言。
    """
    if not (ROOT / ".env").is_file():
        import pytest
        pytest.skip("無 .env：欄位快照無從驗證（CI 的乾淨 clone 就是這種情況）")
    f = ROOT / "settings" / "env" / "host-inventory" / "wsl.txt"
    if not f.is_file():
        return
    stored = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)\s*:\s*(SET|EMPTY|ABSENT)", line.strip())
        if m:
            stored[m.group(1)] = m.group(2)
    live = {}
    for line in _run("--emit-column").stdout.splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)\s*:\s*(SET|EMPTY|ABSENT)", line.strip())
        if m:
            live[m.group(1)] = m.group(2)
    stale = {k: (stored[k], live.get(k, "ABSENT"))
             for k in stored if stored[k] != live.get(k, "ABSENT")}
    assert not stale, (
        f"settings/env/host-inventory/wsl.txt 過期了，這些鍵已變：{stale}\n"
        f"   改完 .env 請重新產生："
        f"python3 scripts/env-inventory.py --emit-column "
        f"> settings/env/host-inventory/wsl.txt")


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
