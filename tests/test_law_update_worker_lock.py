"""`scripts/law-update-worker.sh` 的互斥鎖必須在正常退出後**不留痕**。

## 這道測試為什麼存在

2026-10-02 mbp 實測抓到一個**沒有症狀**的 bug：macOS 沒有 `flock(1)`，worker
退回 `mkdir` 鎖（`:69-87`），而 pid 是寫在**鎖目錄裡面**（`:89`）。於是
`rmdir "$LOCKDIR"`（舊版 cleanup）**永遠失敗** —— `rmdir` 只能刪空目錄，
而 `2>/dev/null` 又把錯誤吞掉。

連鎖反應每分鐘一次：

```
取得鎖 → 寫 pid → :92「沒請求就退出」（每分鐘的正常情況）
       → EXIT trap 裡 rmdir 失敗 → 鎖目錄殘留
       → 下一分鐘 mkdir 失敗 → 讀到已死的 pid
       → :84 記「清除殘留鎖」→ rm -rf ＋ mkdir → 目錄被刪掉重建
```

**怎麼發現的**：比對 `data/.ops/.worker.lock.d/` 的 inode。inode 會變代表是
不同的檔案系統實體 —— 而互斥期間 inode **不該**變。`worker.log` 長到 406KB
且幾乎全是「清除殘留鎖」是第二個線索。

值得記的是 `:58-61` 的註解**已經預見這個洩漏**（「鎖就會每分鐘洩漏一次」，
因為當時知道 trap 位置不對），但只修對了 trap 位置，沒發現 `rmdir` 根本
刪不掉有 pid 的目錄。**預見了失效模式，沒預見到失效機制。**

## 為什麼在 Linux 上也測

本機有 `flock`，所以走的是 `exec 9>` 那條路、**完全不會碰到這段程式碼**。
測試因此不能靠「本機跑看看」—— 必須**強制**走 mkdir 分支。做法是給一個
沒有 `flock` 的 PATH。

這也解釋了為什麼這個 bug 能活到現在：兩台 Linux（wsl／x570）永遠走不到
`:75` 的 else 分支，**只有 macOS 會踩到**，而 macOS 那台的 opencode 要靠
比對 inode 才看得出來。
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "law-update-worker.sh"

pytestmark = pytest.mark.skipif(
    not WORKER.is_file(), reason=f"{WORKER} 不見了"
)


def _src() -> str:
    return WORKER.read_text(encoding="utf-8")


def _sandbox(tmp_path: Path) -> Path:
    """tmp 裡的假 repo：worker 只需要 scripts/ + data/.ops/ 就跑得起來。"""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    # ⚠️ 必須是 `data/.ops`（**有點**）—— worker 的 OPS 是 `$ROOT/data/.ops`。
    # 第一版寫成 `data/ops`，於是「正常退出後不留鎖」那三條**全部空過**：
    # 目錄本來就不存在，斷言「鎖不存在」當然成立。
    # 那是最壞的一種測試錯誤：綠的，但它對什麼都沒驗。
    (repo / "data" / ".ops").mkdir(parents=True)
    shutil.copy2(WORKER, repo / "scripts" / WORKER.name)
    os.chmod(repo / "scripts" / WORKER.name, 0o755)
    # 自我檢查：worker 實際會用的那個目錄必須真的在，
    # 否則下面每一條斷言都是空的。
    assert (repo / "data" / ".ops").is_dir(), "沙箱缺 data/.ops —— 測試會假通過"
    return repo


def _no_flock_path(shared: Path) -> str:
    """一個**不含 flock** 的 PATH —— 這是讓測試走 mkdir 分支的唯一開關。

    `command -v flock` 是 `:69` 的判準，所以只要 flock 不在 PATH 上，
    就會走 else 分支。不需要真的移除系統的 flock。

    用 module-scope 的 fixture 造一次就好：每次呼叫都 `mkdir()` 會撞上
    FileExistsError，而 `tmp_path` 每個測試都是新的目錄 —— 第一版就是這樣
    寫的，八個測試有兩個死在 fixture 上，症狀完全看不出是測試寫錯。
    """
    empty = shared / "path-without-flock"
    empty.mkdir(exist_ok=True)
    # ⚠️ `dirname` 一定要在：worker 的 `ROOT="$(cd "$(dirname "$0")/.." && pwd)"`
    # 用它。第一版的清單漏了它，症狀是 `line 17: dirname: command not found`
    # → ROOT 變成 `/` → log 寫到 `//data/.ops/` → 互斥測試看的是空的 log
    # → **看起來像互斥壞掉，其實是 PATH 太乾淨**。除錯時完全指向錯的方向。
    for tool in ("bash", "dirname", "pwd", "mkdir", "cat", "rm", "rmdir",
                 "date", "find", "tail", "tr", "cut", "printf", "kill"):
        src = shutil.which(tool)
        if src and not (empty / tool).exists():
            (empty / tool).symlink_to(src)
    return str(empty)


@pytest.fixture(scope="module")
def no_flock_path(tmp_path_factory):
    """module-scope：沒有 flock 的 PATH 只造一次。"""
    return _no_flock_path(tmp_path_factory.mktemp("noflock"))


def _run_worker(repo: Path, no_flock: str, *args):
    env = dict(os.environ, PATH=no_flock)   # ← 強制走 mkdir 鎖
    return subprocess.run(
        ["bash", str(repo / "scripts" / "law-update-worker.sh"), *args],
        capture_output=True, text=True, env=env, cwd=repo,
    )


def test_no_request_is_the_normal_case_and_must_leave_no_lock(tmp_path, no_flock_path):
    """**沒有請求**（=:92 的正常情況）之後，鎖必須完全消失。

    這是核心案例：cron 每分鐘都跑、每次都走這條路，所以洩漏是每分鐘一次 ——
    也是為什麼 `worker.log` 會被灌成 406KB。
    """
    repo = _sandbox(tmp_path)
    # 不建立 law-update.request → 走 :92 的 `exit 0`
    r = _run_worker(repo, no_flock_path)
    assert r.returncode == 0, r.stderr
    lock = repo / "data" / ".ops" / ".worker.lock.d"
    assert not lock.exists(), (
        "worker 正常退出後仍留下鎖目錄 —— 下一分鐘會誤判成「上一個被 kill -9」，"
        "然後把目錄刪掉重建（inode 每次都變）"
    )


def test_cleanup_removes_the_pid_file_before_rmdir(tmp_path):
    """cleanup 必須先 `rm -f pid` 再 `rmdir`。

    pid 寫在鎖目錄裡面（`:89`），所以 `rmdir` 單獨一定失敗。這個測試直接
    釘住順序 —— 若日後有人把 `rm -f pid` 「簡化」掉，這裡會紅。
    """
    src = _src()
    i = src.index("_worker_cleanup() {")
    seg = src[i:src.index("\n}", i)]
    assert "rm -f" in seg and "pid" in seg, (
        "cleanup 沒有移除 pid 檔 —— rmdir 只能刪空目錄"
    )
    assert seg.index("rm -f") < seg.index("rmdir"), (
        "順序反了：必須先刪 pid 才 rmdir"
    )


def test_repeated_runs_do_not_accumulate_lock_dirs(tmp_path, no_flock_path):
    """連跑三次都不得留下任何鎖痕跡。

    釘住「洩漏是累積的」這個性質：單次洩漏可能看起來像競態，重複洩漏
    一定是設計問題。
    """
    repo = _sandbox(tmp_path)
    for _ in range(3):
        _run_worker(repo, no_flock_path)
    ops = repo / "data" / ".ops"
    leftovers = [p.name for p in ops.iterdir() if p.name.startswith(".worker.lock")]
    assert not leftovers, f"累積殘留鎖: {leftovers}"


def test_worker_log_stays_quiet_when_there_is_no_request(tmp_path, no_flock_path):
    """沒有請求時 **worker.log 不該被寫**。

    這條是「清除殘留鎖」氾濫的另一個觀察面：406KB 的 log 幾乎全是那句，
    而那些 log 存在**唯一目的**是警告有事發生。有請求以外的寫入，就代表
    有事在發生而沒有人要它發生。
    """
    repo = _sandbox(tmp_path)
    log = repo / "data" / ".ops" / "worker.log"
    _run_worker(repo, no_flock_path)
    text = log.read_text(encoding="utf-8") if log.is_file() else ""
    assert "清除殘留鎖" not in text, (
        f"沒有請求卻記了殘留鎖清除 —— 鎖在洩漏:\n{text[:400]}"
    )


def test_lock_still_actually_mutual_excludes(tmp_path, no_flock_path):
    """修好洩漏**之後**，鎖還是要擋得住並行執行。

    這是那個反面測試：把 `rm -f pid` 加進去很容易，但若同時把互斥弄壞
    （例如整段刪掉、或讓 `LOCK_HELD` 永遠是 0），洩漏會消失而**看起來**修好了，
    實際上是兩個 worker 會同時跑 sync（重建 qdrant／刪 collection）。
    """
    # 目錄存在且有活著的 pid → 必須跳過，不該搶進來
    repo = _sandbox(tmp_path)
    ops = repo / "data" / ".ops"
    lock = ops / ".worker.lock.d"
    lock.mkdir()
    # 用目前這個 process 的 pid —— `kill -0` 會成功，代表「還活著」
    (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    r = _run_worker(repo, no_flock_path)
    assert r.returncode == 0, r.stderr
    log = ops / "worker.log"
    text = log.read_text(encoding="utf-8") if log.is_file() else ""
    assert "另一個 worker 實例執行中" in text, (
        "鎖住時卻沒記「另一個實例執行中」—— 互斥可能壞了"
    )
    assert "清除殘留鎖" not in text, (
        "pid 明明還活著卻被當成殘留鎖清掉 —— `kill -0` 的判斷有問題"
    )
