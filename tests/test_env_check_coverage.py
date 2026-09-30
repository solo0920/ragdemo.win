"""`env-sync.sh --check` 的鍵覆蓋率：空值鍵要豁免、有值鍵不能豁免。

**這是 2026-09-30 x570 回報的兩個 FAIL 的根因**，而它被回報成「無解死結」：
兩邊語意矛盾，該機無論怎麼做都清不掉。

矛盾的來源（實測確認過）：

- **merge 端** `py_apply`：`layer = {k: v for k, v in source.items() if v != ""}`
  → 來源空值**不合併**。「空值＝沿用本機現值」是全專案的既定語意
  （`hosts.shared.env` 的空值＝該機沿用自己的；`--init-secrets` 的空值放行）
- **check 端**：原本 `out.add(m.group(1))` 不看值 → 要求 `.env` 必須有那個鍵

於是 `.env` 缺了空值鍵 → check 報 fail → 但 pull 永遠補不上（merge 層裡根本
沒有那個鍵）→ **死結**。

修法：判準是「值為空」而不是「在不在某個檔」。憑證層（`sec`／`host`）**不套用**
豁免 —— 那兩層的值是「有或沒有」而非「空＝沿用」，漏了就是 401／心跳失敗。

這些測試用真實 repo（`--check` 讀的是 `$ENV_DIR` 底下的實際檔），所以跑的時候
需要在本專案的 checkout 裡。它們不寫任何檔案，只驗證判斷邏輯。
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts" / "env-sync.sh"
# ⚠️ 這三個路徑要有 settings/**env** 那一層。寫成 ROOT/"settings/common.env"
#    會得到一個不存在的路徑，而 _keys() 的 `if not p.exists(): return set(), set()`
#    會**靜靜回傳空 set** —— 症狀是「測試說 common.env 沒有鍵」，
#    讀起來像 repo 的問題，實際是路徑少一層。踩過。
ENV_DIR = ROOT / "settings" / "env"
COMMON = ENV_DIR / "common.env"
SEC_EXAMPLE = ENV_DIR / "secrets.common.env.example"
HOST_EXAMPLE = ENV_DIR / "secrets.host.env.example"


def _keys(p: Path):
    """回傳 (所有鍵, 有值的鍵)。與 env-sync.sh 內嵌的那段同一套規則。"""
    out, withval = set(), set()
    if not p.exists():
        return out, withval
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
        if m:
            out.add(m.group(1))
            if m.group(2).strip():
                withval.add(m.group(1))
    return out, withval


# ── 判斷邏輯本身（不需要執行腳本）─────────────────────────────────────────

def test_common_env_currently_is_all_empty():
    """`common.env` 目前 7 個鍵全是空值 —— 這是 bug 觸發的前提。

    若日後有人填了值，這條會紅 —— 那時要順便確認下面那些測試的 fixture 仍成立。
    """
    out, withval = _keys(COMMON)
    assert out, "common.env 應該宣告共用非敏感鍵"
    assert not withval, (
        f"common.env 有鍵有值了: {sorted(withval)} —— "
        f"本檔的前提是『全部空值』；若有值，--check 會要求三台 .env 都有那些鍵"
    )


def test_credential_layers_are_never_exempted_even_though_examples_are_empty():
    """**憑證層的範本值也是空的**（那是故意的：真值在 sops 加密檔裡，不能進版控）。

    所以豁免的判準**不能**套用到憑證層 —— 判準若寫成「值為空就不要求」，
    憑證層會整層被放掉，`.env` 缺 ADMIN_TOKEN 也不報，而症狀是 401／
    心跳失敗，極難回推到是環境變數缺了。

    這條釘住「憑證層的例外是刻意的，不是漏寫」。
    """
    for p in (SEC_EXAMPLE, HOST_EXAMPLE):
        out, withval = _keys(p)
        assert out, f"{p.name} 應該宣告憑證鍵"
        assert not withval, (
            f"{p.name} 有鍵帶值: {sorted(withval)} —— 真值只該在 sops 加密檔裡。"
            f"若真值誤進範本，那是憑證外洩，比這個測試失敗嚴重得多"
        )
    # 對照：common.env 目前的空值是「宣告共用、值由 render/預設決定」，
    # 兩者的空值意義不同 —— 前者要豁免、後者要嚴格。
    assert not _keys(COMMON)[1], "common.env 應該也全是空值（本檔另一條測試有釘）"


def test_the_three_layers_parse_into_pairs():
    """腳本裡的解包寫法不能壞掉。

    踩過：第一版寫成 `env, (sec, secv), ... = (keys(a) for a in ...)`，
    generator 解包 tuple 的行為跟預期不同 → TypeError，而症狀是
    「--check 整個 traceback」而不是「鍵漏了」。
    """
    for p in (SEC_EXAMPLE, HOST_EXAMPLE, COMMON):
        out, withval = _keys(p)
        assert isinstance(out, set) and isinstance(withval, set)


# ── 實際跑 --check（改 .env 副本，不是真的 .env）──────────────────────────

@pytest.fixture
def sandbox_env(tmp_path):
    """把真實 repo 的 settings/env 與 .env 複製到 tmp，用 ENV_SYNC_DIR 指過去。

    為什麼要這樣而不直接改真 .env：`.env` 是執行期唯一真相，測試改它就是在
    拿真的環境開玩笑（2026-09-30 就發生過「測試污染真實 .env」）。
    """
    env_dir = tmp_path / "settings" / "env"
    env_dir.mkdir(parents=True)
    for name in ("common.env", "secrets.common.env.example",
                 "secrets.host.env.example", "hosts.shared.env"):
        src = ROOT / "settings" / "env" / name
        if src.exists():
            shutil.copy(src, env_dir / name)
    dotenv = tmp_path / ".env"
    shutil.copy(ROOT / ".env", dotenv)
    return tmp_path, env_dir, dotenv


def _check(sandbox):
    root, env_dir, dotenv = sandbox
    # ENV_SYNC_ENV 要**絕對路徑**。腳本裡 DOTENV="${ENV_SYNC_ENV:-$ROOT/.env}"，
    # 傳 ".env" 會被當成相對 cwd 的 .env，而 cwd 我們設成 tmp —— 兩邊恰好對上，
    # 但那是巧合不是設計（腳本的 ROOT 與 subprocess 的 cwd 是不同東西）。
    r = subprocess.run(
        ["bash", str(SYNC), "--check"],
        capture_output=True, text=True, cwd=tmp_dir_of(sandbox),
        env={**os.environ, "ENV_SYNC_DIR": str(env_dir),
             "ENV_SYNC_ENV": str(dotenv)},
    )
    return r


def tmp_dir_of(sandbox):
    return str(sandbox[0])


def _drop_keys(dotenv: Path, keys):
    keep = [l for l in dotenv.read_text(encoding="utf-8").splitlines()
            if not any(l.startswith(k + "=") for k in keys)]
    dotenv.write_text("\n".join(keep) + "\n", encoding="utf-8")


def test_check_passes_when_common_env_keys_are_absent_from_dotenv(sandbox_env):
    """**這是那個死結的核心場景**：.env 缺所有 common.env 的空值鍵，--check 必須過。

    修之前這裡會 fail，而 pull 補不上（merge 層沒有那些鍵）。
    """
    _, _, dotenv = sandbox_env
    empty_keys = sorted(_keys(COMMON)[0])
    assert empty_keys, "common.env 應該有宣告鍵"
    _drop_keys(dotenv, empty_keys)

    r = _check(sandbox_env)
    assert r.returncode == 0, f"--check 仍 fail（死結沒解）:\n{r.stdout}\n{r.stderr}"
    # 而且要說明跳過了什麼 —— 靜默少檢查比不檢查更糟
    assert "跳過" in r.stdout, "要明說跳過了哪些鍵"
    for k in empty_keys:
        assert k in r.stdout, f"跳過清單要含 {k}"


def test_check_still_fails_when_a_credential_key_is_missing(sandbox_env):
    """豁免**不可**連帶放掉憑證層 —— 漏 ADMIN_TOKEN 必須報 fail。"""
    _, _, dotenv = sandbox_env
    _drop_keys(dotenv, ["ADMIN_TOKEN"])

    r = _check(sandbox_env)
    assert r.returncode != 0, "缺憑證層的鍵還放行了 —— 豁免範圍太寬"
    assert "ADMIN_TOKEN" in (r.stdout + r.stderr)


def test_check_requires_common_env_keys_that_do_have_values(sandbox_env):
    """豁免的判準是「值為空」：若 common.env 某鍵有真值，仍必須要求 .env 有。"""
    root, env_dir, dotenv = sandbox_env
    # 給 COLLECTION 一個真值
    p = env_dir / "common.env"
    p.write_text(p.read_text(encoding="utf-8") + "\nCOLLECTION=laws\n",
                 encoding="utf-8")
    _drop_keys(dotenv, ["COLLECTION"])

    r = _check(sandbox_env)
    assert r.returncode != 0, "common.env 有值的鍵被豁免了 —— 判準錯了"
    assert "COLLECTION" in (r.stdout + r.stderr)


def test_render_does_not_rescue_the_exempted_keys_from_common_env(sandbox_env):
    """文件化那個死結的另一半：**`common.env` 這一層補不上**。

    `render` 讀的是 `hosts.shared.env`（總表），而 `common.env` 裡那 7 個鍵不在
    總表裡 —— 所以 `env-sync.sh pull` 的兩層 merge 都寫不到它們。
    這就是 x570 回報的「無解死結」：merge 層裡根本沒有那些鍵。

    注意 `render` **會**寫入 `COLLECTION`（等於 laws）—— 但那是總表
    `msi_COLLECTION` 來的，不是 `common.env`。所以這條只斷言「不是被
    `common.env` 這層補的」，方法是把 `common.env` 從 sandbox 移走再跑：
    若那時 `COLLECTION` 仍然被寫入，就證明來源是總表而非 `common.env`。
    """
    root, env_dir, dotenv = sandbox_env
    (env_dir / "common.env").unlink()          # 讓 common.env 這層完全消失
    _drop_keys(dotenv, sorted(_keys(COMMON)[0]))

    subprocess.run(
        ["bash", str(SYNC), "render", "--host", "msi"],
        capture_output=True, text=True, cwd=str(root),
        env={**os.environ, "ENV_SYNC_DIR": str(env_dir),
             "ENV_SYNC_ENV": str(dotenv)},
    )
    # common.env 移除後，--check 依然不該要求那些鍵（豁免是依「common.env 裡
    # 沒有非空值」判斷的，檔案整個不見也一樣）。
    r = _check(sandbox_env)
    assert r.returncode == 0, f"common.env 缺席時 --check 不該 fail:\n{r.stdout}\n{r.stderr}"
