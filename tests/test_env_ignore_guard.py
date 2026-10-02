"""**任何 `.env.<something>` 檔名都必須被 gitignore 擋住。**

⚠️ 2026-10-03 實測：輪換 `QDRANT_PEER_API_KEY` 時，runbook 裡備份 `.env` 用的是
`.env.rotbak3`。`.gitignore` 當時有：

    .env
    .env.bak
    .env.old
    .env.orig
    .env.*.bak

**`git status` 把 `.env.rotbak3` 列成 `??`** —— 而那個檔案是**全明文的舊憑證**
（就是 2026-10-03 被外洩的那把 qdrant key）。`git add -A` 會把它 commit 進版控。

**這與 2026-10-01 修過的問題完全同型**（該處註解明寫「上一行是精確比對，只擋
`.env` 本身…`git add -A` 就會把它 commit 進去」），只是換了一個檔名。

所以**真正的教訓不是「再加一個檔名」**：任何新造出來的 `.env.<something>` 都可能
從這個縫穿過去，而那條縫只有在有人不小心 `git add -A` 的時候才會造成傷害 ——
**症狀是事後才發現的版控洩漏**。

這條測試因此**不手維護清單**，而是**反查 repo 裡真的出現過的檔名**，逐一驗證。
新增一種命名而忘了加規則 → 這裡紅。

`.env.example` 是**必須追蹤**的（`env-audit --template` 的產物，有測試鎖定），
所以它是唯一的例外。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 必須被追蹤的（否則 env-audit 的 --template 產物會消失）
MUST_BE_TRACKED = {".env.example"}

# 掃得到、但**確實不需要**被 gitignore 的 —— 每一筆都要寫理由，否則這條測試
# 就變成「永遠紅」，而「永遠紅」的測試會被改成 skip，那又回到原點。
ALLOWED_UNIGNORED = {
    ".env.age": r"backup-env.sh 寫到 OUT_DIR（預設 $HOME/ragdemo-backup，**在 repo 外**），"
              "而且內容是 **sops 加密後**的密文，不是明文。（若用 --out 指到 repo 內，"
              "那要另外擋 —— 但那是呼叫端傳錯路徑，不是預設行為。）",
}

# 只認「看起來像備份／暫存檔名」的：`.env.` + 正規檔名（允許 glob 星號，
# 因為 .gitignore 裡本來就有 `.env.*.bak` 這種規則）
NAME_RE = re.compile(r"\.env\.[A-Za-z0-9][A-Za-z0-9._*-]{0,24}")

SCAN_DIRS = ("scripts", "ingest", "backend/app", "frontend/src", ".githooks")

# ⚠️ 這些目錄**必須排除**，否則掃描會被 vendored 內容淹沒 —— 第一版沒排它，
# 結果從 `backend/.venv/.../pathlib.py` 掃出 `.env.310`、從 starlette 掃出
# `.env.get_template`。那不是「守衛抓到的問題」，是**守衛自己壞掉**：
# 一次誤報就會讓人開始忽略它，而那正是它唯一不能被忽略的時候。
SKIP_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".git",
             ".svelte-kit", "data", ".pytest_cache", "build", "dist"}

# 這些檔案裡必然出現「教學用的」`.env.*` 字樣（測試自己的 docstring、runbook 的
# 說明文字）。把它們算進來等於**永遠紅**，而「永遠紅」的測試會被改成 skip ——
# 那就回到原點：壞掉的東西看起來像有覆蓋。
SKIP_FILES = {"test_env_ignore_guard.py"}


def _iter_source_files():
    # ⚠️ **必須包含 `settings/env/`** —— 2026-10-03 實測：runbook
    #   `settings/env/QDRANT-KEY-ROTATION-RUNBOOK.md` 就是**發明** `.env.rotbak3`
    #   這個檔名的地方，而第一版的掃描範圍只有 repo 根的 `*.md`，所以那條規則
    #   **從來沒被驗過**（拿掉它測試照樣綠）。
    #
    #   教訓：反查類的守衛，**掃描範圍要包含「會產生新命名的地方」**，否則它
    #   驗證的是「已知的已知」，對真正的新命名完全無感。
    yield from sorted(ROOT.glob("*.md"))
    for extra in ("settings/env", "settings"):
        d = ROOT / extra
        if d.is_dir():
            yield from sorted(d.glob("*.md"))
    yield from sorted(ROOT.glob("*.sh"))
    for d in SCAN_DIRS:
        p = ROOT / d
        if not p.is_dir():
            continue
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            if any(part in SKIP_DIRS for part in f.relative_to(ROOT).parts):
                continue
            if f.suffix in {".sh", ".py", ".ts", ".yml", ".yaml"} or f.name == "Dockerfile":
                yield f


def _discovered_names() -> set[str]:
    """從 repo 的腳本與文件中反查所有出現過的 `.env.<something>`。"""
    found: set[str] = set()
    for f in _iter_source_files():
        if f.name in SKIP_FILES:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in NAME_RE.finditer(text):
            found.add(m.group(0).rstrip(".,;:`'\""))
    return found


def _is_ignored(name: str) -> bool:
    if shutil.which("git") is None:
        pytest.skip("沒有 git")
    # 用一個真的檔名去問 git check-ignore（比讀 .gitignore 可靠：
    # 它會把「後面被 ! 取消」之類的規則也算進來）
    #
    # ⚠️ glob（例如 .gitignore 裡的 `.env.*.bak`）**不是檔名**，不能直接問 ——
    #   要驗的是「**這個形態**會不會被擋」，所以換成一個同形態的具體實例。
    probe_name = name.replace("*", "probe")
    probe = ROOT / probe_name
    existed = probe.exists()
    try:
        probe.touch(exist_ok=True)
        r = subprocess.run(["git", "check-ignore", "-q", probe_name],
                           cwd=ROOT, capture_output=True, timeout=60)
        return r.returncode == 0
    finally:
        if not existed:
            probe.unlink(missing_ok=True)


def test_every_env_variant_named_in_the_repo_is_gitignored():
    """**repo 裡出現過的每個 `.env.<something>` 都要被 gitignore 擋住。**

    這一條抓到的是 2026-10-03 的實際問題：`.env.rotbak3`（含明文舊憑證）會被
    `git add -A` commit 進版控，而症狀要等到推上去才發現。
    """
    names = _discovered_names()
    assert names, "一個 `.env.*` 檔名都沒掃到 —— 掃描範圍寫錯了，這條測試會變成假綠"

    # 允許清單裡的每一筆都必須有理由 —— 「沒有理由的例外」就是這個缺陷的形狀
    for k, why in ALLOWED_UNIGNORED.items():
        assert why.strip(), f"{k} 被列為例外但沒寫理由 —— 理由就是依據"

    unguarded = sorted(n for n in names
                       if n not in MUST_BE_TRACKED
                       and n not in ALLOWED_UNIGNORED
                       and not _is_ignored(n))
    assert not unguarded, (
        "這些 `.env.*` 檔名在 repo 裡被用到，卻**沒有被 gitignore 擋住** —— "
        "若它們是 `.env` 的備份／暫存，內容是全明文憑證，而 `git add -A` 會把它們\n"
        "  commit 進版控：\n    "
        + "\n    ".join(unguarded)
        + "\n\n  修法：在 .gitignore 加對應規則（注意不要用 `.env*`，那會連 "
          "`.env.example` 一起擋掉）。\n"
          "  然後把真的備份檔刪掉 —— 留在工作樹裡它本身就是風險。")


def test_env_example_is_not_gitignored():
    """**`.env.example` 必須仍可追蹤。**

    它是 `env-audit --template` 的產物，而且 `tests/test_env_audit.py` 會比對它
    與模板輸出 —— 被擋掉的話那條測試在乾淨 clone 上會紅，而**原因會指向錯的方向**
    （「模板不一致」而不是「範例檔不見了」）。
    """
    assert not _is_ignored(".env.example"), \
        "`.env.example` 被 gitignore 擋住了 —— 它必須追蹤，否則 --check 在 CI 上會紅"
    assert (ROOT / ".env.example").exists(), "`.env.example` 不見了"


def test_gitignore_still_ignores_plain_env():
    """基礎規則仍在（`.env` 本身）。這一條是迴歸守衛。"""
    assert _is_ignored(".env"), "`.env` 沒被 gitignore —— 那是明文憑證"