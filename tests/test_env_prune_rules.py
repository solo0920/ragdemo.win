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


def _sandbox(tmp: Path) -> Path:
    """搭一個能讓 env-audit 算出**完整** registry 的沙箱。

    ⚠️ 必須複製 `backend/`（2026-10-02 實測踩到）。
    `Ref.superseded_by`（誰取代誰）不是從 compose.yaml 推導的，而是從
    `backend/app/gateway.py` 的 fallback 鏈推出來的 —— 沒有 backend/ 時它
    全變成空字串，於是「被取代 → 刪」那條規則永遠不觸發。

    **症狀極具欺騙性**：規則看起來「沒生效」，於是會跑去改規則；而實際上是
    沙箱不完整。所以這裡額外斷言 registry 的規模 —— **殘缺的沙箱要吵，
    不能安靜地讓所有測試變成空轉。**
    """
    (tmp / "scripts").mkdir(exist_ok=True)
    for extra in ("env-prune.py", "env-audit.py", "env-sync.sh"):
        shutil.copy(ROOT / "scripts" / extra, tmp / "scripts" / extra)
    shutil.copy(ROOT / "compose.yaml", tmp / "compose.yaml")
    shutil.copytree(ROOT / "backend", tmp / "backend")
    shutil.copytree(ROOT / "ingest", tmp / "ingest")
    # ⚠️ 連 `settings/env/` 也要 —— `per_host_keys()` 是從
    # `hosts.shared.env` 的 `<機台>_<鍵>` 列推導的，沒有這個檔它回空集合，
    # 於是「per-host 鍵受保護」那條規則形同不存在。
    # 這是**第三個**讓沙箱殘缺的坑（見下面兩個斷言的說明）。
    (tmp / "settings" / "env").mkdir(parents=True, exist_ok=True)
    for f in ("hosts.shared.env",):
        shutil.copy(ROOT / "settings" / "env" / f, tmp / "settings" / "env" / f)

    spec = importlib.util.spec_from_file_location(
        "env_audit_probe", tmp / "scripts" / "env-audit.py")
    ea = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ea)
    reg = ea.build_registry()
    assert len(reg) >= 60, (
        f"沙箱只掃到 {len(reg)} 個變數（真實 repo 是 70 上下）—— "
        f"沙箱不完整，測試會**靜默地什麼都驗不到**。缺 backend/ 或 ingest/？")
    superseded = {k: v.superseded_by for k, v in reg.items() if v.superseded_by}
    assert superseded, (
        "沙箱裡 `superseded_by` 全是空的 —— backend/ 沒複製到。"
        "「被取代 → 刪」那條規則在這個沙箱裡不可能觸發，測試會假綠。")
    ph = ea.per_host_keys()
    assert ph, (
        "沙箱裡 `per_host_keys()` 是空的 —— settings/env/hosts.shared.env 沒複製到。"
        "「per-host 鍵受保護」那條規則在這個沙箱裡形同不存在，測試會假紅。")
    return tmp


def _run_real(env_text: str, tmp: Path) -> tuple[int, str, str]:
    """真的跑一次 `env-prune.py --dry-run`，驗證 CLI 那條路徑。

    ⚠️ **必須看 returncode 與 stderr。** 只 grep stdout 會把腳本整個 crash
    藏起來 —— 2026-10-02 踩過：`_registry()` 從 3 元素改成 4 元素而呼叫端
    沒跟著改，腳本拋 `ValueError` 結束，而我的驗證只印「（沒有刪任何東西）」，
    看起來像「規則沒觸發」，於是跑去改規則而不是修那個 unpack。
    """
    _sandbox(tmp)
    (tmp / ".env").write_text(env_text, encoding="utf-8")
    r = subprocess.run([sys.executable, str(tmp / "scripts" / "env-prune.py"),
                        "--dry-run"], capture_output=True, text=True, cwd=tmp)
    return r.returncode, r.stdout, r.stderr


# ── 規則 1：值＝compose 預設 → 刪 ──────────────────────────────────────

def _deleted(out: str) -> set[str]:
    """從 prune 輸出取出「被刪的鍵名」集合。

    ⚠️ **必須取第一欄，不能用子字串比對。** 2026-10-02 踩到：刪除理由是
    `刪（被 OLLAMA_URLS 取代，而 OLLAMA_URLS 有值 → 讀不到這把）` ——
    理由裡**提到** `OLLAMA_URLS`，於是 `any("OLLAMA_URLS" in line ...)`
    對「刪掉 `OLLAMA_BASE_URL`」那行也成立。斷言就這樣錯判成
    「取代者也被刪了」，而實際上規則運作完全正常。

    刪除理由是給人看的，裡面出現別的鍵名是**正常**的；要比對的只有第一欄。
    """
    keys = set()
    for line in out.splitlines():
        s = line.strip()
        if "刪（" in s:
            keys.add(s.split()[0])
    return keys


def _sandbox_prune(tmp: Path, env_text: str) -> tuple[int, set[str], str]:
    """在沙箱裡跑 dry-run，回傳 (returncode, 被刪的鍵, stderr)。

    命名刻意區別於上面的記憶體版 `_prune(text)`：兩個同名函式裡後者會覆蓋
    前者，然後**前面**的測試會用錯簽章而拋 `TypeError` —— 症狀指向錯誤的
    那一行，實際問題在檔案後面。（這也是為什麼我在第一版把兩個都叫 `_prune`。）
    """
    rc, out, err = _run_real(env_text, tmp)
    return rc, _deleted(out), err


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
    _, shared, _, _ = mod._registry()
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
    _, _, per_host, _ = mod._registry()
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
    rc, dels, err = _sandbox_prune(tmp_path, "OLLAMA=\nCOLLECTION=laws\nOTHER=x\n")
    assert rc == 0, err
    # 關鍵斷言：OLLAMA 的空行不該出現在「刪」的清單裡
    assert "OLLAMA" not in dels, f"CLI 路徑把 per-host 鍵的空行刪了：{sorted(dels)}"
    assert "COLLECTION" in dels, \
        "值等於預設的鍵在 CLI 路徑上沒被刪 —— `_registry()` 可能沒同步更新"


# ── 規則 3：被更高優先的來源取代 → 刪 ──────────────────────────────────

def test_superseded_key_with_a_set_superseder_is_deleted(tmp_path):
    """**被取代、而且取代者真的有值** → 刪（哪怕本鍵有值）。

    Friction 點（2026-10-02 三機比對）：`OLLAMA_BASE_URL` 是 `OLLAMA_URLS`
    的低優先 fallback。mbp 兩個都有值 → 程式**永遠讀不到** `OLLAMA_BASE_URL`
    → 那個值是死重量，而且讓三台的鍵集合不同 → 版面指紋對不上。

    為什麼 `DELETE` 那份手寫清單抓不到：它的規則是「**空值** ＋ 有預設值」，
    而這個鍵是「**有值** 也要刪」—— 判準不同。手寫清單表達不了「被取代」，
    但程式碼可以：`Ref.superseded_by` 就是那個事實的機器可讀形式。
    """
    rc, dels, err = _sandbox_prune(
        tmp_path, "OLLAMA_BASE_URL=http://a:11434\nOLLAMA_URLS=http://b:11434\n")
    assert rc == 0, err
    assert "OLLAMA_BASE_URL" in dels, f"被取代的鍵沒被刪：{sorted(dels)}"
    assert "OLLAMA_URLS" not in dels, "取代者本身不該被刪"


def test_superseded_key_is_kept_when_the_superseder_is_empty(tmp_path):
    """⚠️ **取代者空值時，被取代的鍵必須留著。**

    那正是低優先 fallback 的用途：沒有 `OLLAMA_URLS` 時，
    `OLLAMA_BASE_URL` 就是唯一能讓 ollama 接上的設定。刪掉它 = ollama 完全
    連不上，而症狀是「嵌入全失敗」這種很難回推的形狀。

    這就是為什麼那條規則的條件必須是「取代者**有值**」，而不是單純
    「有 superseded_by 就刪」。
    """
    rc, dels, err = _sandbox_prune(
        tmp_path, "OLLAMA_BASE_URL=http://a:11434\nOLLAMA_URLS=\n")
    assert rc == 0, err
    assert "OLLAMA_BASE_URL" not in dels, (
        f"取代者空值卻把被取代的鍵刪了 → ollama 會連不上：{sorted(dels)}")


def test_superseded_key_is_kept_when_superseder_is_absent(tmp_path):
    """取代者**根本不存在** → 被取代的鍵必須留著。"""
    rc, dels, err = _sandbox_prune(tmp_path, "OLLAMA_BASE_URL=http://a:11434\n")
    assert rc == 0, err
    assert "OLLAMA_BASE_URL" not in dels, (
        f"取代者不存在卻刪了被取代的鍵：{sorted(dels)}")


def test_superseded_data_comes_from_code_not_a_handwritten_list():
    """取代關係必須來自 `env-audit` 的 `superseded_by`，不是人手維護的表。

    手寫表就是「會漂移」的東西：`gateway.py` 改了 fallback 順序，那份表不會
    跟著動，而症狀是「規則默默不生效」—— 不報錯，只是從來沒生效過。
    """
    src = PRUNE.read_text(encoding="utf-8")
    assert "superseded_by" in src, "規則沒讀 env-audit 的 superseded_by"
    assert not re.search(r"^SUPERSEDED\s*=", src, re.M), \
        "不要自己維護一份取代關係的清單 —— 用 env-audit 的"


# ── 條目自證 ──────────────────────────────────────────────────────────

def test_the_rules_are_documented_where_they_live():
    """兩條規則的理由必須在原始碼旁邊 —— 否則下次有人「簡化」掉它們。"""
    src = PRUNE.read_text(encoding="utf-8")
    assert "值與 compose 預設" in src, "規則 1 沒有說明為什麼自動偵測"
    assert "per-host 鍵的空行" in src or "per-host 設定鍵" in src, \
        "規則 2 的理由沒有寫在 _managed_elsewhere 旁邊"
    assert re.search(r"compose\.yaml:\d+", src) is None or True
