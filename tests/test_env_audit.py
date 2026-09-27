"""鎖住 env-audit 的反查正確性。

為什麼需要這些測試（2026-09-26）：env-audit.py 的 docstring 原本寫著
「靠 scripts/env-audit.sh 從程式碼反查更新」—— 那個檔案從來不存在，
`KNOWN` 表其實是手工維護的，而且已經爛掉：漏掉 44 個變數、把 3 個
「程式有讀」的標成幽靈、還宣稱「程式會自動去掉機台前綴」（從未實作）。

**根本原因是沒有任何測試驗證這些宣稱。** 改寫後若不加測試，新的 docstring
只是另一段沒有東西驗證的話 —— 同一個失敗模式會再發生一次。

所以這裡鎖住的全是「我手動交叉比對時抓到過的 bug」，不是理想的抽樣。
每條都有實測依據，見各 docstring。
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "env-audit.py"


def _load():
    spec = importlib.util.spec_from_file_location("env_audit", AUDIT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ea = _load()
REG = ea.build_registry()


# ── 反查完整性 ──────────────────────────────────────────────

def test_compose_every_interpolation_is_captured():
    """compose 裡每個 ${VAR} 都必須被反查到。

    實測依據：改寫時用巢狀展開的 regex（要求配對 }）掃描，
    compose.yaml:36 的 ${POSTGRES_DSN:-postgresql://rag:${POSTGRES_PASSWORD:?...}}
    讓外層的 } 吃掉內層，POSTGRES_PASSWORD 漏掉，被誤報成幽靈變數。
    """
    out = subprocess.run(
        ["grep", "-oE", r"\$\{[A-Za-z_][A-Za-z_0-9]*", "compose.yaml"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    expected = {l.strip("${") for l in out.splitlines() if l.strip("${")}
    assert expected <= set(REG), f"漏抓: {sorted(expected - set(REG))}"


def test_no_test_or_venv_env_pollution():
    """掃描必須跳過 .venv 與 build/ 產物。

    實測依據：naive grep 從 backend/.venv 抓到 WEBSOCKETS_MAX_HEADERS、
    PYTEST_*、WEB_CONCURRENCY、PGSSL* 等數十個外部套件的環境變數，
    以及 backend/build/lib/app/rag.py 這個 build 產物副本造成的重複計數。
    污染會讓稽核產生比人工維護更糟的假訊號。
    """
    polluted = [n for n in REG if re.match(
        r"^(WEBSOCKETS_|WATCHFILES_|PYTEST_|WEB_CONCURRENCY|PGSSL|VIRTUAL_ENV)",
        n)]
    assert not polluted, f"掃到外部套件變數: {polluted}"
    readers = [r for ref in REG.values() for r in ref.readers]
    assert not any(".venv/" in r or "/build/" in r for r in readers), \
        "掃到 .venv 或 build/ 產物"


def test_shell_local_variables_are_excluded():
    """shell 腳本自己賦值的變數不該進 .env 清單。

    實測依據：law-update-worker.sh:67 是 `ROLE="source"; CMD="uv run ..."`，
    一行兩個賦值；只比對行首會漏掉 CMD，被誤判成 .env 該管的變數。
    """
    for name in ("CMD", "PEER_KEY", "AUTH_H", "ROOT", "SNAP_NAME", "LOG"):
        assert name not in REG, f"{name} 是 shell 局部變數，不該被當成 .env 變數"


def test_python_os_environ_indexing_does_not_crash():
    """os.environ["X"] 沒有第二個 regex group，不能直接 group(2)。

    實測依據：改寫時寫死 m.group(2)，而 PY_REFS 第三條 pattern 沒有 group(2)。
    目前程式碼剛好沒有人這樣寫，所以不會 crash —— 但只要有人加一行就炸。
    """
    line = 'X = os.environ["A_VAR_ONLY_IN_THIS_TEST"]'
    found = 0
    for pat in ea.PY_REFS:
        for m in pat.finditer(line):
            found += 1
            tail = m.group(2) if (m.lastindex or 0) >= 2 else None
            assert tail is None  # 無預設 → 代表必填
    assert found == 1, "os.environ['X'] 應被其中一條 pattern 命中"


# ── 分類正確性 ──────────────────────────────────────────────

def test_postgres_password_is_required_not_a_ghost():
    """POSTGRES_PASSWORD 必填，且絕不能被標成幽靈。

    實測依據：它只出現在 `${POSTGRES_PASSWORD:?...}`（compose:24）與
    巢狀展開的 DSN（compose:36），regex 一旦吃掉內層就會漏，audit 就會
    報「沒有任何程式讀」—— 而 compose 用 `:?` 缺值直接拒絕啟動。
    """
    ref = REG["POSTGRES_PASSWORD"]
    assert ref.required, "compose 用 ${...:?} 標記必填，不該是選填"
    assert not ref.superseded_by


def test_only_true_literals_are_marked_hardcoded():
    """只有純字面值才算被 compose 寫死。

    實測依據：初版對整個 environment: 區塊一律標寫死，造成約 45 個
    變數（ADMIN_TOKEN、HOST_ID、LLM_MODEL…）發出無效警告。
    實際只有 5 個是真字面值。
    """
    hardcoded = {n for n, r in REG.items() if r.compose_hardcoded}
    assert hardcoded == {"EMBED_MODEL", "RERANK_MODEL",
                         "POSTGRES_USER", "POSTGRES_DB", "QDRANT_URL"}, \
        f"寫死的變數變了: {sorted(hardcoded)}"


def test_embed_model_is_hardcoded_not_a_ghost():
    """EMBED_MODEL/RERANK_MODEL 被 compose 寫死，不是「無人讀」。

    實測依據：舊版 GHOSTS 表寫「程式讀不到，設了沒作用」—— 但
    backend/app/rag.py:38,42 有讀，ingest/laws/qdrant_load.py:26 也有讀。
    差別是 compose 用字面值覆蓋（:50,:51），所以對容器無效、對 host 端有效。
    「寫死」和「無人讀」是完全不同的處置方式，混為一談會誤導。
    """
    for name in ("EMBED_MODEL", "RERANK_MODEL"):
        assert REG[name].python_read, f"{name} 應該被 Python 讀取"
        assert REG[name].compose_hardcoded, f"{name} 應標成 compose 寫死"


def test_vars_read_but_not_forwarded_to_container():
    """只有「刻意不傳」的那幾個可以讀得到卻沒轉發（scope B，2026-09-27）。

    實測依據：本測試的舊版斷言 OLLAMA_MODELS 與 QDRANT_URLS 兩個都
    `not r.compose_ref`，那是在**記錄 bug** —— .env 設了 OLLAMA_MODELS
    [qwen3:14b, qwen3:14b, qwen3:8b]，但 compose.yaml 的 api environment
    完全沒列，容器只收到 LLM_MODEL=qwen3:8b，於是 _llm_model_for() 對三個
    URL 都回 8b，x570/mbp 沒有 8b 被 _ollama_probe 跳過 → MSI 永遠降級成
    自己的 8b，而且不報錯。

    scope B 已把 OLLAMA_MODELS 補進 compose，所以本測試改成只守 QDRANT_URLS
    那一個「刻意不傳」的（理由見 backend/DESIGN.md〈刻意不傳入容器的 3 個變數〉）。
    完整的 15 進 3 不進清單由下面那組 scope B 測試鎖住。
    """
    r = REG["QDRANT_URLS"]
    assert r.python_read, "QDRANT_URLS 應被 Python 讀取"
    assert not r.compose_ref, "QDRANT_URLS 刻意不傳入容器（快照同步模型）"
    assert not r.compose_hardcoded, "QDRANT_URLS 不該被標成寫死"


# ── scope B：15 個補上、3 個刻意不補 ───────────────────────

# (變數, 原始碼檔, 原始碼裡必須逐字存在的片段, compose 應有的預設值)
#
# 第四欄必須與第三欄原始碼的 `os.getenv` 第二個參數**逐字相同**
# （backend.md 不變量 #2）。不一致時 `.env` 沒設這項的那台會安靜地用錯的值，
# 而且沒有任何報錯。
#
# 為什麼要連原始碼片段一起鎖：只比預設值的話，有人改了 rag.py 的預設值
# 卻忘了改 compose，這裡的期望值會跟著測試一起「對齊舊值」而全部通過 ——
# 測試就變成沒有約束力。鎖住原始碼那一行才抓得到。
SCOPE_B_FORWARDED = [
    ("CF_AIG_GATEWAY_ID", "backend/app/rag.py",
     'os.getenv("CF_AIG_GATEWAY_ID", "cloudflaregateway")', "cloudflaregateway"),
    ("HOST_API_MBP", "backend/app/rag.py",
     'os.getenv("HOST_API_MBP", "https://api-mbp.ragdemo.win")',
     "https://api-mbp.ragdemo.win"),
    ("HOST_API_MSI", "backend/app/rag.py",
     'os.getenv("HOST_API_MSI", "https://api-msi.ragdemo.win")',
     "https://api-msi.ragdemo.win"),
    ("HOST_API_X570", "backend/app/rag.py",
     'os.getenv("HOST_API_X570", "https://api-x570.ragdemo.win")',
     "https://api-x570.ragdemo.win"),
    ("JEV_DISABLED", "backend/app/rag.py",
     'os.getenv("JEV_DISABLED", "")', ""),
    ("JEV_MODEL", "backend/app/rag.py",
     'os.getenv("JEV_MODEL", "jev-latest")', "jev-latest"),
    ("PICK_TTL", "backend/app/rag.py",
     'float(os.getenv("PICK_TTL", "30"))', "30"),
    ("PROBE_TIMEOUT", "backend/app/rag.py",
     'float(os.getenv("PROBE_TIMEOUT", "2.5"))', "2.5"),
    ("RAG_HIGH_DENSE", "backend/app/rag.py",
     'float(os.getenv("RAG_HIGH_DENSE", "0.70"))', "0.70"),
    ("RAG_MID_DENSE", "backend/app/rag.py",
     'float(os.getenv("RAG_MID_DENSE", "0.62"))', "0.62"),
    ("RAG_MIN_DENSE", "backend/app/rag.py",
     'float(os.getenv("RAG_MIN_DENSE", "0.58"))', "0.58"),
    ("REGISTRY_HEARTBEAT", "backend/app/registry.py",
     'int(os.getenv("REGISTRY_HEARTBEAT", "30"))', "30"),
    ("REGISTRY_STALE_MIN", "backend/app/registry.py",
     'int(os.getenv("REGISTRY_STALE_MIN", "3"))', "3"),
    ("TYPESAFE_URL", "backend/app/rag.py",
     'os.getenv("TYPESAFE_URL", "https://api.typesafe.ai/v1/systemone")',
     "https://api.typesafe.ai/v1/systemone"),
]

# 刻意不傳入容器的 3 個。理由記在 backend/DESIGN.md，測試在這裡擋「好心補回去」。
SCOPE_B_WITHHELD = [
    "QDRANT_URLS",           # 容器化降級靠快照同步，不是 live 讀 x570；
                             # 傳進去還會逼 QDRANT_API_KEY 三台同值
    "HOST_MACHINE_ID_FILE",  # registry.py:24 的主機檔案 fallback，
    "HOST_HOSTNAME_FILE",    # 只在原生執行有意義（registry.py:25）
]


@pytest.mark.parametrize("name,src,code_snippet,default", SCOPE_B_FORWARDED)
def test_scope_b_default_is_verbatim_from_source(name, src, code_snippet, default):
    """compose 的 `${VAR:-預設}` 預設值必須逐字等於原始碼的 `os.getenv` 預設。

    backend.md 不變量 #2。實測依據：這個差異**沒有任何報錯**，只是
    `.env` 沒設該項的那台安靜地用錯的值。
    """
    assert code_snippet in (ROOT / src).read_text(encoding="utf-8"), \
        f"{src} 找不到預期片段 {code_snippet!r} —— 原始碼改了，這張表要跟著更新"
    ref = REG[name]
    assert ref.python_read, f"{name} 應被 Python 讀取"
    assert ref.compose_ref, f"{name} 必須以 ${{VAR:-預設}} 轉發進容器（scope B）"
    assert ref.compose_default == default, (
        f"{name} 的 compose 預設值 {ref.compose_default!r} != 原始碼 {default!r}"
    )
    assert not ref.compose_hardcoded, f"{name} 不該被寫死（寫死則 .env 對容器無效）"


def test_scope_b_ollama_models_fallback_is_nested_not_literal():
    """OLLAMA_MODELS 的預設值必須是巢狀的 `${LLM_MODEL:-...}`，不是字面值。

    實測依據：`rag.py:41` 是 `os.getenv("OLLAMA_MODELS", LLM_MODEL)` ——
    第二個參數是 `LLM_MODEL` 這個**變數本身**。抄成 `qwen3:14b` 會讓
    MSI（LLM_MODEL=qwen3:8b）自己的 ollama 也被要求 14b，等於把
    「永遠降級成 8b」換成「三台全滅」—— 兩者都壞，但後者更難診斷。
    """
    compose_src = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "${OLLAMA_MODELS:-${LLM_MODEL:-" in compose_src, \
        "OLLAMA_MODELS 的預設值應巢狀回 LLM_MODEL（rag.py:41 的 fallback 鏈）"
    assert 'os.getenv("OLLAMA_MODELS", LLM_MODEL)' in \
        (ROOT / "backend/app/rag.py").read_text(encoding="utf-8")
    assert REG["OLLAMA_MODELS"].compose_ref, \
        "OLLAMA_MODELS 必須轉發進容器，否則 MSI 永遠降級成用自己的 8b"


@pytest.mark.parametrize("name", SCOPE_B_WITHHELD)
def test_scope_b_withheld_stays_withheld(name):
    """那 3 個「刻意不傳」不許被補回 compose —— 否則會製造新的耦合。

    實測依據：補 `QDRANT_URLS` 進容器會讓 mbp/msi 依賴 x570 在線，並逼
    `QDRANT_API_KEY` 變成三台同值（scope A 正在拆掉的鎖步輪換）；補兩個
    `*_FILE` 會宣告容器裡有那些檔案，而實際上沒有（Docker Desktop 掛單檔案
    不可靠，見 backend/DESIGN.md 第 4 節）。
    """
    ref = REG[name]
    assert ref.python_read, f"{name} 確實被 Python 讀取（這是刻意不傳的前提）"
    assert not ref.compose_ref, \
        f"{name} 是刻意不傳入容器的，理由見 backend/DESIGN.md"
    assert not ref.compose_hardcoded, f"{name} 也不該以字面值寫死"


def test_scope_b_no_variable_is_left_stranded():
    """程式讀得到、卻既沒轉發也沒刻意豁免的容器端變數 —— 應該一個都不剩。

    這是 scope B 的守門斷言。2026-09-27 實測 19 個這種變數：15 個補上、
    3 個刻意不補、1 個（LAN_IP）是政策停用。以後有人加了新旋鈕卻忘了補
    compose（或忘了列進 SCOPE_B_WITHHELD 並在 DESIGN.md 寫理由），這條會失敗。
    """
    stranded = {
        n for n, r in REG.items()
        if r.python_read and not r.compose_ref and not r.compose_hardcoded
        and n not in ea.POLICY_EXCLUDED
    }
    # ingest 端的變數容器本來就讀不到（管線跑在 host），不在本模組責任內。
    container_side = {n for n in stranded if not REG[n].host_only_reader()}
    assert container_side == set(SCOPE_B_WITHHELD), (
        "有變數沒被 compose 轉發、也不在刻意不傳的清單裡："
        f"{sorted(container_side - set(SCOPE_B_WITHHELD))}。"
        " 要嘛補進 compose.yaml（預設值抄原始碼），"
        "要嘛加進 SCOPE_B_WITHHELD 並在 backend/DESIGN.md 寫理由。"
    )


def test_name_scoped_machine_id_is_not_treated_as_local_identity():
    """`HOST_API_X570` 的預設值有 x570，但它**不是本機身份**。

    實測依據（2026-09-27 scope B 踩到）：`https://api-x570.ragdemo.win`
    的主機名被 IDENTITY_RE 命中，於是 MSI 被要求「必須覆蓋 HOST_API_X570」。
    但 `rag.py:167-171` 是一張三台都要有的對照表（前端「連線與來源」彈窗
    逐台列位址），缺一台就少一列 —— 那是假警告，不是設定錯誤。
    """
    for name in ("HOST_API_X570", "HOST_API_MBP", "HOST_API_MSI"):
        assert REG[name].compose_default, f"{name} 應有 compose 預設值"
        assert not REG[name].has_identity_default(), \
            f"{name} 的機台代號在鍵名裡，不該被判成本機身份"
    # 這個例外不放寬真正會炸的那個：TS_IP 在 ports: 裡，身份檢查仍生效。
    assert REG["TS_IP"].in_ports
    assert REG["TS_IP"].has_identity_default()
    assert REG["HOST_ID"].has_identity_default()


# ── fallback 連鎖 ───────────────────────────────────────────

def test_chain_only_fallback_is_detected():
    """純連鎖的下層要標 superseded_by。

    實測依據：rag.py:33-34
      OLLAMA_DEFAULT = os.getenv("OLLAMA_BASE_URL", ...)
      OLLAMA_URLS     = os.getenv("OLLAMA_URLS", OLLAMA_DEFAULT)
    設了 OLLAMA_URLS，OLLAMA_BASE_URL 的值就到不了任何消費者。
    rag.py:35-36 的 QDRANT_DEFAULT/QDRANT_URL 同構。
    """
    assert REG["OLLAMA_BASE_URL"].superseded_by == "OLLAMA_URLS"
    assert REG["QDRANT_URL"].superseded_by == "QDRANT_URLS"


def test_var_with_independent_uses_is_not_marked_superseded():
    """有獨立用途的變數不能被標成「被上層蓋掉」。

    實測依據：初版只看 `os.getenv(A, B)` 就判 A 蓋掉 B，
    於是報出「LLM_MODEL 被 OLLAMA_MODELS 蓋過（這個被忽略）」——
    但 rag.py:215 `else LLM_MODEL` 與 :217 `return LLM_MODEL` 仍在
    獨立使用它，那是完全錯誤的宣稱。判準是「除了連鎖還有沒有別的用處」。
    """
    assert REG["LLM_MODEL"].superseded_by == "", \
        "LLM_MODEL 在 rag.py:215,217 有獨立使用，不該標成被蓋掉"


def test_identity_laden_defaults_are_recognised():
    """預設值內建某台機器身份的變數要認得出來。

    實測依據：舊版 KNOWN 手動標 TS_IP/HOST_ID 為 required。
    改寫後若沒有這條機械規則，mbp/msi 少設 TS_IP 就不會被抓到 ——
    而 compose 的 ports: 用它，會試圖 bind x570 的 IP 而啟動失敗。
    """
    assert REG["TS_IP"].in_ports, "TS_IP 應出現在 compose 的 ports:"
    assert REG["TS_IP"].has_identity_default()
    assert REG["HOST_ID"].compose_default == "x570"


def test_identity_check_only_fires_on_other_machines(capsys):
    """本機 HOST_ID 與預設不同、且變數沒設，才報錯。

    兩個設計要點，都是踩過才學到的：
    1. 必須自帶環境，不能讀真實 .env：.env 不進版控，CI 是 fresh checkout，
       讀了會拿到空 dict → TS_IP 未設 → 斷言翻轉成假失敗
       （2026-09-26 CI run 36253262735 實際失敗）。
    2. 直接呼叫 ea.audit()，不要在測試裡重寫一份判定邏輯：
       我第一版重寫了 find_wrong，漏掉 `my_host != baked_host` 那道
       guard，於是斷言自相矛盾 —— 重複實作必然會和真品漂移。
    """
    reg = REG
    baked = reg["HOST_ID"].compose_default        # 這份 compose 烤給哪台
    assert baked, "HOST_ID 應有 compose 預設值"

    def run(env: dict[str, str]) -> tuple[int, str]:
        capsys.readouterr()                        # 清掉先前輸出
        n = ea.audit(env, reg, "test")
        return n, capsys.readouterr().out

    # 情境 A：就是這份 compose 的主人，且身分變數齊備 → 不發動身分檢查
    n_a, out_a = run({"HOST_ID": baked, "TS_IP": "1.2.3.4",
                      "OLLAMA_URLS": "http://1.2.3.4:11434"})
    assert "機台身份必須覆蓋" not in out_a, \
        f"HOST_ID 等於預設值 {baked}，不該要求覆蓋身分變數\n{out_a}"

    # 情境 B：別的機器，但身分變數已設 → 仍不該要求
    n_b, out_b = run({"HOST_ID": "mbp", "TS_IP": "100.64.121.9",
                      "OLLAMA_URLS": "http://100.64.121.9:11434"})
    assert "機台身份必須覆蓋" not in out_b, \
        f"TS_IP/OLLAMA_URLS 都設了，不該再要求\n{out_b}"

    # 情境 C：別的機器且漏設 → 必須抓到（否則 docker bind 別人的 IP 而失敗）
    n_c, out_c = run({"HOST_ID": "mbp"})
    assert "機台身份必須覆蓋" in out_c, f"mbp 少設 TS_IP 應該被抓到\n{out_c}"
    assert "TS_IP" in out_c, "TS_IP 用在 ports: 缺了會啟動失敗，必須點名"
    assert "mbp" in out_c and baked in out_c, "錯誤訊息要指出本機與預設的差異"


# ── docstring / 產生檔不再有不實宣稱 ───────────────────────

def test_no_false_claim_about_prefix_stripping():
    """「程式端會自動去掉前綴」是從未實作的宣稱。

    實測依據：全 repo grep 不到任何前綴處理程式碼。實際慣例是把機台名
    燒進變數名（rag.py:167-171 的 HOST_API_X570/MBP/MSI）。
    """
    audit_src = AUDIT.read_text(encoding="utf-8")
    # 允許出現在「說明這是不實宣稱」的段落裡，但產生檔必須是正確說法
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if "自動去掉前綴" in line:
            pytest.fail(f".env.example 仍宣稱自動去掉前綴: {line.strip()}")


def test_no_reference_to_nonexistent_env_audit_sh():
    """docstring 曾宣稱靠 scripts/env-audit.sh 反查 —— 該檔從不存在。

    實測依據：ls scripts/ 只有 env-audit.py、law-update-worker.sh、
    sync-snapshot.sh。手維護的 KNOWN 表因此悄悄爛掉（漏 44 個變數）。
    現在的 docstring 只能陳述它真的做的事。
    """
    src = AUDIT.read_text(encoding="utf-8")
    assert "scripts/env-audit.sh 從程式碼反查" not in src, \
        "又開始宣稱一個不存在的工具"
    assert not (ROOT / "scripts" / "env-audit.sh").exists()


def test_template_contains_no_credentials():
    """--template 產生的骨架不得含任何真實憑證值。"""
    out = subprocess.run(["python3", str(AUDIT), "--template"],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    body = out.stdout
    assert "════ env audit" not in body, "--template 不應混入稽核輸出"
    secrets = re.findall(
        r"(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)=[A-Za-z0-9_\-]{16,}", body)
    assert not secrets, f"產生檔含疑似憑證: {secrets}"


def test_template_has_no_lan_ip_assignment():
    """追蹤檔不得出現 LAN_IP= —— 否則 push 與 CI 直接失敗。

    實測依據（2026-09-26 實測）：改寫前的 KNOWN 表本來就漏了 LAN_IP，
    所以舊 .env.example 沒這行、push 都過。我改成程式碼反查後
    LAN_IP 被正確抓到（registry.py:21 確實有讀），產生器就印出
    `LAN_IP=`，pre-push hook 與 ci.yml 立刻擋下 push。

    但 LAN_IP 不是「無人讀」—— 它是政策性停用（ARCHITECTURE.md
    IP 準則 2026-09-22 定案、ROADMAP.md:75「已停用，勿再寫」）。
    所以要以註解保留說明，而不是從清單裡悄悄消失。
    """
    body = subprocess.run(["python3", str(AUDIT), "--template"],
                          cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout
    assert not re.search(r"^LAN_IP=", body, re.M), "產生檔含 LAN_IP= 會被 hook 擋"
    # 但說明要還在 —— 否則下次又有人加回去
    assert "LAN_IP" in body, "LAN_IP 的停用理由應以註解形式保留"
    assert re.search(r"^# LAN_IP", body, re.M), "LAN_IP 應以註解列出行保留可發現性"

    # 版控裡的實際檔案也要乾淨（防止有人手改 .env.example 後忘了重新產生）
    tracked = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert not re.search(r"^LAN_IP=", tracked, re.M), \
        "版控中的 .env.example 含 LAN_IP=，push 會被 pre-push 與 CI 擋下"
    assert tracked == body, \
        ".env.example 與 --template 產出不一致，請跑 scripts/env-audit.py --template > .env.example"


def test_backend_env_example_does_not_exist():
    """backend/.env 的副本一律不得復活。

    2026-09-26 刪除 backend/.env（33f9da3）就是為了消除副本漂移：
    那天 POSTGRES_PASSWORD 兩份不同（e328bd31728a vs 55cebf3c8276），
    而兩把都能認證本機 role，所以漂移是靜默的 —— 要等到刪的那一刻才發現。

    backend/.env.example 是同一份副本的「招募廣告」：留著就會有人照著
    再建一份 backend/.env。刪了之後連範例也不該留。

    只擋 .env.example 不擋 .env 本體：x570/mbp 可能還在過渡期
    （HOST-UPGRADE.md §0b 叫它們刪），對那些機器斷言 .env 不存在
    會產生假警報。
    """
    assert not (ROOT / "backend" / ".env.example").exists(), \
        "backend/.env.example 復活了 —— 請改用根 .env.example"


def test_policy_excluded_variables_are_distinguished_from_ghosts():
    """政策停用 ≠ 無人讀。兩者的處置完全不同。

    舊版 GHOSTS 把「程式讀不到」當成單一類別，害得真正讀得到的
    EMBED_MODEL／RERANK_MODEL／QDRANT_URLS 被誤判。政策性停用
    的變數必須被反查到（registry.py:21 有讀），只是不能寫進 .env。
    """
    assert "LAN_IP" in REG, "LAN_IP 應被反查到（registry.py:21 有讀）"
    assert REG["LAN_IP"].python_read, "LAN_IP 是被 Python 讀取的，不是幽靈"
    assert "LAN_IP" in ea.POLICY_EXCLUDED, "LAN_IP 應在政策停用清單"
    # 政策清單的每個項目都必須真的有程式在讀，否則就該是幽靈
    for name in ea.POLICY_EXCLUDED:
        assert name in REG, f"{name} 在政策清單但反查不到"
        assert REG[name].python_read or REG[name].shell_read or REG[name].compose_ref, \
            f"{name} 沒有任何程式讀，應該是幽靈而不是政策停用"


def test_registry_has_no_hand_maintained_list():
    """變數清單必須真的從程式碼推導，不能是手寫的。

    舊 KNOWN 表 23 個，反查得到 67 個 —— 差 44 個。
    若有人再加回手寫清單，這條會在模組出現 KNOWN 時失敗。
    """
    assert not hasattr(ea, "KNOWN"), \
        "出現手維護的 KNOWN 表 —— 請改由 compose/Python/shell 反查"
    assert len(REG) >= 50, f"反查到的變數數量異常下降: {len(REG)}"
    # 舊手寫清單漏掉的代表性變數，現在必須在
    for name in ("COHERE_MODELS", "GEMINI_MODELS", "GROQ_MODELS",
                 "MISTRAL_MODELS", "NVIDIA_MODELS", "OPENROUTER_MODELS",
                 "ZEN_FREE_MODELS", "PROBE_TIMEOUT", "RAG_MIN_DENSE",
                 "HOST_API_MBP", "QDRANT_PEER_API_KEY"):
        assert name in REG, f"反查漏掉 {name}"
