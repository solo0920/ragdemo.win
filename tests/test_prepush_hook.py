"""`pre-push` 必須包含四道強制檢查 —— 少一道就有「CI 紅了但沒人知道」的缺口。

**這支檔的存在理由**：2026-09-30 發現 CI 的 `compose config` 從 09-27 起
連續紅了 10 次 push 而沒有人發現。原因是那個 job 缺 `HOST_ID`，而**本機
pre-push 沒有同一道檢查** —— 所以按 push 的那一側全程看不見，只能靠信箱。

CI 是「三機紀律的公開證據」，而一直紅的 CI 等於沒有 CI：大家看本機
pre-push 綠就把信寄出去。所以同一道檢查必須**兩邊都有**。

這些測試是靜態檢查 hook 的內容 —— 不執行 hook（那會真的 push），而是確認
它「有做那些事」。那比執行可靠：hook 的行為本來就靠 pre-push 自己在每次 push
時驗證，而「某道檢查**存在**」這件事沒有任何執行點能證明。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".githooks" / "pre-push"


def _hook() -> str:
    assert HOOK.is_file(), f"{HOOK} 不見了 —— 那表示三台機器都沒有 push 檢查"
    return HOOK.read_text(encoding="utf-8")


def test_hook_is_executable():
    """hook 必須可執行，否則 git 會靜靜略過它（`core.hooksPath` 指到這裡）。"""
    assert HOOK.stat().st_mode & 0o111, "pre-push 沒有執行位元"


def test_hook_has_bash_shebang_and_strict_mode():
    head = _hook().splitlines()[:20]
    assert head[0].startswith("#!"), "第一行必須是 shebang"
    assert "bash" in head[0], f"shebang 應指向 bash: {head[0]}"
    assert any("set -euo pipefail" in l for l in head), (
        "缺 `set -euo pipefail` —— hook 裡的檢查會在第一個失敗的指令就停下，"
        "但不會把非零碼往外傳（那樣 push 還是會成功）"
    )


# ── 四道強制檢查 ─────────────────────────────────────────────────────────

REQUIRED_CHECKS = {
    "backend Python 語法": r"ast\.parse",
    "shell 語法": r"bash -n",
    "IP 準則（LAN_IP=）": r"LAN_IP=",
    "pytest": r"\.venv/bin/pytest",
    "compose 插值": r"docker compose config -q",
}


def test_hook_runs_every_mandatory_check():
    """四道強制檢查都要在 hook 裡 —— 這是本檔的核心主張。

    少一道就是一個「按 push 的人看不到、只有信箱知道」的缺口。2026-09-27
    缺的就是 compose 那道。
    """
    text = _hook()
    missing = [name for name, pat in REQUIRED_CHECKS.items()
               if not re.search(pat, text)]
    assert not missing, (
        f"pre-push 少了這些檢查: {missing} —— "
        f"它們是本機側的唯一防線（CI 只在 push 之後才知道，且可能沒人看）"
    )


def test_compose_check_fails_the_push_not_just_warns():
    """compose 檢查失敗必須 `exit 1`，不能只是印個警告。

    靜默降級成警告 = 這道檢查不存在。要擋就擋，否則就別加。
    """
    text = _hook()
    i = text.index("docker compose config -q")
    # 從這裡往後到下一段標題，找有沒有 exit 1
    j = text.find("# 5)", i)
    if j == -1:
        j = len(text)
    seg = text[i:j]
    assert "exit 1" in seg, "compose 檢查失敗時沒有 exit 1 —— 這道檢查等於沒有"


def test_compose_check_skips_cleanly_when_docker_absent():
    """沒裝 docker 的機器要跳過，不是誤擋。

    跳過而不是擋：這三台都裝了 docker，但 hook 是 per-clone 設定、會被複製到
    別的機器（例如乾淨 clone 來驗證）。擋住會讓那種情況完全不能用。
    """
    text = _hook()
    assert re.search(r"if command -v docker", text), (
        "compose 檢查沒有 `command -v docker` 的守衛 —— "
        "沒裝 docker 的機器會被擋下"
    )


def test_compose_check_uses_quiet_flag():
    """**必須用 `-q`**（只驗證不印出展開後的設定）。

    沒有 `-q` 時 `docker compose config` 會把整份展開後的設定印到 stdout，
    裡面包含 `POSTGRES_PASSWORD` 明文。而 hook 的輸出會直接進 CI log、
    進 shell scrollback、進使用者的終端記錄。
    2026-09-30 實測：`-q` 的失敗訊息裡只出現變數名與格式錯誤
    （`invalid IP address: …`），**32 字元的真實密碼出現 0 次**。
    """
    text = _hook()
    # 只比對「真的在執行」的呼叫（`if out="$(docker compose ...)"`），
    # 不要匹配到錯誤訊息裡那個字串 —— `echo "✗ docker compose config 失敗"`
    # 裡也有這五個字，但它不是執行（第一版就踩到這個：negation lookahead
    # 跨過了換行才發現已經太晚）。
    for m in re.finditer(r'\$\(\s*(docker compose [a-z]+(?: -q)?)', text):
        call = m.group(1)
        assert call.endswith(" -q"), (
            f"實際執行的呼叫沒帶 -q: {call!r} —— "
            f"沒有 -q 會把展開後的設定（含明文密碼）印出來"
        )
    # 至少要有一個真的呼叫
    assert re.search(r'\$\(\s*docker compose', text), "沒找到 docker compose 的執行呼叫"


# ── 可選項不該變成強制 ───────────────────────────────────────────────────

def test_http_smoke_stays_optional():
    """完整 HTTP smoke 必須維持選用（`RAGDEMO_SMOKE=1` 才跑）。

    它需要本機 api 在線且 LLM 可達，較慢且會被離線擋住 —— 設成強制的話，
    機台 api 沒起來的開發者就完全不能 push，那是比 CI 紅更難接受的處置。
    """
    text = _hook()
    assert 'RAGDEMO_SMOKE:-0' in text or 'RAGDEMO_SMOKE:-' in text, (
        "HTTP smoke 沒有保留 RAGDEMO_SMOKE 的開關 —— 它必須是選用的"
    )


def test_hook_still_reports_each_check_individually():
    """每一道檢查都要有自己的 ✓/✗ 訊息。

    全部擠成一句「all passed」的話，壞掉時使用者看不出是哪一道。
    """
    text = _hook()
    ticks = len(re.findall(r'echo "[✓✗⚠]', text))
    assert ticks >= len(REQUIRED_CHECKS), (
        f"只找到 {ticks} 個狀態訊息，少於 {len(REQUIRED_CHECKS)} 道檢查 —— "
        f"使用者會看不出是哪一道壞了"
    )


# ── 行為測試：守衛真的會擋嗎 ────────────────────────────────────────────
# 上面那些是**靜態**檢查（確認 hook「有做那些事」）。但它們看不見一类 bug：
# **檢查存在、卻不管用**。2026-09-30 的 LAN_IP 守衛就是那類 ——
#   `git grep ... | grep -q .` 在 `pipefail` 下會被 SIGPIPE 弄成 141 而靜默失效。
# 而任何只放一兩個違規檔的測試**抓不到**它（少量時 git grep 來得及寫完、回 0）。
# 所以這組必須真的把守衛跑起來，而且違規檔要**夠多**。
#
# ⚠️ 為什麼在 tmp 裡跑而不是真的 push：抽出的是守衛那一段，不含 `git push`。
#    這是執行行為，不是執行整個 hook。

import shutil          # noqa: E402
import subprocess      # noqa: E402

import pytest          # noqa: E402

# 少於這個數量守衛「恰好會通過」，抓不到 bug。實測：3 檔 → 舊版也擋。
MANY = 400
FEW = 3


def _guard_block() -> str:
    """抽出 LAN_IP 守衛那一段（含 shebang 需要的 set -euo pipefail）。"""
    src = _hook()
    start = src.index("# 2) IP 準則")
    end = src.index("✓ 無 LAN_IP= 殘留", start)
    end = src.index("\n", end)
    return "set -euo pipefail\n" + src[start:end]


def _run_guard(tmp_path: Path, n_files: int) -> int:
    """在 tmp git repo 裡放 n 個含 LAN_IP= 的追蹤檔，跑守衛，回 exit code。"""
    repo = tmp_path / f"repo{n_files}"
    repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, capture_output=True, text=True)
    run("git", "init", "-q", ".")
    for i in range(n_files):
        (repo / f"f{i}.env").write_text(f"LAN_IP=10.0.0.{i}\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x")
    script = tmp_path / f"guard{n_files}.sh"
    script.write_text(_guard_block(), encoding="utf-8")
    return subprocess.run(["bash", str(script)], cwd=repo,
                          capture_output=True, text=True).returncode


@pytest.mark.parametrize("n", [MANY, FEW])
def test_lan_ip_guard_blocks_at_every_scale(tmp_path, n):
    """守衛在少量與大量違規下都必須擋。

    `MANY` 那個 case 才是有意義的：舊版（`| grep -q .`）在 FEW 會通過、
    在 MANY 會被繞過，所以只測 FEW 等於測不到 bug。
    """
    assert _run_guard(tmp_path, n) != 0, f"{n} 個違規檔居然放行了"


def test_lan_ip_guard_allows_a_clean_repo(tmp_path):
    """沒有違規時必須放行 —— 否則這道守衛會擋掉所有正常 push。"""
    repo = tmp_path / "clean"
    repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, capture_output=True, text=True)
    run("git", "init", "-q", ".")
    (repo / "ok.env").write_text("TS_IP=\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x")
    script = tmp_path / "clean.sh"
    script.write_text(_guard_block(), encoding="utf-8")
    assert subprocess.run(["bash", str(script)], cwd=repo,
                          capture_output=True, text=True).returncode == 0


# ── pytest 環境壞掉時的診斷（2026-10-02）──────────────────────────────
#
# 為什麼要測這個：2026-10-02 repo 從 `ragdemo` 改名成 `ragdemo.win` 之後，
# `.venv/bin/*` 的 shebang 全部指向舊絕對路徑 → `bad interpreter`。
# 症狀與「沒裝 pytest」**完全不同**，但第一版 hook 只印「pytest 失敗」，
# 診斷方向指向「測試壞了」，實際上是環境壞了 —— 走錯方向會去改 tests/。
#
# 這裡**執行**那一段（靜態比對抓不到分支寫錯，而分支寫錯的症狀是
# 「正確的診斷訊息從來沒出現過」）。抽出 hook 裡那一段、指向一個假的
# 壞掉的 pytest，不碰真實 .venv。

def _pytest_block() -> str:
    """抽出 hook 裡「跑 pytest」那一段，含 `if [ -x ... ]; then` 到對應的 fi。"""
    text = _hook()
    start = text.index("if [ -x .venv/bin/pytest ]; then")
    # 這段的 fi 是第一個「行首就是 fi」的行（內層 case 的 fi 有縮排）
    end = text.index("\nfi\n", start) + len("\nfi\n")
    return text[start:end]


def _run_pytest_block(tmp_path, stderr_text, exit_code=127):
    fake = tmp_path / ".venv" / "bin"
    fake.mkdir(parents=True)
    p = fake / "pytest"
    p.write_text(f"#!/bin/sh\necho {stderr_text!r} >&2\nexit {exit_code}\n",
                 encoding="utf-8")
    p.chmod(0o755)
    script = tmp_path / "seg.sh"
    script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n"
                      + _pytest_block(), encoding="utf-8")
    return subprocess.run(["bash", str(script)], cwd=tmp_path,
                          capture_output=True, text=True)


def test_broken_venv_is_diagnosed_as_environment_not_tests(tmp_path):
    """`bad interpreter` 要診斷成「venv 壞掉」並給出**有效的**修法。

    關鍵在於「無效的修法」：只印 `uv sync --dev` 是錯的 —— 實測它回
    `Checked 19 packages` 而什麼都沒修（pyvenv.cfg 的 home 是可攜的，
    uv 判定 venv 已是最新）。所以訊息必須明說要 `rm -rf .venv` 重建。
    """
    r = _run_pytest_block(tmp_path, "bad interpreter: /old/path/.venv/bin/python: no such file")
    assert r.returncode != 0, "venv 壞掉必須擋下 push，不該靜默放行"
    assert "rm -rf .venv" in r.stderr, "沒給重建 venv 的修法（uv sync --dev 對這個無效）"
    assert "uv sync --dev" in r.stderr
    assert "環境" in r.stderr, "要說明是環境壞掉，不是測試失敗"


def test_broken_venv_message_says_uv_sync_alone_will_not_fix_it(tmp_path):
    """訊息要主動否決 `uv sync --dev`，否則人會照著跑一次、得到同樣的壞 venv。

    這是本條最容易被「簡化掉」的地方：訊息裡同時出現 `uv sync --dev`
    和 `rm -rf .venv` 是自相矛盾的，只看第一個的人會跑錯的那個。
    """
    r = _run_pytest_block(tmp_path, "bad interpreter: /old/.venv/bin/python: no such file")
    joined = r.stderr.replace(" ", "")
    assert "修不好" in joined or "無效" in joined, (
        "要明說 uv sync --dev 對這個情況修不好"
    )


def test_missing_pytest_is_distinguished_from_broken_venv(tmp_path):
    """`No module named pytest` 是「沒裝」，修法不同，不可混為一談。

    沒裝 → `uv sync --dev` 就好（venv 本身是好的）。
    路徑過期 → 要重建。
    兩者共用同一句「環境壞掉，請跑 X」會讓其中一個人照著跑錯的修法。
    """
    r = _run_pytest_block(tmp_path, "No module named pytest")
    assert r.returncode != 0
    assert "uv sync --dev" in r.stderr
    assert "rm -rf .venv" not in r.stderr, (
        "沒裝 pytest 時叫人去刪 venv 是錯的 —— venv 本身是好的"
    )
