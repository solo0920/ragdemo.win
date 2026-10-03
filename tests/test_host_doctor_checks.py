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
    # ⚠️ 2026-10-03：原本斷言 `"8000/ready" in readiness` —— **那等於把「寫死
    # 埠碼」當成正確行為釘住**。2026-10-03 把 ch_readiness／ch_query 改成讀
    # `$API_URL`（可用 HOST_API_LOCAL 覆寫）之後，這條斷言擋住了正確的改法。
    #
    # 那是本專案今天記錄了八次的那一類的**倒過來**版本：守衛不是抓壞的，
    # 而是**擋住修好的**。而症狀一樣是「測試紅了，但紅的原因指向錯的方向」。
    assert "/ready" in readiness, "ch_readiness 必須打 /ready"
    assert "${API_URL}/ready" in readiness, (
        "ch_readiness 必須打 `${API_URL}/ready` —— 寫死 8000 會讓 "
        "HOST_API_LOCAL 對這一項失效（改埠之後症狀是「明明改了卻沒生效」）")
    assert "/query" in query and "${API_URL}/query" in query, \
        "ch_query 必須打 `${API_URL}/query`（理由同上）"
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


# ── 時區與 log 判據（2026-10-02）────────────────────────────────────────────

def _bash_snippet(fn_name: str) -> str:
    """抽出函式本體（去掉 `local` 之外的相依呼叫不管），給 bash 執行。"""
    return re.search(rf"^{fn_name}\(\) \{{\n(?:.*\n)*?\}}\n", SRC, re.M).group(0)


def test_age_h_local_treats_naive_as_local_not_utc(tmp_path):
    """**naive 時間戳必須當本機時間，不能當 UTC。**

    ⚠️ 2026-10-02 實測：今天新寫的 `ch_source_sync` 把 naive 當 UTC
    （`t.replace(tzinfo=utc)`），那是 `ch_law_version` 註解裡**已經記錄過**的
    同一個錯誤 —— 少算整個 UTC offset。

    實測同一個 `last_checked=2026-09-29T23:39:23`（wsl 是 CST +0800）：

        正確（naive ↔ 本機）: 71.7 小時
        當成 UTC          : 63.7 小時      ← 差 8.0，就是 CST 的 offset

    門檻 26h 因此實際變成 34h —— **檢查晚 8 小時才響**。而一個「晚 8 小時才響」
    的門檻，看起來完全正常，因為它還是會響。

    這條測試**真的執行** `age_h_local()`，並且在一個人為設成非零 offset 的
    `TZ` 下跑 —— 那正是兩種算法會分歧的條件。
    """
    import datetime as _dt
    import subprocess

    fn = _bash_snippet("age_h_local")
    assert fn, "抽不到 age_h_local()"

    # ⚠️ **不要用 TZ= 環境變數來製造分歧** —— 那是錯的前提。`date '+%F %T'` 產生
    #   的 naive 字串與「檢查執行的機器」是同一個時區，兩邊同框相減才是正確的。
    #   改了 TZ 就變成「拿 CST 的字串去減 UTC 的 now」，兩種算法都會對不上，
    #   測試會因為錯誤的理由失敗。
    #
    #   正確的判準：結果要**等於 local-naive 算法**，而**不等於** UTC 算法。
    #   本機是 CST（+0800）時兩者差 8 小時，是乾淨的判別距離。
    naive = (_dt.datetime.now() - _dt.timedelta(hours=3)).replace(
        tzinfo=None).isoformat(timespec="seconds")
    # 兩種算法（**全部用 naive 相減**，混 aware/naive 會 TypeError）：
    #   正確 naive↔本機 : now_local - naive              = 3.0
    #   錯誤 當成 UTC   : now_utc_naive - naive          = 3.0 - offset
    now_local_naive = _dt.datetime.now()
    now_utc_naive = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    offset_h = (now_local_naive - now_utc_naive).total_seconds() / 3600
    want_local = 3.0
    want_utc = want_local - offset_h

    r = subprocess.run(["bash", "-c", f'{fn}\nage_h_local "{naive}"'],
                       capture_output=True, text=True, timeout=30)
    got = r.stdout.strip()
    assert got, f"沒有輸出：{r.stderr[:200]}"
    age = float(got)
    assert abs(age - want_local) <= 0.2, (
        f"算得 {age} 小時，應約 {want_local} —— naive 被當成 UTC 的話會是 "
        f"{want_utc:.1f}（本機 UTC offset 的差）")
    assert abs(age - want_utc) > 0.5 or abs(want_utc - want_local) < 0.5, (
        "這個測試在 UTC 機器上沒有判別力（兩種算法相同）—— 換一個非零 offset 的"
        "機器跑，或把斷言改成相對比較")


def test_law_log_last_attempt_reads_the_log_not_the_json(tmp_path):
    """**「排程有沒有在跑」只能看 log，不能看檔案裡的欄位。**

    ⚠️ `sync-snapshot.sh:185` 是：
        if [ "$old" = "$ver" ]; then log "law version unchanged"; return 0; fi
    —— 版本沒變就**不寫 `synced_at`**。所以 `synced_at` 是「上次**版本變了**」
    的時間戳，**不是**「上次**跑了**」。拿它當健康信號會**結構性誤報**：
    上游沒發新版時它永遠不動，而那是正常狀態。

    實測（mbp 2026-10-02 回報）：log 最後一行是
    `[2026-10-02 23:17:29] unchanged (39879 points), skip` —— 排程**有在跑而且成功**
    —— 而 doctor 說「快照已 25 小時沒成功更新」。**方向講反了。**
    """
    # ⚠️ `law_log_last_attempt_h` 會呼叫 `age_h_local`，所以**兩個都要帶進去** ——
    #   第一版只抽一個，結果是 `age_h_local: command not found`，
    #   而那種失敗看起來像「log 讀不到」，會讓人以為是實作的問題。
    fn = _bash_snippet("age_h_local") + _bash_snippet("law_log_last_attempt_h")
    assert fn, "抽不到 law_log_last_attempt_h()"

    log = tmp_path / "sync.log"
    # 三種時間戳形狀都要認：方括號、ISO T 分隔、帶秒
    log.write_text(
        "[2026-10-01 10:00:00] old line\n"
        "garbage without timestamp\n"
        "[2026-09-01 09:00:00] SYNC OK\n"
        "[2026-10-02T23:17:29] unchanged (39879 points), skip\n",
        encoding="utf-8")
    r = subprocess.run(["bash", "-c", f'SYNC_LOG="{log}"\n{fn}\nlaw_log_last_attempt_h'],
                       capture_output=True, text=True, timeout=30)
    got = r.stdout.strip()
    assert got, f"讀不到 log 時間：{r.stderr[:200]}"
    age = float(got)
    # 2026-10-02T23:17:29 —— 應該是很近（< 48h），而不是被最後一行以外的东西帶走
    assert age < 48, (
        f"算出 {age} 小時 —— 它抓錯行了。應取**時間最新的那一筆**，"
        f"不是檔案最後一個『像時間戳』的字串。")

    # 沒有時間戳時必須回空字串，讓呼叫端知道「無法判斷」而不是回 0
    empty = tmp_path / "empty.log"
    empty.write_text("no timestamps at all\n", encoding="utf-8")
    r2 = subprocess.run(["bash", "-c", f'SYNC_LOG="{empty}"\n{fn}\nlaw_log_last_attempt_h'],
                        capture_output=True, text=True, timeout=30)
    assert r2.stdout.strip() == "", (
        f"沒有時間戳時回傳了 {r2.stdout.strip()!r} —— 必須是空字串。"
        "回 0 會被當成「剛剛才跑過」，那是**最危險**的方向")


def test_law_version_checks_the_log_before_blaming_synced_at():
    """**`law-version` 必須先看 log，再談 `synced_at`。**

    順序有意義：先講 `synced_at` 會讓人去查上游，而真正可行動的事實往往是
    「這台根本沒有那條排程」。wsl 實測就是這樣 —— 訊息說 24.1 小時沒更新，
    但 `synced_at` 講的是「上游多久沒發新版」；而 wsl 的 crontab **沒有**
    `sync-snapshot.sh` 的條目（那兩條 `*/10` 是給 `ensure-stack.sh` 的）。
    """
    body = _fns()["ch_law_version"]
    log_chk = body.find("law_log_last_attempt_h")
    synced_msg = body.find("快照已 ${age_h} 小時沒成功更新")
    assert log_chk != -1, "ch_law_version 沒有查同步 log —— 那就只剩 synced_at 可看"
    assert synced_msg != -1, "原本那條訊息不見了（寫法變了？）"
    assert log_chk < synced_msg, (
        "先回報 synced_at 才查 log —— **先講錯的那個**，會讓人去查上游而不是排程")
    assert "排程" in body, "必須把「排程」與「版本」分開講"


# ── port 被別人佔走（2026-10-03，mbp 回報的真實事故）────────────────────────

def test_api_identity_and_port_owner_exist_and_are_wired():
    """`api-identity` 與 `api-port-owner` 必須存在**而且被呼叫**。

    事故：mbp 上 Homebrew 的 `omlx-server` 搶先綁 8000，我們的 api 容器之後
    publish **不報錯但被遮蔽**（OrbStack 的 port 轉發是 userspace proxy）。
    於是 doctor 報「readiness 404 / query 404」—— **症狀，不是原因**。
    人會去查「為什麼 /ready 沒了」，而真正的事實是「這個 port 上根本不是
    本專案的 API」。而 `code-drift` 仍綠，因為它是 docker exec 在容器內比對。
    """
    fns = _fns()
    for name in ("ch_api_identity", "ch_api_port_owner"):
        assert name in fns, f"缺少 {name}()"
        assert name in re.findall(r"^ch_[a-z_]+$", SRC, re.M), \
            f"{name}() 定義了但沒被呼叫 —— 定義了不跑等於沒有"


def test_api_identity_checks_content_not_only_the_status_code():
    """**必須比對回應「內容」，不能只看 HTTP code。**

    ⚠️ 實測：omlx 的 `/health` 回的是 **HTTP 200** —— 所以只查 code 的話，
    一個佔了 port 的外來程式**完全可以完全隱形**。舊的 `ch_readiness` 查的是
    `/ready`（那裡是 404），而它**根本沒查 `/health`**。

    我們的 `/health` 一定帶 `collection`／`host_id`／`host`；omlx 回的是
    `{"status":"healthy","default_model":null,"engine_pool":{…}}`，三個都沒有。
    """
    body = _fns()["ch_api_identity"]
    assert "/health" in body, "要查 /health —— 那是外來程式最可能回 200 的端點"
    assert "API_IDENTITY_FIELDS" in body, \
        "必須有一個欄位白名單來認出身分，而不是硬寫在 grep 裡"
    # 判準必須是「內容裡有沒有那些欄位」，不是「code 是不是 200」
    assert re.search(r"grep\s+-Eq?\s+\"?\\\$?\{?API_IDENTITY_FIELDS", body) or \
           'API_IDENTITY_FIELDS' in body and 'grep -Eq' in body, \
        "放行／擋下的判準必須是 grep 內容，不是 http_code"
    assert "http_code" in body, "仍需要 http_code（用於回報），但不能只有它"


def test_port_owner_does_not_mistake_a_non_process_field_for_a_name():
    """**`ss -ltnp` 沒有 process 資訊時，不可把別的欄位當成進程名。**

    ⚠️ 2026-10-03 在 wsl 實測：`ss -ltnp` 那一行只有 **5 個欄位**（port 轉發跑在
    另一個 namespace），而 **`$NF` 會抓到最後一個有值的欄位** —— 也就是 peer
    address `0.0.0.0:*`。

    第一版就這樣印出「監聽者：0.0.0.0:*」。**症狀是垃圾進 → 看起來很合理的錯
    輸出**：一句話解釋了 8000 是誰在用，而那是錯的 —— 比報錯更糟，因為人會照著
    那個錯名字去查。

    有 process 時欄位是 7 個（State/Recv-Q/Send-Q/Local/Peer/Process），
    所以必須要求 `NF>=7`。
    """
    body = _fns()["ch_api_port_owner"]
    assert re.search(r"NF\s*>=\s*7", body), \
        "抓 process 欄必須要求 NF>=7 —— 否則沒有 process 資訊時會抓到 peer address"
    assert not re.search(r"\{print\s+\$NF\}", body), \
        "不可用 $NF 抓 ss 的 process 欄（沒有 process 資訊時它會回 peer address）"


def test_lsof_and_ss_pipelines_cannot_kill_the_script(tmp_path):
    """**`lsof` 回 1 時，整支腳本必須活著。**

    ⚠️ 2026-10-03 實測：`lsof` 沒有相符時回 **1**，而 `set -o pipefail` 會把那個
    1 傳成整條管線的結果 → `out="$(…)"` 那行回 1 → **`set -e` 整支腳本死掉**。

    症狀是「stdout 零行、stderr 零行、exit 1」—— 完全看不出是哪一行，而
    `bash -n` 語法檢查**通過**（因為它語法真的沒錯）。

    ⚠️⚠️ 這一條**第一版是無效的**，而且無效的方式正是本專案今天記錄了七次的
    那一種：斷言寫成 `assert "|| true" in line or "|| true" in body` ——
    **第二個分支讓它恆真**。而且 `|| true` 在**續行**上，單行檢查本來就看不到。

    所以改成**真的執行**：造一個回 1 的假 `lsof` 放進 PATH，然後在 `set -euo
    pipefail` 下呼叫那個函式 —— 斷言它**回得出結論**，而不是把 shell 帶走。
    """
    import os
    import stat
    import subprocess

    stub = tmp_path / "lsof"
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    stub_ss = tmp_path / "ss"
    stub_ss.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stub_ss.chmod(stub_ss.stat().st_mode | stat.S_IEXEC)

    # ⚠️ 用檔案既有的 `_fns()`，不要自己寫 regex。第一版自己寫了
    #   `^ch_api_port_owner\(\) \{(?:.*\n)*?\n\}`，抓不到（巢狀大括號），
    #   得到 None → `AttributeError`。而這個檔案裡**早就有一個抽函式的工具**，
    #   自己重寫一份只會得到不同的行為。
    # ⚠️ 兩個坑，都要避開：
    #   · `_fns()` 回的是**函式體**（不含 `name() {` 那一行）→ 要自己補頭，
    #     否則 body 裡的 `local` 不在函式內 → `local: can only be used in a function`。
    #   · `_fns()` 的正則只抓 `ch_*`，而 `_api_port` 以 `_` 開頭 → KeyError。
    #     所以底層那個自己抽（用大括號配對，不用會漏掉巢狀的 regex）。
    def body_of(name: str) -> str:
        m = re.search(rf"^{re.escape(name)}\(\) \{{", SRC, re.M)
        assert m, f"抽不到 {name}()"
        i, depth = m.end(), 1
        while i < len(SRC) and depth:
            if SRC[i] == "{":
                depth += 1
            elif SRC[i] == "}":
                depth -= 1
            i += 1
        return SRC[m.end():i - 1].strip("\n")

    fns = "".join(f"{n}() {{\n{body_of(n)}\n}}\n" for n in ("_api_port", "ch_api_port_owner"))
    script = f"""set -euo pipefail
API_URL="http://127.0.0.1:8000"
bump() {{ printf 'RESULT %s %s\\n' "$2" "$1"; }}
{fns}
ch_api_port_owner
echo "SURVIVED"
"""
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    r = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, env=env, timeout=60)

    assert "SURVIVED" in r.stdout, (
        "lsof 回 1 時**整支腳本被 set -e 殺掉**了 —— 症狀是沒有任何輸出、"
        f"exit {r.returncode}。\n  stdout={r.stdout!r}\n  stderr={r.stderr[-300:]!r}")
    assert "RESULT" in r.stdout, \
        f"lsof 回 1 時沒有回出結論：{r.stdout!r}"
