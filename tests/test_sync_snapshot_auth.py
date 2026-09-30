"""`sync-snapshot.sh` 的認證順序與「哪個端點驗證」的契約。

**這是 2026-09-30 mbp 回報的問題**：它實測發現 qdrant 的 `/healthz` **不驗證
API key**（錯的 key、甚至完全不带 header 都回 200），而 `sync-snapshot.sh` 的
第一個 preflight 打的就是它。

功能上沒有漏洞 —— 真正的把關是 `auth_precheck`（打 `/collections`），而且它在
第一個 preflight 之後**立刻**執行。危害是文件的：日後有人拿第一個 preflight 的
結果當「認證沒問題」的證據會被騙到。mbp 自己就一度誤判「認證沒生效」，
因為測的是免驗證的 `/healthz`。

所以這支檔用**真實的 qdrant** 跑，釘住三件事：
1. `/healthz` 免驗證這個事實（若哪天 qdrant 改了，這條會提醒重新評估）
2. `auth_precheck` 確實在第一個 preflight 之後、且在抓點數之前
3. 認證失敗時的訊息要分得清「沒設 PEER key」與「key 不符」
"""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts" / "sync-snapshot.sh"


def _text() -> str:
    return SYNC.read_text(encoding="utf-8")


# ── 靜態：順序與結構 ─────────────────────────────────────────────────────

def test_first_preflight_is_healthz_and_is_documented_as_unauthenticated():
    """第一個 preflight 是 `/healthz`，而它**不驗證認證** —— 這必須寫在註解裡。

    若日後有人把它改成 `/collections`（mbp 的建議），這條會紅 —— 那個改動會
    丟失「來源機離線就靜默 skip、不報錯」的行為，所以要改必須同時更新這裡。
    """
    text = _text()
    first = text.index('"$SOURCE/healthz"')
    # 該行的上方 800 字元內要有「不驗證」的說明
    window = text[max(0, first - 800):first]
    assert "不驗證" in window, (
        "/healthz 那一行上方沒有說明它不驗證認證 —— "
        "危害是日後有人拿它的成功結果當認證沒問題的證據"
    )
    assert "/collections" in window, "註解應指出哪個端點才驗證"


def test_healthz_check_still_skips_quietly_when_source_is_offline():
    """離線要 `exit 0`（靜默 skip），不是 exit 1。

    來源機臨時下線時，本機的排程不該天天報錯 —— 那是「正常的暫時狀態」，
    不是故障。改成 exit 1 會讓告警疲勞。
    """
    text = _text()
    i = text.index('"$SOURCE/healthz"')
    j = text.index("auth_precheck || exit 1", i)
    seg = text[i:j]
    assert "exit 0" in seg, "離線時要 exit 0（靜默 skip）"
    assert "exit 1" not in seg, "離線時不該 exit 1 —— 那會讓排程天天報錯"


def test_auth_precheck_runs_before_fetching_point_count():
    """**認證必須在抓點數之前。**

    `pts_of` 在認證失敗時回 `-1`。若順序反了，「key 錯了」會被誤判成
    「來源機資料變了」（-1 ≠ 舊值）而白白重抓一次完整快照 —— 那是幾十 MB
    的網路傳輸，而且會覆蓋掉本機好好的備援資料。
    """
    text = _text()
    i_auth = text.index("auth_precheck || exit 1")
    i_pts = text.index('SRC_PTS="$(pts_of')
    assert i_auth < i_pts, (
        "auth_precheck 必須在 pts_of 之前 —— "
        "pts_of 失敗回 -1，順序反了會讓認證失敗被誤判成「資料變了」而重抓快照"
    )


def test_auth_precheck_tolerates_being_called_only_once():
    """`auth_precheck` 應該只被呼叫一次（在 offline 檢查之後）。

    若有第二個呼叫點，通常是有人想「順便再確認一次」—— 那會讓每次同步多打
    一個請求。這個測試不是為了省流量，是為了讓意圖明確：要重驗就改這裡。
    """
    text = _text()
    # 扣掉函式定義那一行
    calls = text.count("auth_precheck || exit 1")
    assert calls == 1, f"auth_precheck 被呼叫 {calls} 次（預期 1 次）"


# ── 真的打 qdrant：驗證「/healthz 免驗證」這個前提 ────────────────────────

def _qdrant_up() -> bool:
    """本機 qdrant 有沒有活著。

    **用 `/healthz` 而不是 `/collections`** —— 後者需要認證，不帶 key 會回 401，
    `curl -sf` 的 rc 就不是 0，於是「qdrant 活著」被誤判成「沒跑」而全部 skip
    （第一版就這樣：4 passed / 3 skipped，那 3 條才是真正驗證前提的）。
    這也再次印證本檔的主張：`/healthz` 免驗證、`/collections` 要認證。
    """
    return subprocess.run(
        ["curl", "-sf", "-m", "3", "http://127.0.0.1:6333/healthz"],
        capture_output=True).returncode == 0


@pytest.mark.skipif(not _qdrant_up(), reason="本機 qdrant 沒跑")
@pytest.mark.parametrize("path", ["/healthz", "/collections"])
def test_qdrant_healthz_is_unauthenticated_collections_is_not(path):
    """實測釘住前提：`/healthz` 免驗證、`/collections` 驗證。

    這是 mbp 2026-09-30 的實測結果，msi 複驗。若哪天 qdrant 改了
    （例如加了 `/healthz` 的認證），這條會紅 —— 那時 `sync-snapshot.sh` 的
    註解要更新，而且「離線就靜默 skip」與「認證失敗要報錯」可以合併成一步。
    """
    r = subprocess.run(
        ["curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}",
         f"http://127.0.0.1:6333{path}",
         "-H", "api-key: definitely-not-the-real-key"],
        capture_output=True, text=True)
    code = r.stdout.strip()
    if path == "/healthz":
        assert code == "200", (
            f"/healthz 現在回 {code} 而非 200 —— qdrant 可能改成驗證了。"
            f"那 sync-snapshot.sh 的註解要更新，且第一步與 auth_precheck 可合併"
        )
    else:
        assert code in ("401", "403"), (
            f"/collections 錯的 key 應回 401/403，實得 {code} —— "
            f"若這裡也不驗證了，auth_precheck 就形同虛設，整支腳本沒有認證防線"
        )


@pytest.mark.skipif(not _qdrant_up(), reason="本機 qdrant 沒跑")
def test_auth_precheck_rejects_a_wrong_key():
    """`auth_precheck` 對錯的 key 必須回非零 —— 它是這支腳本唯一的認證關卡。

    做法：把「主流程之前」的部分抽出成一個獨立腳本，用**真實的** AUTH_H 指向
    本機 qdrant 但給錯的 key，然後只呼叫 `auth_precheck`。

    踩過兩次，都值得記：
      1. 只抽出 `auth_precheck()` 與 `log()` 兩行 → 函式需要 `$TS`／`$LOG`／
         `$SOURCE`，前導沒跟著抽出，跑出來是 usage 訊息。所以改成抽出整段前導。
      2. 抽出前導後加上 `echo "RC=$?"` 想知道回傳碼 —— **不會執行到**。
         腳本有 `set -euo pipefail`，`auth_precheck` 回 1 會讓整支腳本提早結束。
         那正是正確行為（認證不過就停，不要繼續抓快照）。所以改成看
         **process 的 returncode**，而不是在腳本裡印。
    """
    src = SYNC.read_text(encoding="utf-8")
    head = src[:src.index("# 1) source 在線？")]
    runner = head + "\nauth_precheck\n"
    p = Path("/tmp/opencode/_ap.sh")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(runner, encoding="utf-8")

    # 訊息寫進 `$HOME/qdrant/sync.log`（腳本第 32-33 行）。**隔離 HOME** ——
    # 否則測試會把「測試用的認證失敗訊息」寫進使用者真正的 sync.log，
    # 與真實的同步失敗混在一起、難以分辨（2026-09-30 第一版就這樣）。
    fake_home = Path("/tmp/opencode/_fakesync")
    fake_home.mkdir(parents=True, exist_ok=True)
    log = fake_home / "qdrant" / "sync.log"
    if log.exists():
        log.unlink()

    env = {**os.environ, "HOME": str(fake_home),
           "QDRANT_PEER_API_KEY": "definitely-not-the-real-key",
           "QDRANT_API_KEY": "definitely-not-the-real-key"}
    r = subprocess.run(["bash", str(p), "http://127.0.0.1:6333",
                        "http://127.0.0.1:6333", "laws"],
                       capture_output=True, text=True, env=env)
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    assert r.returncode != 0, (
        f"錯的 key 竟然通過了 auth_precheck（rc=0）: {r.stdout} {lines}"
    )
    assert any("QDRANT_PEER_API_KEY" in l for l in lines), (
        f"log 要點名是哪一把 key 的問題（否則使用者不知道該換哪一把）: {lines}"
    )
