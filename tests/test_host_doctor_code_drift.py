"""`host-doctor.sh` 的 `code-drift` 檢查 —— 2026-10-02 補，x570 實測踩到才有的。

## 這道檢查為什麼存在

x570 的後端**長達數天**跑著 2026-09-30 拆分重構**之前**的架構：容器裡沒有
`gateway.py`／`retrieve.py`／`cn_parse.py`／`law_meta.py`／`common/`，
`rag.py` 是 85KB 的舊單體版而工作區是 46KB 拆分版。

**它為什麼一路綠**：舊版本**自洽**，所以 `/health`、`/query`、peer 探測、
registry 心跳全部正常。而當時每一道現有的檢查都回綠：

| 檢查 | 回報 | 為什麼抓不到 |
|---|---|---|
| `repo-state` | 乾淨 | 問的是**工作樹**，不是容器 |
| `upgrade` | 與上游同步 | 問的是 **git**，不是映像 |
| `container:api` | running | 只問「跑著嗎」，不問「跑什麼」 |
| `env-check` | 一致 | 問的是 `.env` |

沒有任何一道問過「容器裡跑的是不是這個 repo」。這就是 x570 能帶著一個
5 天前的後端通過所有驗收的原因。

**為什麼會踩到**：`docker compose up -d` 只 Recreate 容器（換環境變數），
**不重建映像**（那要 `--build`）。於是 pull 完程式碼、跑 `up -d`，會得到
「新環境變數 ＋ 舊程式碼」的混合體 —— 而且完全沒有症狀。

## 為什麼這些測試是靜態的

CI 沒有跑著 api 容器，執行 `ch_code_drift` 只會得到 skip。所以這裡驗的是
**它存在、它接線了、它的判準與修法寫對了**。真正「會不會抓到」靠本機實測：
`code-drift` 在兩種偏移（工作區多一個檔、容器多一個檔）下都被實測觸發過。
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "scripts" / "host-doctor.sh"


def _src() -> str:
    assert DOCTOR.is_file(), f"{DOCTOR} 不見了"
    return DOCTOR.read_text(encoding="utf-8")


def _fn(text: str, name: str) -> str:
    """抽出某個 ch_* 函式本體（到下一個行首 `# ──` 分節為止）。"""
    start = text.index(f"\n{name}() {{")
    m = re.search(r"\n# ── ", text[start + 1:])
    end = start + 1 + (m.start() if m else len(text))
    return text[start:end]


# ── 存在與接線 ─────────────────────────────────────────────────────────

def test_check_exists():
    assert "ch_code_drift() {" in _src(), "ch_code_drift 函式不見了"


def test_check_is_actually_called():
    """最容易被悄悄弄丟的一步：函式寫了但沒接進執行清單。

    症狀是「這道檢查看起來存在、報告裡從來不出現」—— 那比沒有更糟，
    因為人會以為它有在保護。
    """
    src = _src()
    calls = re.findall(r"^ch_\w+$", src, re.M)
    assert "ch_code_drift" in calls, (
        f"ch_code_drift 沒被呼叫（執行清單是：{calls}）"
    )


# ── 判準 ───────────────────────────────────────────────────────────────

def test_compares_content_hash_not_timestamps():
    """判準必須是**內容雜湊**，不是時間戳。

    Friction 點：看起來最自然的是比 `image Created` 與 commit date。但那
    有兩個錯法 —— 時區／git 設定會讓它誤報，而「從舊 checkout 重建」會
    讓時間戳是新的而內容是舊的，**正好漏掉最壞的情況**。x570 那次就是
    後者（映像比工作區舊，但其實是 pull 之後沒 rebuild）。
    """
    fn = _fn(_src(), "ch_code_drift")
    assert re.search(r"sha256sum|shasum", fn), "沒有比對內容雜湊"
    assert "Created" not in fn and "cI" not in fn, (
        "不該用時間戳當判準 —— 見 docstring 的說明"
    )


def test_compares_the_whole_app_tree():
    """要比 `app/` 底下**所有** .py，不是挑幾個檔案。

    x570 的實際情況是「少了 5 個檔案、rag.py 是另一個版本」。挑檔案比對
    （例如只看 main.py）會全數漏掉 —— 而 main.py 恰恰是**沒變**的那個。
    """
    fn = _fn(_src(), "ch_code_drift")
    assert re.search(r"find\s+app\s+-name\s+'\*\.py'", fn) or \
           re.search(r'find\s+app\s+-name\s+"\*\.py"', fn), \
        "要用 find 列出整個 app/，不是挑檔案"
    # 兩邊都要有 —— 只比一邊等於沒比
    assert fn.count("find app -name") + fn.count("find app -name") >= 1
    assert "/app" in fn, "容器側要進到 /app 比"


def test_only_app_is_compared_because_that_is_all_the_dockerfile_copies():
    """比對範圍要對齊 Dockerfile。

    Dockerfile 是 `COPY app ./app`，所以 `app/` 就是完整的不變量。若哪天
    改成 COPY 整個 backend/，這裡要跟著擴大 —— 而忘了擴大的症狀是
    「新增了 backend/ 某個檔但 doctor 不報」。
    """
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY app ./app" in dockerfile, (
        "Dockerfile 改了 COPY 範圍 → host-doctor 的 ch_code_drift 要跟著調整"
    )


# ── 修法與訊息 ─────────────────────────────────────────────────────────

def test_failure_message_names_the_build_fix():
    """fail 的訊息必須指名 `--build`，否則人會只跑 `up -d`（等於沒修）。

    `up -d` 與 `up -d --build` 的差別**就是這道檢查要抓的那件事**，
    訊息裡少三個字就會原地重現同一個故障。
    """
    fn = _fn(_src(), "ch_code_drift")
    assert "--build" in fn, "訊息要指名 `docker compose up -d --build api`"


def test_failure_message_says_other_checks_will_be_green():
    """要明說「其他檢查都會是綠的」，因為那正是當初沒有人發現的原因。

    沒有這句，使用者看到 `[FAIL] code-drift` 但其他全 ok，會合理懷疑
    「是不是這道檢查誤報了」—— 然後就把它跳過。
    """
    fn = _fn(_src(), "ch_code_drift")
    assert "綠" in fn, "要說明為什麼其他檢查都沒抓到"


def test_failure_lists_which_files_differ():
    """要指名**哪些檔案**不同。只說「不一致」不足以診斷。

    x570 那次的診斷關鍵就是「容器裡沒有 gateway.py / retrieve.py /
    common/」—— 沒有檔名就只能猜。
    """
    fn = _fn(_src(), "ch_code_drift")
    assert "只在工作區有" in fn and "只在容器有" in fn, (
        "要分方向列出差異（單體 vs 拆分兩個方向都會發生）"
    )


# ── 不可退化 ───────────────────────────────────────────────────────────

def test_never_prints_file_contents():
    """只印檔名，不印檔案內容。

    app/ 下全部是程式碼不是憑證，但 doctor 的規則是「值一律不顯示」——
    這條規則之所以可信，靠的是**沒有任何一個分支例外**。
    """
    fn = _fn(_src(), "ch_code_drift")
    for forbidden in ("cat /app", "cat app", "head -c", "sed -n.*app/"):
        assert forbidden not in fn, f"比對過程不該讀出內容：{forbidden}"


@pytest.mark.parametrize("tool,why", [
    ("sha256sum", "Linux 走這個"),
    ("shasum", "macOS（mbp）只有 shasum，沒有 sha256sum —— 少了它 mbp 直接 skip"),
])
def test_hash_tool_has_a_portable_fallback(tool, why):
    assert tool in _fn(_src(), "ch_code_drift"), f"{why}"


# ── law-version 的角色判斷（同一個家族）──────────────────────────────────
#
# 這兩道共用一個病根：**報告沒有分角色**。
#   code-drift  問「容器裡跑的是不是這個 repo」—— 以前根本沒問
#   law-version 問「有沒有 .law_version」—— 但 source 機本來就不會有，
#               於是一台完全正常的機器被報成「快照同步還沒成功過」。
# 兩者的修法同一個：先分清角色，再判斷。

LAW_FN_SNIPPET = "LAW_SYNC_FILE"


def test_law_version_distinguishes_source_from_backup():
    """缺 `.law_version` 時要先分角色。

    實測（x570）：它是 source 機、每日 `sync_daily` 有在跑，卻長期回
    「沒有 data/laws/.law_version（…快照同步還沒成功過）」—— 結構性錯誤，
    而且**永遠不會自己好**。這是同一個「報告沒有分角色」家族。
    """
    src = _src()
    assert LAW_FN_SNIPPET in src, "沒有宣告角色判斷用的 .law_sync.json 路徑"
    i = src.index('bump law-version warn "沒有 data/laws/.law_version')
    # 那個 warn 之前必須先有 source 分支
    before = src[:i]
    assert 'if [ -f "$LAW_SYNC_FILE" ]' in before, (
        "缺 .law_version 時沒有先分 source／備援 —— source 機會被誤報成故障"
    )


def test_source_machine_message_does_not_claim_snapshot_never_ran():
    """source 機的訊息不能提「快照同步」，因為它從來不跑那條路。

    這是那句話得留在 warn 裡的理由：對備援機它是對的，對 source 機是錯的。
    訊息要隨角色換掉，不是兩種機器共用一句。
    """
    src = _src()
    i = src.index('if [ -f "$LAW_SYNC_FILE" ]; then')
    seg = src[i:i + 700]
    assert "source" in seg, "source 分支要說明角色判斷依據"
    assert "快照同步" not in seg.split("else")[0], (
        "source 分支不該提快照同步 —— 它不走那條路"
    )
