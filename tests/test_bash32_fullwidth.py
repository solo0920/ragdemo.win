"""**bash 3.2 的全形括號坑** —— 靜態掃描（2026-10-02）。

## 為什麼需要這個測試

`$VAR` 後面**緊接**非 ASCII 字元時，macOS 預設的 **bash 3.2** 會把它當成
變數名的一部分：

    echo "版面 $layout_fp（三台必須相同）"
    # bash 5.x → 印出變數值，然後是「（」
    # bash 3.2 → 找變數 layout_fp（  → set -u → layout_fp: unbound variable

**症狀是靜默的半數情況**：沒有 `set -u` 時只是印出空字串（訊息裡少一段），
有 `set -u`（專案全部腳本都有）就直接**整支腳本死掉**。

## 為什麼會反覆踩到

因為**本機 CI 都在 bash 5.x**，那裡 `$VAR（全` 解析正常。2026-10-02 踩到的
那個 `env-sync.sh:561` 在 wsl 上跑了幾天完全正常，然後在 mbp 上第一次執行
就讓 `--check` 整個掛掉 —— 而 `--check` 是三機規格的**唯一驗收**。

所以這條不能靠「記得用 `${}`」，必須靜態掃描：**這個 repo 的所有可執行行
都不該有這種寫法。**

## 判準

只掃**非註解行**。註解裡出現 `$VAR（全形` 是**刻意**的 —— 那些是警告別人
別這麼寫的說明文字（見 `compose-up-at-login.sh:52`）。

跳過註解的判法：去掉行首空白後以 `#` 開頭，或行內 ` #` 之後才算註解。
不處理行內註解是刻意的 —— 那樣會漏掉 `cmd "$X"   # 說明（全形` 這種真 bug。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# `$NAME` 後面緊接一個非 ASCII 字元。全形括號（U+FF08/U+FF09）、全形斜線、
# 破折號、中文字元都算 —— 它們在 bash 3.2 的 locale 下都可能是識別字元。
BAD = re.compile(r"\$[A-Za-z_][A-Za-z_0-9]*[^\x00-\x7F]")

# 這些是刻意示範「不要這樣寫」的註解，掃描時要略過整行。
ALLOW_COMMENT_MARKERS = (
    "# ⚠️ 這裡必須寫 ${",
    "# ⚠️",
)


def _shell_files() -> list[Path]:
    out = [ROOT / "scripts" / p.name for p in (ROOT / "scripts").glob("*.sh")]
    hooks = ROOT / ".githooks"
    if hooks.is_dir():
        out += [p for p in hooks.iterdir() if p.is_file()]
    return sorted(p for p in out if p.is_file())


def _is_comment(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("#")


def test_no_executable_line_uses_bare_var_before_non_ascii():
    """**可執行行**不可出現 `$VAR` + 非 ASCII（bash 3.2 會當成變數名的一部分）。

    Friction 點（2026-10-02 實測）：`env-sync.sh:561` 是
    `echo "…版面 $layout_fp（三台…"`。在 wsl（bash 5.3）上跑了好幾天完全正常，
    第一次在 mbp 上執行就 `layout_fp: unbound variable` —— 而 `--check` 是
    三機規格的**唯一驗收**，所以那台連「我做到了沒有」都沒辦法回答。

    `backup-env.sh:70` 是同一形狀，只是只在「輸出目錄在 repo 內」那個分支
    才走到，所以症狀更難遇到。
    """
    bad: list[str] = []
    for f in _shell_files():
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for ln, line in enumerate(lines, 1):
            if _is_comment(line):
                continue
            m = BAD.search(line)
            if m:
                rel = f.relative_to(ROOT)
                bad.append(f"{rel}:{ln}  {m.group(0)!r}  →  用 ${{{m.group(0)[1:].rstrip('（／—–')}}}")
    assert not bad, (
        "這些可執行行有 `$VAR` 緊接非 ASCII 字元。\n"
        "**在 bash 5.x 上測不出來**（本機與 CI 都是 5.x），只在 macOS 的 bash 3.2 上炸，\n"
        "而且 `set -u` 會讓整支腳本死掉。請一律寫 `${VAR}`：\n  "
        + "\n  ".join(bad))


def test_the_scan_actually_catches_the_known_instances():
    """掃描器本身要能抓到已知的真例 —— 否則上面那條是假綠。

    這是「守衛的守衛」。用**當初踩到的原文**當測試資料，而不是另寫一個
    類似的例子 —— 另寫的例子很可能剛好避開真正的陷阱（例如把全形括號
    換成半角，那就沒有陷阱了）。
    """
    for sample in (
        '  echo "版面 $layout_fp（三台必須相同）"',
        '  "$ROOT"|"$ROOT"/*) die "輸出目錄在 repo 內（$OUT_DIR）。"',
    ):
        m = BAD.search(sample)
        assert m, f"掃描器抓不到這個真例：{sample}"
        assert m.group(0).endswith(("（", "）")) or any(
            ord(c) > 127 for c in m.group(0)), \
            f"抓到的東西不對：{m.group(0)!r}"


def test_comments_may_mention_the_pattern_on_purpose():
    """註解裡**可以**出現那個寫法 —— 那些是警告別人別這麼寫的說明。

    跳過註解不是放水，是因為把 `compose-up-at-login.sh:52` 那種
    「⚠️ 這裡必須寫 ${REPO} 不是 $REPO：後面接的是全形右括號」判成違規，
    等於逼人刪掉警告。而那條註解正是防止同樣 bug 再生的東西。
    """
    f = ROOT / "scripts" / "compose-up-at-login.sh"
    hit = [ln for ln in f.read_text(encoding="utf-8").splitlines()
           if BAD.search(ln) and _is_comment(ln)]
    assert hit, "compose-up-at-login.sh 裡那條警告註解不見了 —— 它是預防措施"


def test_all_shell_scripts_still_parse():
    """所有 shell 腳本都要能 parse（`bash -n`）。

    擺在最後面當 sanity check：`${VAR}` 改錯位置（例如把括號寫到變數名裡）
    會讓腳本整個不能 parse，而那是比未綁定變數更難懂的錯誤。
    """
    bad = []
    for f in _shell_files():
        r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
        if r.returncode != 0:
            bad.append(f"{f.relative_to(ROOT)}: {r.stderr.strip()[:120]}")
    assert not bad, "\n".join(bad)
