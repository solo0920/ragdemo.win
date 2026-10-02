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


def test_no_gnu_only_commands_are_used_unconditionally():
    """**不可無條件使用只存在於 GNU coreutils 的指令。**

    ⚠️ 2026-10-02 實測踩到（mbp 回報）：我寫 `timeout 60 sops ...`，
    而 **macOS 沒有 `timeout`**（那是 GNU coreutils 的，macOS 上通常也沒裝
    `gtimeout`）。結果 `command not found` → rc=127 → 那條檢查**永遠 fail**，
    而且 fail 的理由看起來像「sops 解不開」，實際是「指令不存在」——
    **症狀指向完全錯誤的方向。**

    這是本專案第三次只會在 macOS 炸的錯誤：

    | # | 形狀 | 類別 | 現有守衛涵蓋？ |
    |---|---|---|---|
    | 1 | `$VAR（全形` | 引號／字元 | ✅ `test_bash32_fullwidth.py` |
    | 2 | GNU `stat -f` | 旗標語意 | ✅ `test_perm_of_helper_*` |
    | 3 | `timeout` 不存在 | **指令存在性** | ❌ **原本沒有** ← 這條 |

    前兩個是「寫法」問題，這個是「有沒有」問題 —— **完全不同的類別**，
    所以前兩個的守衛涵蓋不到它。這就是為什麼要單獨一條。

    判準是「這個指令在 macOS 上預設有沒有」。清單要短且可辯護：只列**確定
    不在 macOS 預設環境**的，不要把所有 Linux 指令都塞進來（否則這條測試
    會變成一份無法維護的假設清單）。
    """
    # macOS 預設**沒有**的 GNU coreutils 指令。每一個都要有理由。
    for fn_name, body in _fns().items():
        for i, line in enumerate(body.splitlines(), 1):
            code = line.split("#")[0]
            # 判準是**呼叫形態** `timeout <數字>`，而不是「前面是什麼」。
            # ⚠️ 第一版寫成「前面是 `$(` 或行首或 `;`」—— 那漏掉了
            #   `out="$(FOO=bar timeout 60 sops …)"` 這種形狀：
            #   `timeout` 前面是 `"$keyf" `，於是測試**綠著**而 mbp 照樣炸。
            #   那是「用猜的規則驗證實際行為」的典型失敗。
            for m in re.finditer(r"(?<!run_with_)(?<![\w-])timeout\s+\d", code):
                assert "command -v timeout" not in code, (
                    f"{fn_name}() 第 {i} 行：偵測式要留 `command -v timeout`，"
                    f"但那不該同時是無條件呼叫")
                raise AssertionError(
                    f"{fn_name}() 第 {i} 行**無條件**用了 `timeout`："
                    f"{line.strip()[:90]}\n"
                    f"   macOS 沒有它（GNU coreutils 才有）→ rc=127，"
                    f"而 fail 的理由會看起來像「sops 解不開」。"
                    f"請改用 run_with_timeout。")
            # gtimeout 只允許出現在 fallback 分支（`^\s*gtimeout "$secs"`）
            for m in re.finditer(r"(?<![\w-])gtimeout\b", code):
                assert re.match(r'^\s*gtimeout\s+"\$secs"', code), (
                    f"{fn_name}() 第 {i} 行：gtimeout 只該出現在 fallback 分支"
                    f"（且要帶 \"$secs\"），這裡：{line.strip()[:80]}")

    # 必須有那個可攜的 helper，而且它**真的**處理了三種情況
    assert "run_with_timeout()" in SRC, "缺少可攜的 timeout helper"
    helper = re.search(r"run_with_timeout\(\) \{(.*?)\n\}", SRC, re.S)
    assert helper, "run_with_timeout() 沒有實作"
    h = helper.group(1)
    assert "command -v timeout" in h, "要先試系統的 timeout"
    assert "gtimeout" in h, "macOS + coreutils 要有 gtimeout 這條路"
    assert re.search(r'^\s*"\$@"', h, re.M), \
        "兩者都沒有時要直接跑（寧可沒有上限，也不能因為沒有 timeout 就不檢查）"


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


# ── source 機每日同步（2026-10-02）────────────────────────────────────────

def test_source_sync_check_exists_and_is_wired():
    """source 機的每日同步檢查必須存在**而且被呼叫**。

    為什麼需要：`ch_law_version` 對 source 機（`.law_sync.json` 存在）**只看檔案
    在不在**，完全沒檢查 `last_checked` —— 而它自己的註解卻寫著「每日
    `sync_daily` 有在跑（last_checked 是新的）」。**那句話沒有任何程式碼在驗。**

    後果正是使用者要確認的那件事：x570 的 crontab 有兩個錯誤時，`host-doctor`
    回 ok。**一個宣稱一個沒驗證過的東西，比沒有那個檢查更糟。**
    """
    fns = _fns()
    assert "ch_source_sync" in fns, "缺少 source-sync 檢查"
    calls = re.findall(r"^ch_[a-z_]+$", SRC, re.M)
    assert "ch_source_sync" in calls, "定義了但沒被呼叫"


def test_source_sync_distinguishes_not_run_from_ran_and_failed():
    """**「沒在跑」與「跑了但失敗」必須是兩句不同的話。**

    ⚠️ 2026-10-02 wsl 實測：資料停在 62 小時前，而 log 顯示**當天有在嘗試** ——
    是上游 `law.moj.gov.tw` 回 HTTP 500。第一版訊息寫「沒在跑，或 crontab 有錯」，
    那是**把跑了但失敗說成沒跑**，會讓人去查 crontab 而真正的原因是上游。

    這個專案反覆在收這一類（把兩個不同的失效混成一句話）。所以訊息必須：
    * 看 log 最後的時間戳判斷「有沒有近期嘗試」
    * 兩種情況給**不同的處置方向**（看 crontab vs 看 log／上游）
    """
    body = _fns()["ch_source_sync"]
    assert "last_checked" in body, "必須讀 last_checked（那才是「上次同步」的證據）"
    assert "applied_at" in body, "也要看 applied_at —— last_checked 會更新但 applied_at 不會（下載到了但沒套用）"
    # 判斷「近期嘗試」的依據必須是 log，而不是猜
    assert "log" in body.lower(), "要去看 sync 的 log"
    assert "跑了但失敗" in body, "必須有『跑了但失敗』這個說法"
    assert "沒在跑" in body, "必須有『沒在跑』這個說法"
    # last_checked 的門檻要 > 24h，否則每天下午都誤報
    m = re.search(r"float\('\$age_h'\) < (\d+)", body)
    assert m, "門檻寫法變了"
    assert int(m.group(1)) >= 26, (
        f"門檻 {m.group(1)}h 太短 —— 每日 06:30 跑的機器在當天下午就會誤報。"
        f"要 >24h 留緩衝")


def test_source_sync_checks_every_path_in_the_cron_line():
    """**cron 那條指令的每個路徑都要靜態驗** —— 因為 cron 失敗是靜默的。

    2026-10-02 x570 的兩個錯誤就是這兩種形狀，而症狀都是「沒有任何錯誤輸出，
    只是法規沒更新」：cron 的信寄到郵件，而多半沒人看。

    必須分別驗：工作目錄（`cd` 目標）、解譯器、腳本 —— 少驗一個，那一類錯誤
    就漏掉。
    """
    body = _fns()["ch_source_sync"]
    assert "source-sync-cwd" in body, "要驗 cd 的目標"
    assert "source-sync-interp" in body, "要驗解譯器（.venv 名稱拼錯是常見原因）"
    assert "source-sync-script" in body, "要驗腳本本身"


def test_source_sync_parsed_paths_are_trimmed():
    """**解析出來的路徑必須 trim** —— 第一版在 wsl 上**假失敗**。

    ⚠️ `[^&|;]+` 是貪婪的，會把 `&&` 前的空白一起吃進去，於是 cdpath 變成
    `/…/ragdemo.win `（帶尾隨空白）→ `[ -d ]` 為假 → 回報「cd 目標不存在」，
    而那個目錄明明存在、cron 完全正常。

    **一支專門告訴人「你的 cron 會不會失敗」的檢查，如果會假失敗就會被忽略** ——
    那比沒有這個檢查更糟。所以 trim 不是細節，是這條檢查能不能被信任的前提。
    """
    body = _fns()["ch_source_sync"]
    for var in ("cdpath", "ipath", "spath"):
        # 取出該變數那一行，必須有去空白的動作
        m = re.search(rf'^\s*{var}="\$\(.*$', body, re.M)
        assert m, f"找不到 {var} 的取得那一行（寫法變了？）"
        line = m.group(0)
        assert re.search(r"sed -E 's/\^\[\[:space:\]\]\+//", line) or \
               re.search(r"tr -d '[:space:]'", line) or \
               "awk" in line, \
            f"{var} 沒有 trim —— 尾隨空白會讓 [ -d ] / [ -x ] 為假（2026-10-02 實測假失敗）"
