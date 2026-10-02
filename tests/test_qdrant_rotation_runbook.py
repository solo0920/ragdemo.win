"""`settings/env/QDRANT-KEY-ROTATION-RUNBOOK.md` 的守衛。

⚠️ 為什麼一份 markdown 需要測試：因為它是**會被執行的**文件 —— 裡面有
會改 `.env` 的 python 片段與有順序約束的步驟。而今天這一整輪的核心教訓是
**「驗過一次會爛」**：本專案已經有多個「文件／測試寫著某件事，但沒有人驗證它
是否還成立」的案例（`ch_law_version` 宣稱有驗 `last_checked` 卻沒驗、
`ENV-SPEC §一 B` 只查了容器那條路就宣稱已解決）。

所以這裡釘三件事：
  1. 那三個 python 片段**真的還能跑**（在臨時 `.env` 上執行，不碰真的）
  2. **不可回頭點的順序** —— S5 必須在 S4 之後，這是整份文件最要緊的一條
  3. 文件裡不得出現明文憑證
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "settings" / "env" / "QDRANT-KEY-ROTATION-RUNBOOK.md"


# ⚠️⚠️ **這裡本來是 `pytest.mark.skipif(not RUNBOOK.exists())` —— 而那正是本專案
# 2026-10-03 記錄的第六個同型缺陷：把壞掉的東西變成綠的。
#
# 實際發生：`RUNBOOK` 的路徑寫成 `settings/…`（少了 `env` 那一層），於是檔案明明
# 存在，三條測試卻全部 **skip** —— 測試報告顯示「3 skipped」，**看起來像有覆蓋**，
# 實際上一個斷言都沒跑。
#
# `skipif not exists` 對「基礎設施缺失」是合理的，對「**我指名的這份文件**」不是 ——
# 檔案不見了就是有人刪了或改名了，那是**要查的事**，不是可以跳過的事。


def test_the_runbook_file_exists():
    """**檔案必須在。** 見上方：這裡曾經是 skip，而 skip 把壞路徑變成綠燈。"""
    assert RUNBOOK.exists(), (
        f"runbook 不在 {RUNBOOK} —— 如果它改名或被併進別的文件，"
        "請更新這個測試的路徑，並確認新位置仍然釘住順序與片段可執行性")
    assert RUNBOOK.stat().st_size > 2000, \
        f"runbook 只有 {RUNBOOK.stat().st_size} bytes —— 內容被截斷了？"


def _text() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def test_runbook_snippets_still_run():
    """**文件裡的 python 片段必須真的還能跑。**

    ⚠️ 第一版就有兩個真 bug，而且**都不會報錯**：
      · `Path(".env").with_suffix(".env.rotbak")` 產生的是 **`.env.env.rotbak`**
        —— pathlib 對點檔的處理不是「去掉副檔名再加」，所以訊息裡寫的備份檔名
        是錯的。S7 的清理步驟會誤以為沒備份（或反過來）。
      · 只有第一段有「該鍵出現幾次」的斷言，另兩段**沒有** —— 若 `.env` 裡那個鍵
        出現兩次，會**靜默替換兩處**。

    兩者都不會讓指令失敗，只會在事後產生錯誤的狀態。所以要用執行來驗，不能用
    「看起來對不對」。

    這條測試在 **tmp 資料夾**跑，**不碰真的 `.env`** —— 這個 repo 反覆在修的
    另一個類型就是「測試污染真實狀態」。
    """
    snippets = re.findall(r"<<'PY'\n(.*?)\nPY\n", _text(), re.S)
    assert len(snippets) == 3, \
        f"應該有 3 個 python 片段，實際 {len(snippets)} —— 有人改過或刪過"

    for i, sn in enumerate(snippets, 1):
        key = re.search(r'startswith\("([A-Z_]+)="', sn)
        assert key, f"片段{i} 沒看出它改哪個鍵 —— 寫法變了，這條測試要跟著改"
        key = key.group(1)

        d = ROOT / ".pytest-rotcheck"
        d.mkdir(exist_ok=True)
        env_file = d / ".env"

        def write_env(dup: bool):
            body = (f"{key}=x\n{key}=y\nOTHER=1\n" if dup
                    else f"A=1\nQDRANT_API_KEY=own\nQDRANT_PEER_API_KEY=peer\nB=2\n")
            env_file.write_text(body, encoding="utf-8")
            for f in d.glob(".env.rotbak*"):
                f.unlink()

        # ① 正常情況：替換成功、且**備份檔名真的存在**
        write_env(dup=False)
        r = subprocess.run([sys.executable, "-", "N" * 43],
                           input=sn, capture_output=True, text=True, cwd=d, timeout=60)
        assert r.returncode == 0, f"片段{i} 正常路徑就失敗了：{r.stderr[-300:]}"
        got = dict(l.split("=", 1) for l in env_file.read_text().splitlines() if "=" in l)
        assert got.get(key) == "N" * 43, f"片段{i} 沒有真的替換 {key}"
        baks = list(d.glob(".env.rotbak*"))
        assert len(baks) == 1, \
            f"片段{i} 沒有產生恰好一個備份（實際 {[b.name for b in baks]}）—— " \
            f"⚠️ `Path('.env').with_suffix('.env.rotbak')` 會產生 `.env.env.rotbak`"

        # ② 重複鍵必須擋下（否則會靜默替換兩處）
        write_env(dup=True)
        r2 = subprocess.run([sys.executable, "-", "N" * 43],
                            input=sn, capture_output=True, text=True, cwd=d, timeout=60)
        assert r2.returncode != 0, f"片段{i} 遇到重複的 {key} 居然成功了 —— 會靜默改兩處"
        assert f"{key} 出現 2 次" in r2.stderr, \
            f"片段{i} 的錯誤訊息要指名是重複（現在：{r2.stderr.strip().splitlines()[-1][:90]}）"

        # ③ 長度不符必須擋下（43 是 qdrant key 的長度）
        write_env(dup=False)
        r3 = subprocess.run([sys.executable, "-", "tooshort"],
                            input=sn, capture_output=True, text=True, cwd=d, timeout=60)
        assert r3.returncode != 0, f"片段{i} 長度不對也接受 —— 那會寫進一個無效的 key"

        for f in d.glob(".env*"):
            f.unlink()
        d.rmdir()


def test_the_irreversible_step_comes_after_the_verification_step():
    """**不可回頭的步驟必須排在驗收之後。**

    這是整份文件最要緊的一條。`settings/env/README.md §7` 記著：
    「只輪換 peer 那把 → mbp／wsl 拿去打 x570 會 401，**而且再 pull 幾次都不會好**」。
    §1 的表格說明 **S5 是不可回頭點**（x570 不再接受舊值），而 S4 是最後一個
    能驗收跨機路徑的步驟。

    如果順序被調換（先抽掉舊值、再散播新值），兩台備援機就會永久 401，症狀是
    「備援資料悄悄停更」—— 只有 log，cron 只看得到非零 exit。

    所以這條測試**只看步驟在文件裡出現的次序**。
    """
    t = _text()
    # 步驟標題必須存在且可辨識
    steps = {}
    for m in re.finditer(r"^### (S\d)[a-z]?\. ", t, re.M):
        steps.setdefault(m.group(1), m.start())
    for need in ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"):
        assert need in steps, f"runbook 少了 {need} 這一步（寫法變了？）"

    ordered = [s for s, _ in sorted(steps.items(), key=lambda kv: kv[1])]
    assert ordered == sorted(ordered), f"步驟在文件裡的次序不對：{ordered}"

    # 不可回頭點必須**明寫**，而且排在 S4（最後的跨機驗收）之後
    assert "不可回頭" in t, "必須標示哪一步不可回頭 —— 否則沒人知道要在哪裡停下"
    assert steps["S5"] > steps["S4"], \
        "S5（不可回頭）必須在 S4（跨機驗收）之後 —— 反過來就是兩台備援機永久 401"
    # S3 是「還沒 pull 之前先證明新值能連」的閘門，也不能排在 S4 之後
    assert steps["S3"] < steps["S4"], \
        "S3 是散播前的閘門，必須在 S4 之前 —— 散播了才驗就來不及了"


def test_runbook_contains_no_plaintext_credential():
    """**runbook 不得含明文憑證。**

    這個 repo 的硬規則：憑證只印 `len=`／`sha12=`。runbook 會被貼進工單、貼進
    聊天、貼進 issue —— 那些地方都不該有值。

    特別要防的是「順手把現在的值貼進範例」：那正是 2026-10-03 發生的事
    （`QDRANT_API_KEY` 被印進對話）。
    """
    t = _text()
    # 常見的憑證形狀：qp_ 前綴、43 字符的 base64url、ghp_、sk-、ENC[…]
    for pat, why in (
        (r"\bqp_[A-Za-z0-9_-]{10,}", "qdrant key"),
        (r"\bghp_[A-Za-z0-9]{20,}", "GitHub PAT"),
        (r"\bsk-[A-Za-z0-9]{20,}", "OpenAI-style key"),
        (r"ENC\[", "sops 密文"),
        (r"\b[A-Za-z0-9_-]{43}\b", "43 字符（qdrant key 長度）—— 必須是佔位符不是真值"),
    ):
        hits = [h if isinstance(h, str) else h[0] for h in re.findall(pat, t)]
        # 允許明顯是佔位/說明用的（全是同一個字元、或含中文說明）
        real = [h for h in hits
                if not re.fullmatch(r"(N|X|Y|V|M)+", h)
                and h not in ("N" * 43, "X" * 43, "Y" * 43, "V" * 43, "M" * 43)]
        assert not real, f"runbook 裡有疑似明文憑證（{why}）：{real[:2]}"