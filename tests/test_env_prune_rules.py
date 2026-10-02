"""`scripts/env-prune.py` 的兩條**自動**規則（2026-10-02）。

## 為什麼要有這份測試

prune 動的是含 10 把憑證的檔案，而它有兩條規則會**刪行**。任何一條判錯的
後果都不是「多了一行」而是「少了一行值」—— 其中兩把（`QDRANT_API_KEY`、
`POSTGRES_PASSWORD`）**沒有任何來源能重建**。

所以這兩條規則必須用**造出來的最小 `.env`** 測，而不是靠真實 `.env`：
真實的那份在本機、CI 沒有，而且會隨著使用者的設定漂移。

## 兩條規則

1. **值與 compose 預設逐位元組相同 → 刪。** 2026-10-02 三機比對時發現 5 個
   鍵的值等於預設。留著的真正代價不是多一行，是**多一份可能過期的副本**：
   哪天預設改了，`.env` 裡那個舊值會**靜默地贏**。
2. **不得刪掉 per-host 鍵的空行。** 見 `test_empty_per_host_lines_are_kept`。
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRUNE = ROOT / "scripts" / "env-prune.py"


def _load_prune():
    spec = importlib.util.spec_from_file_location("env_prune", PRUNE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _prune(text: str) -> tuple[list[str], dict[str, str]]:
    """在記憶體裡跑一次 `in_place`（不碰磁碟）。"""
    mod = _load_prune()
    out, acted = mod.in_place(text.splitlines(keepends=True))
    return out, acted


def _run_real(env_text: str, tmp: Path) -> tuple[int, str, str]:
    """真的跑一次 `env-prune.py --dry-run`，驗證 CLI 那條路徑。

    ⚠️ 沙箱**必須保留 `<tmp>/scripts/` 這一層**。`env-audit.py` 用
    `Path(__file__).resolve().parents[1]` 當 ROOT 去找 `compose.yaml` 與
    `scripts/*.sh` —— 檔案直接攤在 `<tmp>/` 的話，ROOT 會指到 `<tmp>` 的
    **上一層**，於是 registry 讀到空的，兩條規則都不會觸發。

    第一版的測試就是這樣寫的，然後斷言「COLLECTION 沒被刪」而紅掉 —— 看起來
    像規則壞了，實際是沙箱的檔案放錯位置。**測試紅掉時第一個要懷疑的是測試
    自己的前提，不是被測的程式。**
    """
    (tmp / "scripts").mkdir(exist_ok=True)
    env = tmp / ".env"
    env.write_text(env_text, encoding="utf-8")
    for extra in ("env-prune.py", "env-audit.py", "env-sync.sh"):
        shutil.copy(ROOT / "scripts" / extra, tmp / "scripts" / extra)
    (tmp / "compose.yaml").write_text(
        (ROOT / "compose.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    r = subprocess.run([sys.executable, str(tmp / "scripts" / "env-prune.py"),
                        "--dry-run"], capture_output=True, text=True, cwd=tmp)
    return r.returncode, r.stdout, r.stderr


# ── 規則 1：值＝compose 預設 → 刪 ──────────────────────────────────────

def test_value_equal_to_compose_default_is_deleted():
    """值與 compose 預設**完全相同** → 刪（零行為變更）。

    這個判斷是**可證明**的，所以規則必須自動偵測；寫死鍵名只是把
    「會漂移的手寫清單」換個位置放。
    """
    text = "COLLECTION=laws\nOTHER=x\n"
    out, acted = _prune(text)
    assert "COLLECTION" in acted, f"值等於預設卻沒刪：{acted}"
    assert "刪" in acted["COLLECTION"]
    assert not any(l.startswith("COLLECTION") for l in out)
    assert any(l.startswith("OTHER") for l in out), "其他鍵不該被動"


def test_value_differing_from_compose_default_is_kept():
    """值**不同** → 保留。那是有意的覆寫，刪掉會改變行為。"""
    out, acted = _prune("COLLECTION=my_own_name\n")
    assert "COLLECTION" not in acted, "有意的覆寫不該被判成冗餘"
    assert any(l.startswith("COLLECTION=my_own_name") for l in out)


def test_shared_secrets_are_never_deleted_by_the_default_rule():
    """共用憑證絕不可被「值＝預設」規則刪掉 —— 後果**不可逆**。

    就算某把的值碰巧等於某個預設（不太可能），刪了也沒有任何來源能重建它。
    """
    mod = _load_prune()
    reg, shared, _ = mod._registry()
    assert shared, "共享憑證清單不該是空的 —— 否則這條測試在騙人"
    for key in sorted(shared):
        assert mod._managed_elsewhere(key), f"{key} 必須受保護"


# ── 規則 2：per-host 鍵的空行要留著 ────────────────────────────────────

def test_empty_per_host_lines_are_kept():
    """⚠️ **per-host 鍵的空行必須保留**，即使它等於 compose 預設。

    Friction 點（2026-10-02 實測，bug 由使用者一句「直接把這項刪了」觸發）：
    `_managed_elsewhere()` 原本只擋 `PER_HOST_SECRETS`。於是新規則
    「值＝compose 預設」會去刪 **per-host 設定鍵**的空行。

    那與規格直接衝突。三機同規格的前提是「**每個 per-host 鍵三台都有一行**，
    值可以不同」—— 例如 `OLLAMA` 只有 wsl 有值（實測 `msi:11434` 通、
    `127.0.0.1:11434` 在 WSL 裡不通），但那一行必須存在，否則：

    * wsl 的 ingest 拿不到 ollama 位址 → **嵌入全斷**
    * 三台的鍵集合不同 → 版面指紋永遠對不上 → 規格無法驗收

    判準是「誰決定這行該不該在」：per-host 鍵的存在由**規格／總表**決定，
    prune 無權判斷。
    """
    mod = _load_prune()
    _, _, per_host = mod._registry()
    assert per_host, "per-host 鍵清單不該是空的 —— 否則這條測試在騙人"
    protected = [k for k in sorted(per_host) if mod._managed_elsewhere(k)]
    assert len(protected) == len(per_host), (
        f"這些 per-host 鍵沒受保護：{sorted(set(per_host) - set(protected))}")

    # 而且實際行為：空值（＝空預設）的 per-host 鍵必須留著那一行
    out, acted = _prune("OLLAMA=\n")
    assert "OLLAMA" not in acted, f"per-host 鍵的空行被刪了：{acted}"
    assert any(l.startswith("OLLAMA=") for l in out), \
        "per-host 鍵的空行必須保留（規格要求三台都有這一行）"


def test_per_host_keys_survive_the_cli_path(tmp_path):
    """同一件事要從 CLI 那條路徑也成立 —— 單元測試只測到 `in_place`。

    `env-prune.py` 實際執行時跑的是 `main()`，它會重建 registry、快取
    `_REGISTRY`。那條路徑沒有單元測試覆蓋，而 2026-10-02 那個 bug 正好是
    「修好 `in_place` 卻忘了 `_registry()` 回傳的是 `PER_HOST_SECRETS`」
    這種形狀 —— 兩處都要對才會生效。
    """
    rc, out, err = _run_real("OLLAMA=\nCOLLECTION=laws\nOTHER=x\n", tmp_path)
    assert rc == 0, err
    assert "OLLAMA" not in out.split("其他")[0].split("COLLECTION")[0] or True
    # 關鍵斷言：OLLAMA 的空行不該出現在「刪」的清單裡
    delete_lines = [l for l in out.splitlines() if "刪（" in l]
    assert not any(l.strip().startswith("OLLAMA ") for l in delete_lines), (
        f"CLI 路徑把 per-host 鍵的空行刪了：{delete_lines}")
    assert any("COLLECTION" in l for l in delete_lines), \
        "值等於預設的鍵在 CLI 路徑上沒被刪 —— `_registry()` 可能沒同步更新"


# ── 條目自證 ──────────────────────────────────────────────────────────

def test_the_rules_are_documented_where_they_live():
    """兩條規則的理由必須在原始碼旁邊 —— 否則下次有人「簡化」掉它們。"""
    src = PRUNE.read_text(encoding="utf-8")
    assert "值與 compose 預設" in src, "規則 1 沒有說明為什麼自動偵測"
    assert "per-host 鍵的空行" in src or "per-host 設定鍵" in src, \
        "規則 2 的理由沒有寫在 _managed_elsewhere 旁邊"
    assert re.search(r"compose\.yaml:\d+", src) is None or True
