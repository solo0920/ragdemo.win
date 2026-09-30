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
