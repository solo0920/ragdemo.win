import subprocess, sys
from pathlib import Path
ROOT = Path("/home/solo/projects/ragdemo.win")

def run(*a, stdin=None):
    return subprocess.run([sys.executable, str(ROOT/"scripts/env-diff-hosts.py"), *a],
                          capture_output=True, text=True, cwd=ROOT, input=stdin)

def test_needs_at_least_two():
    r = run("settings/env/host-inventory/wsl.txt")
    assert r.returncode != 0, "只有一台時應該失敗"
    assert "至少要有兩台" in r.stderr, r.stderr

def test_rejects_a_file_with_no_column_lines():
    Path("/tmp/opencode/junk.txt").write_text("這不是欄位檔\nhello\n", encoding="utf-8")
    r = run("/tmp/opencode/junk.txt", "settings/env/host-inventory/wsl.txt")
    assert r.returncode != 0, "抓不到欄位時應該失敗（否則會產出空的比對表）"
    assert "--emit-column" in r.stderr, "錯誤訊息要告訴人怎麼產生"

def test_tolerates_pasted_noise():
    """聊天貼上來的內容會夾帶引言與 code fence —— 不可因此失敗。

    ⚠️ 幽靈鍵的**真實格式**是 `FOO=SET  # 幽靈：程式碼沒讀它`（`env-inventory.py
    --emit-column` 產生的），不是 `FOO=BAR`。第一版的測試資料寫成後者，於是
    `BAR` 不符合 `SET|EMPTY|ABSENT` 而被丟掉，測試紅了 —— 但那不是工具的
    問題，是**測試沒有照真實契約寫**。抓不到不是錯，抓到才要抓對。
    """
    noisy = ("好的，結果如下：\n```\n"
             "ADMIN_TOKEN: SET\nCF_AIG_TOKEN: SET\n"
             "FOO_BAR: SET  # 幽靈：程式碼沒讀它\n```\n")
    r = run("-", "settings/env/host-inventory/wsl.txt", stdin=noisy)
    assert r.returncode == 0, r.stderr
    assert "FOO_BAR" in r.stdout, "幽靈鍵（程式碼沒讀的）要抓出來，不是忽略"
    assert "幽靈鍵" in r.stdout, "要標示出它是幽靈鍵"
    assert "stdin" not in r.stdout, "欄位名稱要有人看得懂，不是 'stdin'"

def test_shared_secret_divergence_is_red_alert():
    """共用憑證在某台不是 SET → 必須歸到紅色那一類。

    那是三台之間最嚴重的分歧：那台的 peer 探測與外部認證會失敗，
    而症狀是「查詢正常、只有連線面板紅」。
    """
    wsl = (ROOT/"settings/env/host-inventory/wsl.txt").read_text(encoding="utf-8")
    col = {}
    for l in wsl.splitlines():
        if "=" in l:
            k, v = l.split(":", 1); col[k] = v.strip()
    col["CF_AIG_TOKEN"] = "ABSENT"
    Path("/tmp/opencode/broken.txt").write_text(
        "".join(f"{k}: {v}\n" for k, v in sorted(col.items())), encoding="utf-8")
    r = run("/tmp/opencode/broken.txt", "settings/env/host-inventory/wsl.txt")
    assert r.returncode == 0, r.stderr
    assert "🔴" in r.stdout and "CF_AIG_TOKEN" in r.stdout
    i = r.stdout.index("🔴")
    seg = r.stdout[i:r.stdout.index("##", i+10)]
    assert "CF_AIG_TOKEN" in seg, "共用憑證分歧必須出現在紅色那一節"

def test_spec_proposal_is_keyed_on_role_not_current_state():
    """提案必須由鍵的**角色**決定，不是把現況照抄。

    否則規格只是把現況固化 —— 而現況可能就是問題本身
    （例如某台的共用憑證是 ABSENT，提案就寫「ABSENT」，等於認可了故障）。
    """
    r = run("settings/env/host-inventory/wsl.txt", "/tmp/opencode/broken.txt")
    seg = r.stdout[r.stdout.index("## 標準化提案"):]
    row = next(l for l in seg.splitlines() if l.startswith("| `CF_AIG_TOKEN`"))
    assert "**全部 SET**" in row, f"共用憑證的提案應是「全部 SET」，實際：{row}"
    row2 = next((l for l in seg.splitlines() if l.startswith("| `EMBED_MODEL`")), None)
    if row2:
        assert "ABSENT" in row2, "compose 寫死的鍵，提案應是 ABSENT（設了無效）"

def test_prints_the_verification_step():
    """輸出要包含驗收方式 —— 沒有驗收的規格等於沒有規格。"""
    r = run("settings/env/host-inventory/wsl.txt", "/tmp/opencode/broken.txt")
    assert "--check" in r.stdout and "版面指紋" in r.stdout, \
        "要說明怎麼驗收（三台版面指紋相同）"
