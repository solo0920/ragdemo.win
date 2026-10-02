"""`scripts/host-doctor.sh` 新增的四項檢查（2026-10-02）。

## 為什麼這四項值得單獨測

它們補的是**四個「本專案真的踩過」的洞**，每一個的症狀都是**靜默**的：

1. **`ch_tools` 只用 `command -v` 確認 sops 執行檔在** —— 那證明不了任何事。
   私鑰不在、recipient 對不上，一樣是「sops 在」而 `pull` 失敗。
2. **`QDRANT__SERVICE__ALT_API_KEY` 缺了，本機查詢全對** —— 只有
   `sync-snapshot.sh` 跨機寫入時 401。
3. **`/health` 回 200 但 RAG 已經斷了** —— 刪掉 `OLLAMA_URLS` 之後實測：
   `/health` 全程 200、`POST /query` 全部 500（`/api/embed` 掛在 ollama）。
4. **bash 3.2 的全形括號** —— `$keyf（` 會被找成變數 `keyf（`。

## 為什麼用「靜態比對」而不是「真的跑 host-doctor」

`host-doctor.sh` 會動 docker、會真的打一次 `/query`（用掉額度）、會真的
解密。在測試裡跑那些就是**測試對真環境跑** —— 這個專案在這上面踩過三次，
也是最常見的 CI 紅掉原因（CI 是乾淨 clone、沒有 `.env`、沒有 docker）。

所以這裡驗的是**形狀與判準**：檢查存在、判準是對的、錯誤訊息會指出真正的
原因。至於「在真的壞掉的環境下會不會亮」，那是 `host-doctor.sh` 自己在三台
機器上做的事 —— 而那正是它存在的理由。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HD = ROOT / "scripts" / "host-doctor.sh"
SRC = HD.read_text(encoding="utf-8")


def _fns() -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r"^(ch_[a-z_]+)\(\) \{", SRC, re.M):
        start = m.end()
        depth = 1
        i = start
        while i < len(SRC) and depth:
            if SRC[i] == "{":
                depth += 1
            elif SRC[i] == "}":
                depth -= 1
            i += 1
        out[m.group(1)] = SRC[start:i]
    return out


def test_the_four_new_checks_exist_and_are_wired_in():
    """四項都要存在，**而且被呼叫** —— 定義了沒呼叫等於沒有。

    ⚠️ 分開驗是刻意的：本專案有過「函式寫好了、忘了加進執行順序」形狀的問題，
    而那種錯誤不會讓任何東西變紅 —— 檢查就是不會跑。
    """
    fns = _fns()
    for name in ("ch_sops", "ch_qdrant_keys", "ch_readiness", "ch_query"):
        assert name in fns, f"{name} 不存在"
    # 執行順序：找出連續呼叫 ch_* 的那一段
    calls = re.findall(r"^ch_[a-z_]+$", SRC, re.M)
    for name in ("ch_sops", "ch_qdrant_keys", "ch_readiness", "ch_query"):
        assert name in calls, f"{name} 定義了但沒被呼叫 —— 檢查不會跑"
    # 順序有意義：sops/keys/readiness 要在 query 之前
    order = {n: calls.index(n) for n in calls}
    assert order["ch_sops"] < order["ch_query"], \
        "ch_sops 應該在 ch_query 之前（前面失敗時，後面的結果沒意義）"
    assert order["ch_readiness"] < order["ch_query"], \
        "ch_readiness 應該在 ch_query 之前"


def test_sops_check_really_decrypts_and_does_not_leak():
    """`ch_sops` 必須**真的解密一次**，而且不留下值。

    「有裝 sops」與「能用 sops」是兩件事 —— 這個 repo 記錄在案的教訓
    （2026-10-02 的逐鍵指紋、版面指紋都是同一型：證明**形狀**不等於證明**行為**）。
    """
    body = _fns()["ch_sops"]
    assert "sops --decrypt" in body, "沒有真的解密 —— 只確認執行檔存在證明不了任何事"
    assert "--extract" in body, "應該只取一個鍵，不要把整份解密出來"
    # 值不能被印出來
    assert not re.search(r'echo\s+"?\$\{?out\}?"?', body), \
        "把解密結果 echo 出來了 —— 值會進 log"
    assert re.search(r"(shred -u|rm -f)\s+\"?\$tmp", body), \
        "臨時檔沒有銷毀 —— 解密出來的明文會留在磁碟上"
    assert "mktemp" in body, "應該用 mktemp，不要寫固定路徑"


def test_qdrant_check_distinguishes_local_from_cross_machine():
    """ALT key 缺了**不能**是 fail —— 本機查詢不需要它。

    把它當 fail 會產生一個**每次都紅**的假紅，而假紅會讓人開始忽略這支腳本。
    但一定要講清楚缺了會壞什麼（跨機寫入 401）。
    """
    body = _fns()["ch_qdrant_keys"]
    assert "qdrant-alt-key warn" in body, \
        "ALT_API_KEY 空應該是 warn 不是 fail（本機查詢不受影響）"
    assert "401" in body, "要說明缺了會壞什麼（跨機寫入 401），不能只說 warn"
    assert "QDRANT__SERVICE__ALT_API_KEY" in body and "QDRANT__SERVICE__API_KEY" in body, \
        "兩個槽都要看 —— 只看 API_KEY 抓不到這個洞"
    # 只印長度，不印值
    assert "${#" in body, "應該只印長度"


def test_readiness_and_query_are_separate_checks():
    """**`/ready` 與「真的打一次查詢」必須是兩件事。**

    2026-10-02 實測：刪掉 `OLLAMA_URLS` 之後 `/health` 全程 200、
    `POST /query` 全部 500。所以「依賴都探得到」與「實際查詢會成功」是兩個
    不同的問題 —— 合成一個就會漏掉其中一類。
    """
    fns = _fns()
    readiness, query = fns["ch_readiness"], fns["ch_query"]
    assert "/ready" in readiness and "8000/ready" in readiness
    assert "/query" in query and "8000/query" in query
    assert "/query" not in readiness, \
        "/ready 檢查不該自己去打 /query —— 那會讓它變慢，而它會被輪詢"
    assert "/health" not in readiness, (
        "不要用 /health 當 readiness 的判準 —— 它的語意是「進程活著且可達」"
        "（前端同儕探測依賴那個語意），它**不會**回報依賴故障")


def test_query_check_can_be_skipped_and_says_so():
    """`ch_query` 會用掉一點額度，所以要能跳過 —— 但跳過必須**顯示為跳過**。

    靜默跳過會讓「沒有檢查」看起來像「檢查通過」。
    """
    body = _fns()["ch_query"]
    assert "RAGDEMO_NO_QUERY" in body, "要提供跳過開關（它會真的呼叫上游）"
    assert re.search(r'bump query skip', body), \
        "跳過時要用 skip 而不是靜默 return —— 否則讀的人以為查過了"


def test_perm_of_helper_avoids_the_gnu_stat_f_trap():
    """**`stat -f` 在 GNU 上是「檔案系統狀態」，不是檔案系統。**

    ⚠️ 2026-10-02 實測踩到：`stat -f '%Lp' "$f" || stat -c%a "$f"` 這個寫法
    在 Linux 上**前者成功**（印出整份 filesystem 報告），於是 `||` 的 fallback
    永遠不執行。那不是報錯，是**印出一堆不相干的東西並看起來像通過** ——
    比沒有權限檢查更糟。

    所以必須**先探測哪個旗標可用**，而且探測本身要驗證輸出形狀。
    """
    assert "perm_of()" in SRC, "沒有 perm_of() helper"
    body = re.search(r"perm_of\(\) \{(.*?)\n\}", SRC, re.S)
    assert body, "perm_of() 沒有實作"
    b = body.group(1)
    assert "stat -c" in b and "stat -f" in b, "兩種旗標都要處理（macOS 與 Linux）"
    # ❌ 絕不可用 `||` 串 —— 那正是踩到的那個形狀
    assert not re.search(r"stat -f[^\n]*\|\|", b), \
        "不可用 `||` 串 stat 的兩種旗標：GNU 的 `stat -f` 會成功但答非所問"


def test_bash32_fullwidth_safe():
    """新程式碼不可有 `$VAR` + 全形字元 —— **本機測不出來，只有 mbp 會炸**。

    `tests/test_bash32_fullwidth.py` 掃全部腳本；這裡再釘一次這支，
    因為它是最常被改的那支。
    """
    r = subprocess.run(["bash", "-n", str(HD)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    bad = []
    for i, line in enumerate(SRC.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue          # 註解裡可以寫那個形狀（那是警告文字）
        m = re.search(r"\$[A-Za-z_][A-Za-z_0-9]*[^\x00-\x7F]", line)
        if m:
            bad.append(f"{i}: {m.group(0)!r}")
    assert not bad, "可執行行有 `$VAR` + 全形字元（bash 3.2 會當成變數名的一部分）:\n  " + "\n  ".join(bad)
