"""`scripts/env-sync.sh` 裡的 **heredoc Python 區塊**必須是合法 Python。

## 為什麼需要這個測試

`bash -n` **不檢查 heredoc 的內容** —— 它只看 shell 結構。所以：

    <<'PY'
    else:
        print("…")
        sys.stdout.flush()      # ← 縮排錯，IndentationError
    PY

`bash -n` 回 0（綠燈），CI 全過，而 `env-sync.sh --check` 在**任何機器上**
都會噴 `IndentationError: unexpected indent`。

2026-10-02 實測踩到：我用腳本把 SIGPIPE 守衛插進 7 個 heredoc，縮排取自
「最後一行」的縮排，於是插到了 `for`／`else` 區塊裡面。`bash -n` 說沒問題，
CI 也綠，只有真的跑 `env-sync.sh` 才炸 —— 而我第一時間是去懷疑插入邏輯，
不是懷疑語法檢查不足。

**判準是「這個測試的存在理由」**：shell 腳本裡的內嵌語言，只有「實際執行」
或「獨立解析」才驗得到。`bash -n` 對 shell 足夠，對 heredoc **不夠**。
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_SYNC = ROOT / "scripts" / "env-sync.sh"

HEREDOC = re.compile(r"<<'PY'")


def _py_blocks() -> list[tuple[int, str]]:
    """回傳 [(heredoc 起始行號, Python 原始碼)]。"""
    lines = ENV_SYNC.read_text(encoding="utf-8").splitlines()
    out: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        if "python3" in lines[i]:
            j = i
            while j < len(lines) and HEREDOC.search(lines[j]) is None:
                j += 1
            if j < len(lines):
                k = j + 1
                while k < len(lines) and lines[k].strip() != "PY":
                    k += 1
                out.append((j + 2, "\n".join(lines[j + 1:k])))
                i = k
        i += 1
    return out


def test_there_are_python_blocks_to_check():
    """先確認掃描器找得到東西 —— 否則下面那條是**假綠**。

    一條「驗證 heredoc」的測試，如果掃描器壞掉而回傳空清單，就會靜靜地
    全部通過。所以掃描器本身要有測試。
    """
    blocks = _py_blocks()
    assert len(blocks) >= 5, f"只找到 {len(blocks)} 個 heredoc Python 區塊"
    assert any("print(" in src for _, src in blocks), \
        "找到的區塊裡沒有 print —— 掃描邏輯可能抓錯位置"


def test_heredoc_python_blocks_are_valid():
    """每個 heredoc 的 Python 必須能 parse —— **`bash -n` 查不到這件事**。

    Friction 點（2026-10-02 實測）：用腳本把 SIGPIPE 守衛插進 7 個 heredoc，
    縮排取自「最後一行」，於是插到 `for`／`else` 區塊裡面 →
    `IndentationError: unexpected indent`，而 `bash -n` 回 0、CI 綠。
    只有真的執行 `env-sync.sh` 才會炸。

    錯誤訊息要帶行號 —— 內嵌區塊沒有檔案名可用，所以用 heredoc 的行號定位。
    """
    bad = []
    for start, src in _py_blocks():
        try:
            ast.parse(src)
        except SyntaxError as e:
            line = start + (e.lineno or 1) - 1
            bad.append(f"{ENV_SYNC.relative_to(ROOT)}:{line}  {e.msg}")
    assert not bad, (
        "heredoc 裡的 Python 語法錯誤（`bash -n` 查不到，只有這裡抓得到）:\n  "
        + "\n  ".join(bad))


def test_every_heredoc_block_has_a_sigpipe_guard():
    """每個會印東西的 heredoc 都要有 SIGPIPE 守衛。

    `env-sync --check | head -1` 是**本專案印指紋的標準驗收寫法**（兩份
    handoff 都是這麼寫的）。沒有守衛的話，mbp／x570 使用者每次跑都會看到：

        env-sync --check: 版面 33 keys, layout sha12=152822bac747（…）
        Exception ignored in: <_io.TextIOWrapper name='<stdout>' ...>
        BrokenPipeError: [Errno 32] Broken pipe

    指紋完全正確，底下卻跟一段紅字 —— **看起來像指令壞了**，而它沒壞。
    在規格驗收那一步加這種噪音，代價是讓人以為自己弄壞了什麼。
    """
    missing = []
    for start, src in _py_blocks():
        if "print(" not in src:
            continue
        if "SIGPIPE" not in src:
            missing.append(str(start))
    assert not missing, (
        f"這些 heredoc 會 print 但沒有 SIGPIPE 守衛（起始行 {missing}）—— "
        f"`| head -1` 會讓它們噴 BrokenPipeError traceback")


def test_bash_n_passes_too():
    """`bash -n` 仍然要過 —— 它驗的是 shell 結構，與 heredoc 互補。"""
    r = subprocess.run(["bash", "-n", str(ENV_SYNC)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_check_output_is_clean_when_piped_to_head():
    """端到端：`--check | head -1` 的 stderr 必須是空的。

    前幾條都是靜態檢查；這條實際跑一次，證明守衛真的有效。
    ⚠️ 必須**照 mbp 的原樣**跑（`| head -1`，不 merge stderr）—— 之前我測成
    `2>&1 | head -1` 就沒重現。
    """
    r = subprocess.run(
        f"bash {ENV_SYNC} --check | head -1",
        shell=True, capture_output=True, text=True, cwd=ROOT)
    assert "layout sha12=" in r.stdout, r.stdout
    assert "BrokenPipe" not in r.stderr, (
        f"`--check | head -1` 的 stderr 不乾淨：\n{r.stderr}")
    assert "Traceback" not in r.stderr, (
        f"`--check | head -1` 的 stderr 有 traceback：\n{r.stderr}")
