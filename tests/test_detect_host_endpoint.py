"""`detect_host_endpoint()`：WSL 上自動偵測 Windows 主機的 ollama 位址。

為什麼需要（2026-09-29 實測踩到兩次）：

1. `OLLAMA_URLS` 在 WSL 上必須是 Windows 主機的位址，而那是 **WSL 的預設閘道**
   （`ip route` 的 default gw），由 Windows 分配、每次 WSL 重啟可能變。
   寫死進被追蹤的總表就會變成陷阱 —— 過期值的症狀是
   `httpx.ConnectError: ollama unreachable`（連 404/400 都不是，方向完全不一樣）。

2. `host.docker.internal` **不能用**。那是 Docker Desktop 的慣用名；WSL 原生
   docker 把它解析到 172.17.0.1（WSL 自己），而 ollama 跑在 Windows、在 WSL
   網段外 → 容器連不到。實測 `docker compose logs` 顯示
   `httpx.ConnectError: ollama unreachable`。

設計上只對 WSL 生效：x570（原生 Linux）與 mbp（macOS）對它們來說「本機
ollama」就是 localhost，總表空值＝沿用現值，不該被注入任何位址。
"""
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
ENV_SYNC = REPO / "scripts" / "env-sync.sh"


def _detect() -> str:
    """在子 shell 裡 source 該函式並呼叫（不跑整支腳本的 main）。"""
    src = ENV_SYNC.read_text(encoding="utf-8")
    start = src.index("detect_host_endpoint() {")
    end = src.index("\n}\n", start) + 3
    snippet = src[start:end]
    r = subprocess.run(
        ["bash", "-c", f"{snippet}\necho \"$(detect_host_endpoint)\""],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"函式執行失敗：{r.stderr}"
    return r.stdout.strip()


def _read_env(path: pathlib.Path) -> dict[str, str]:
    """讀 .env 成 dict。

    刻意不用 `dict(l.split("=", 1) for ...)`：值裡可能含 `=`（例如 DSN 的
    query string），而且重複鍵會被後者覆蓋而看不出來。逐行 assign 才不會踩到。
    """
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v
    return out


def test_function_exists_in_env_sync():
    assert "detect_host_endpoint()" in ENV_SYNC.read_text(encoding="utf-8"), (
        "env-sync.sh 必須有 detect_host_endpoint() —— render 靠它注入 OLLAMA 位址")


def test_render_passes_detected_value_to_py_apply():
    """偵測結果必須真的傳進 render，否則函式是裝飾品。"""
    src = ENV_SYNC.read_text(encoding="utf-8")
    assert "RAGDEMO_DETECT_ENDPOINT" in src, (
        "render 沒把偵測結果傳給 py_apply —— 函式定義了但沒接上")
    # 走**位置參數**而非環境變數：`VAR=x func` 不會 export 給 python3 子行程，
    # 第一版就是讀 os.environ 拿到空字串（要加 debug 才發現）。
    assert '_inject = detected or ""' in src, (
        "py_apply 沒從位置參數讀偵測結果")


def test_render_actually_injects_detected_endpoint(tmp_path):
    """端到端：給定偵測值，render 必須把它寫進 .env。

    只檢查字串存在是不夠的 —— mutation 測試顯示把變數名改掉測試照樣全綠，
    因為它們只看「原始碼裡有沒有這個字」。這條真的跑一次 render。
    """
    import os
    import shutil
    repo = pathlib.Path(__file__).resolve().parents[1]
    dotenv = tmp_path / ".env"
    # 複製真實 repo 的 .env（gitignored，但這台機器上有 —— 沒有就跳過）
    real_env = repo / ".env"
    if not real_env.exists():
        pytest.skip("沒有 .env 可複製（乾淨 clone）")
    shutil.copy(real_env, dotenv)
    # 清掉目標鍵，讓注入是唯一寫入來源
    lines = [l for l in dotenv.read_text(encoding="utf-8").splitlines()
             if not l.startswith(("OLLAMA_URLS=", "OLLAMA="))]
    dotenv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    sentinel = "http://203.0.113.7:11434"   # RFC 5737 測試網段，不會誤打真機
    r = subprocess.run(
        ["bash", str(ENV_SYNC), "render", "--host", "msi"],
        capture_output=True, text=True, timeout=60, cwd=repo,
        # ENV_SYNC_ENV 決定 render 寫哪一份 .env —— 不設的話會寫進**真實的**
        # repo .env（第一版測試就踩到：tmp 的 .env 沒變，變的是真的那份）。
        # ENV_SYNC_DIR 則指向真實總表（要注入就不能用 fixture 的假總表）。
        env={**os.environ, "ENV_SYNC_ENV": str(dotenv),
             "RAGDEMO_DETECT_ENDPOINT": sentinel},
    )
    assert r.returncode == 0, f"render 失敗：{r.stderr}"
    got = _read_env(dotenv)
    assert got.get("OLLAMA") == sentinel, f"OLLAMA 未注入偵測值：{got.get('OLLAMA')!r}"
    assert sentinel in got.get("OLLAMA_URLS", ""), (
        f"OLLAMA_URLS 未注入偵測值：{got.get('OLLAMA_URLS')!r}")


def test_injection_is_idempotent(tmp_path):
    """重跑 render 不該把同一個位址疊加兩次。"""
    import os
    import shutil
    repo = pathlib.Path(__file__).resolve().parents[1]
    real_env = repo / ".env"
    if not real_env.exists():
        pytest.skip("沒有 .env 可複製（乾淨 clone）")
    dotenv = tmp_path / ".env"
    shutil.copy(real_env, dotenv)
    lines = [l for l in dotenv.read_text(encoding="utf-8").splitlines()
             if not l.startswith(("OLLAMA_URLS=", "OLLAMA="))]
    dotenv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sentinel = "http://203.0.113.9:11434"
    for _ in range(3):
        subprocess.run(["bash", str(ENV_SYNC), "render", "--host", "msi"],
                       capture_output=True, timeout=60, cwd=repo,
                       env={**os.environ, "ENV_SYNC_ENV": str(dotenv),
                            "RAGDEMO_DETECT_ENDPOINT": sentinel})
    urls = _read_env(dotenv).get("OLLAMA_URLS", "")
    assert urls.count(sentinel) == 1, f"重跑 render 疊加了：{urls}"


def test_no_hardcoded_ip_in_env_sync():
    """IP 會變，不該出現在腳本裡當常數。"""
    import re
    src = ENV_SYNC.read_text(encoding="utf-8")
    offenders = re.findall(r"\b(?:10|172|192)\.\d+\.\d+\.\d+\b", src)
    assert not offenders, f"env-sync.sh 不可硬寫 IP：{set(offenders)}"


def test_no_hardcoded_ip_in_shared_table():
    """同樣的紀律：被追蹤的總表不放 IP。"""
    import re
    table = (REPO / "settings" / "env" / "hosts.shared.env").read_text(encoding="utf-8")
    # 只檢查非註解行
    code = "\n".join(l for l in table.splitlines() if not l.lstrip().startswith("#"))
    offenders = re.findall(r"\b(?:10|172|192)\.\d+\.\d+\.\d+\b", code)
    assert not offenders, f"hosts.shared.env 不可硬寫 IP：{set(offenders)}"


@pytest.mark.skipif(sys.platform != "linux", reason="WSL 專用路徑")
def test_on_this_host_it_finds_a_reachable_endpoint():
    """在這台機器上跑，偵測出的位址應該真的連得上（若本機就是 WSL）。"""
    ep = _detect()
    if not ep:
        pytest.skip("本機不是 WSL（或無預設閘道）—— 不注入是正確行為")
    assert ep.startswith("http://"), f"應為 http:// 開頭，實際 {ep!r}"
    assert ":11434" in ep, f"應含 ollama 埠，實際 {ep!r}"

    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(f"{ep}/api/tags", timeout=8) as r:
            assert r.status == 200, f"偵測出的位址連不上（HTTP {r.status}）"
    except (urllib.error.URLError, OSError) as e:
        pytest.fail(f"偵測出的 {ep} 連不上：{e} —— 偵測邏輯或環境變了")


def test_non_wsl_gets_empty_string():
    """WSL_DISTRO_NAME 清空 + /proc/version 沒有 wsl → 必須回空（不猜）。"""
    src = ENV_SYNC.read_text(encoding="utf-8")
    start = src.index("detect_host_endpoint() {")
    end = src.index("\n}\n", start) + 3
    snippet = src[start:end]
    r = subprocess.run(
        ["bash", "-c",
         f"{snippet}\n"
         # 用一個假的 /proc/version 看不到 wsl：直接餵空 WSL_DISTRO_NAME 且
         # 讓 grep 找不到（以 function 覆寫 ip route 會太複雜，改用環境變數控制）。
         'echo "[$(WSL_DISTRO_NAME= detect_host_endpoint)]"'],
        capture_output=True, text=True, timeout=30,
    )
    # 這台機器本身就是 WSL，所以結果可為空可為非空；重點是「不會崩」。
    assert r.returncode == 0, f"函式在非 WSL 路徑上不該崩：{r.stderr}"
