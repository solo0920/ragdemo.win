"""`scripts/env-diff-hosts.py`（三機往返的比對端）。

## 為什麼這些測試全部自造資料

比對工具吃的是「各機回報的欄位」。若直接拿真實 `.env` 當輸入，就會踩到
CI 常見坑：CI 是乾淨 clone、**沒有 `.env`**，於是測試在這台全綠、在 CI 紅。
所以這裡只用兩樣東西：

* `settings/env/host-inventory/wsl.txt` —— **已被版控**的欄位快照（沒有值）
* 在它上面做出各種變體（缺一把憑證、多一個幽靈鍵…）

## 為什麼 `ROOT` 必須從 `__file__` 推

第一版是 `cp /tmp/xxx.py tests/` 抄過來的，裡面留著
`Path("/home/solo/projects/ragdemo.win")`。本機全綠，CI 紅
（`FileNotFoundError: '/home/solo/projects/ragdemo.win'`）—— 那是 CI 在
別的路徑。**測試檔裡的絕對路徑是最容易漏到 CI 的東西**，因為它在本機是對的。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "env-diff-hosts.py"
WSL_COL = ROOT / "settings" / "env" / "host-inventory" / "wsl.txt"

COL_RE = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*)\s*:\s*(SET|EMPTY|ABSENT)\b")


def run(*args, stdin=None, col_dir=None):
    """跑 `env-diff-hosts.py`。`col_dir` 指定「repo 裡已存在的快照目錄」。

    ⚠️ 2026-10-03：**預設必須指向一個 tmp 目錄，不能用 repo 真的那個。**

    那個腳本會**自動補上 `host-inventory/` 裡已存在的快照**，所以「只傳一個檔」
    這個前提**只在 repo 恰好只有一份快照時成立**。實測：x570／mbp 提交自己的
    `host-inventory/<host>.txt` 之後，依賴那個前提的兩條測試**在兩台都紅**，
    而 wsl 還綠 —— **症狀是「測試紅，但紅的原因與被測的邏輯無關」**。

    而這不是那兩台造成的：一旦它們的 commit 落地，**wsl 也會跟著紅**。
    所以預設用 tmp（空的），讓「只有一台」這個情境由測試自己建立，
    而不是我碰巧處在只有一份快照的 repo 裡。
    """
    env = dict(os.environ)
    if col_dir is not None:
        env["ENV_DIFF_HOSTS_COL_DIR"] = str(col_dir)
    else:
        env.pop("ENV_DIFF_HOSTS_COL_DIR", None)
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=ROOT,
                          input=stdin, env=env)


def _isolated_col_dir(tmp_path: Path) -> Path:
    """一個**空的**欄位目錄 —— 模擬「repo 只有傳進來的那一份」。"""
    d = tmp_path / "host-inventory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def base_column() -> dict[str, str]:
    """版控在案的 wsl 欄位快照。"""
    if not WSL_COL.is_file():
        raise AssertionError(
            f"{WSL_COL} 不存在 —— 比對工具的輸入來源。"
            f"產生：python3 scripts/env-inventory.py --emit-column > {WSL_COL}")
    col = {}
    for line in WSL_COL.read_text(encoding="utf-8").splitlines():
        m = COL_RE.match(line.strip())
        if m:
            col[m.group(1)] = m.group(2)
    assert col, f"{WSL_COL} 抓不到任何欄位行"
    return col


def write_col(path: Path, col: dict[str, str]) -> Path:
    path.write_text("".join(f"{k}: {v}\n" for k, v in sorted(col.items())),
                    encoding="utf-8")
    return path


def test_needs_at_least_two(tmp_path):
    """只有一台時要明確拒絕 —— 產出「全部一致」的表會讓人以為比對過了。

    ⚠️ 2026-10-03：原本**沒有** `col_dir`，所以它測的是「repo 現在只有一份快照」。
      x570／mbp 提交自己的快照後這條在兩台都紅 —— 而紅的原因與腳本無關。
      現在用 tmp 的空目錄，讓情境由測試自己建立。
    """
    r = run("settings/env/host-inventory/wsl.txt",
            col_dir=_isolated_col_dir(tmp_path))
    assert r.returncode != 0, "只有一台時應該失敗"
    assert "至少要有兩台" in r.stderr, r.stderr


def test_rejects_a_file_with_no_column_lines(tmp_path):
    """抓不到欄位時要失敗 —— 否則會產出一張空的比對表，看起來像「沒分歧」。"""
    junk = tmp_path / "junk.txt"
    junk.write_text("這不是欄位檔\nhello\n", encoding="utf-8")
    r = run(str(junk), "settings/env/host-inventory/wsl.txt")
    assert r.returncode != 0, "抓不到欄位時應該失敗"
    assert "--emit-column" in r.stderr, \
        "錯誤訊息要告訴人怎麼產生（否則對方會去猜格式）"


def test_tolerates_pasted_noise():
    """聊天貼上來的內容會夾帶引言與 code fence —— 不可因此失敗。

    寬鬆解析的理由：這份檔會經過聊天／issue／手機輸入。嚴格解析會讓人得先
    手工整理才敢貼，而那個步驟幾乎沒人會做 —— 結果就是往返卡住。

    ⚠️ 幽靈鍵的**真實格式**是 `FOO_BAR: SET  # 幽靈：…`（`--emit-column` 產生的）。
    第一版的測試資料寫成 `FOO=BAR`，於是 `BAR` 不符合 `SET|EMPTY|ABSENT` 被丟掉，
    測試紅了 —— 但那不是工具的問題，是**測試沒有照真實契約寫**。
    """
    noisy = ("好的，結果如下：\n```\n"
             "ADMIN_TOKEN: SET\nCF_AIG_TOKEN: SET\n"
             "FOO_BAR: SET  # 幽靈：程式碼沒讀它\n```\n")
    r = run("-", "settings/env/host-inventory/wsl.txt", stdin=noisy)
    assert r.returncode == 0, r.stderr
    assert "FOO_BAR" in r.stdout, "幽靈鍵（程式碼沒讀的）要抓出來，不是忽略"
    assert "幽靈鍵" in r.stdout, "要標示出它是幽靈鍵"
    assert "stdin" not in r.stdout, "欄位名稱要有人看得懂，不是 'stdin'"


def test_shared_secret_divergence_is_red_alert(tmp_path):
    """共用憑證在某台不是 SET → 必須歸到紅色那一類。

    那是三台之間最嚴重的分歧：那台的 peer 探測與外部認證會失敗，而症狀是
    「查詢正常、只有連線面板紅」—— 不會有任何錯誤訊息指向它。
    """
    col = base_column()
    col["CF_AIG_TOKEN"] = "ABSENT"
    other = write_col(tmp_path / "x570.txt", col)
    r = run(str(other), "settings/env/host-inventory/wsl.txt")
    assert r.returncode == 0, r.stderr
    i = r.stdout.index("🔴")
    seg = r.stdout[i:r.stdout.index("\n##", i + 10)]
    assert "CF_AIG_TOKEN" in seg, "共用憑證分歧必須出現在紅色那一節"


def test_spec_proposal_is_keyed_on_role_not_current_state(tmp_path):
    """**提案必須由鍵的角色決定，不是把現況照抄。**

    否則規格只是把現況固化 —— 而現況可能就是問題本身：某台共用憑證是 ABSENT
    時，照抄的提案就會寫「ABSENT」，等於把故障認可成規格。
    """
    col = base_column()
    col["CF_AIG_TOKEN"] = "ABSENT"          # 現況：缺一把
    other = write_col(tmp_path / "x570.txt", col)
    r = run(str(other), "settings/env/host-inventory/wsl.txt")
    assert r.returncode == 0, r.stderr
    seg = r.stdout[r.stdout.index("## 標準化提案"):]
    row = next(l for l in seg.splitlines() if l.startswith("| `CF_AIG_TOKEN`"))
    assert "**全部 SET**" in row, \
        f"共用憑證的提案應是「全部 SET」（不管現在誰缺），實際：{row}"
    row2 = next((l for l in seg.splitlines()
                 if l.startswith("| `EMBED_MODEL`")), None)
    if row2:
        assert "ABSENT" in row2, "compose 寫死的鍵，提案應是 ABSENT（設了無效）"


def test_prints_the_verification_step(tmp_path):
    """輸出要包含驗收方式 —— 沒有驗收的規格等於沒有規格。"""
    col = base_column()
    col["CF_AIG_TOKEN"] = "ABSENT"
    other = write_col(tmp_path / "x570.txt", col)
    r = run(str(other), "settings/env/host-inventory/wsl.txt")
    assert "--check" in r.stdout and "版面指紋" in r.stdout, \
        "要說明怎麼驗收（三台版面指紋相同）"


def test_missing_hosts_are_named_not_silently_skipped(tmp_path):
    """少一台時要**指名**缺哪台，並給出取得它的指令。

    靜默跳過的後果：比對表看起來完整（三欄），但其中一欄其實是「沒資料」——
    那正是「以為比對過了」的形狀。

    ⚠️ 這條只給一台，卻預期它在**報缺哪幾台**之後才拒絕。所以順序很重要：
    先指名缺口，再說「至少兩台」—— 反過來的話，使用者只看到「給兩台」，
    不知道該去問誰。

    ⚠️ 2026-10-03：同 `test_needs_at_least_two` —— 必須用 tmp 的空目錄，否則
      這條測的是「repo 有幾份快照」。它在 x570／mbp 上紅、wsl 上綠。
    """
    r = run("settings/env/host-inventory/wsl.txt",
            col_dir=_isolated_col_dir(tmp_path))
    assert r.returncode != 0
    assert "x570" in r.stderr and "mbp" in r.stderr, \
        f"要指名缺哪幾台，實際：{r.stderr}"
    assert "還缺" in r.stderr, "要有明確的『還缺』字樣，不是順帶提到"


def test_env_diff_hosts_does_not_modify_anything(tmp_path):
    """**這支工具不可執行任何修改** —— 它不在任何一台機器上跑。

    理由與 `--emit-column` 相同：`.env` 有 8 份憑證，不能離開那台機器；
    而「哪個鍵有沒有值」不敏感，所以可以回報。這支的產物是**規格**，
    不是變更。真要套用是各台自己跑 `env-relayout.py`。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    for danger in ('write_text(', 'shutil.', 'os.remove', 'os.unlink',
                   'open(', 'subprocess.run(["bash"', 'subprocess.run(["sh"'):
        assert danger not in src, (
            f"env-diff-hosts.py 出現 {danger!r} —— 這支只該產生文字報表，"
            f"不該碰任何檔案或執行外部指令")
    # 唯一允許的 subprocess 是讀 env-audit.py（同一個 repo 的 Python）
    assert src.count("subprocess") == 0 or "importlib" in src, \
        "只該用 importlib 載入 env-audit.py，不要 spawn 任何東西"
