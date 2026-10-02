"""`scripts/ensure-stack.sh` 必須「修壞的」而**不動「還在起來的」**。

## 為什麼要有這道測試

`ensure-stack.sh` 是 watchdog。watchdog 最危險的失效模式不是「沒修」，
而是**修壞的** —— 它每 10 分鐘跑一次，任何誤判都會被放大成週期性的破壞。

2026-10-02 wsl 實測到的正是後者。原版流程：

```
docker daemon 就緒 → 立刻判 /health → 不健康就 --force-recreate
```

而 `@reboot` 那時容器正由 `restart: always` 拉起來、`/health` 還沒 200
（實測冷啟動到 200 要 **8 秒**）→ watchdog 判成故障 → 重建三個容器 →
重建後確實好了 → 回報 `exit 0`「已修復」。

也就是說 watchdog **每次開機都無謂地重建整個 stack，而且回報成功**。
症狀完全看不出來：`exit 0` 看起來是最健康的結果。

## 這裡測什麼

用假的 `docker`（stub）取代真的，於是可以精確構造三種狀態而不動真實 stack：

1. 健康 → 不該有任何 `up` 呼叫
2. **還在啟動**（容器 running 但 /health 還沒 200，之後會好）→ 冷卻期內
   轉好就**不該**有 `up` 呼叫（這是那個 bug）
3. 容器不存在 → 該 `up -d`（建立），**不該** `--force-recreate`

真的 docker daemon 也要能跑（這支是三台機器的 watchdog，壞掉等於
三台都沒有防護）。
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ensure-stack.sh"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(), reason=f"{SCRIPT} 不見了"
)


def _src() -> str:
    return SCRIPT.read_text(encoding="utf-8")


# ── 靜態：流程必須是「先等，再判」而非「立刻 force-recreate」───────────────

def test_has_a_grace_period_before_declaring_failure():
    """docker 就緒之後必須先有一段冷卻期。

    Friction 點：沒有冷卻期時「還在啟動」與「壞掉」長得一模一樣 ——
    都是 /health 不回 200。而這支每 10 分鐘跑一次，於是每次開機都誤判。
    """
    assert "GRACE_SEC" in _src(), (
        "沒有冷卻期 —— 啟動中的 stack 會被當成故障而 force-recreate，"
        "而且重建後確實會好、所以回報 exit 0，症狀看不出來"
    )


def test_grace_period_polls_health_before_acting():
    """冷卻期必須是**輪詢**健康，不是 sleep 固定時間後再判一次。

    `sleep 60; if healthy` 會讓「其實 8 秒就好了」的情況多花 52 秒，
    而且在 @reboot 時把開機時間白白拖長。輪詢才是「一好就停」。
    """
    src = _src()
    i = src.index("for i in $(seq 1 \"$GRACE_SEC\")")
    seg = src[i:i + 400]
    assert "if healthy" in seg, "冷卻期裡要輪詢 healthy"
    assert "exit 0" in seg, "一旦健康就立刻返回（對正常狀態零延遲）"


def test_does_not_force_recreate_when_containers_are_absent():
    """容器不存在 → `up -d`（建立），**不可** `--force-recreate`。

    `--force-recreate` 的唯一用途是「容器存在但網路 sandbox 損壞」。
    對不存在的容器，它是「剛建好的馬上刪掉重建」—— 實測三個 ID 全換。
    """
    src = _src()
    assert "ps -aq" in src, "要能分辨「容器不存在」與「容器壞掉」"
    # 建立路徑裡不能有 --force-recreate
    i = src.index('log "偵測異常：容器不存在')
    create_branch = src[i:src.index("else", i)]
    assert "--force-recreate" not in create_branch, (
        "容器不存在時不該 force-recreate —— 那是建立，不是重建"
    )
    # 反過來，壞掉的路徑必須還在（否則修不了網路 sandbox 損壞）
    j = src.index("qdrant networks=")
    assert "--force-recreate" in src[j:j + 200], "網路損壞的路徑仍需 force-recreate"


def test_grace_period_is_longer_than_measured_cold_start():
    """冷卻期必須比實測的冷啟動時間長。

    實測是 8 秒（`docker compose down` → `up -d` → /health 200）。
    冷卻期短於它就等於沒有冷卻期 —— 那個 bug 會原封不動地回來。
    """
    m = re.search(r'GRACE_SEC="\$\{GRACE_OVERRIDE:-(\d+)\}"', _src())
    assert m, "找不到 GRACE_SEC 的預設值（格式應為 ${GRACE_OVERRIDE:-N}）"
    grace = int(m.group(1))
    assert grace >= 60, (
        f"冷卻期預設 {grace}s 比實測冷啟動 8s 的安全餘裕太小；"
        "容器多的機器可用 --grace 調大，但預設別調到比冷啟動還短"
    )


# ── 執行：假的 docker，精確構造三種狀態 ─────────────────────────────────

def _stub_env(tmp_path: Path, *, state: str):
    """造假的 `docker` 與 `curl`，回傳 (env, calls_log)。

    ⚠️ 分派要**只看 $1**。第一版寫成 `case "$1 $2 $3" in "info ")`，
    而 `$2`/`$3` 未設定時會塌成兩個空格（`info  `），pattern 對不上 →
    每個 stub 都走不到預期分支 → 八個測試有兩個紅，症狀完全看不出是
    stub 寫錯（看起來像 ensure-stack 的判斷有問題）。

    state:
      healthy  三容器 running、/health 200
      booting  三容器 running、/health 先失敗兩次再成功（模擬 8 秒後起來）
      absent   `compose ps` 回空（容器根本不存在）
    """
    bindir = tmp_path / f"bin-{state}"
    bindir.mkdir()
    calls = tmp_path / f"calls-{state}.log"
    C = str(calls)

    containers = ('echo ragdemo-api-1 running\n'
                  'echo ragdemo-postgres-1 running\n'
                  'echo ragdemo-qdrant-1 running\n')

    d = bindir / "docker"
    up_marker = f"{C}.up"
    # 條件式列出容器：
    #   healthy / booting → 一直有（那兩個狀態的前提就是容器在跑）
    #   absent           → 只有在 `up -d` 之後才有
    #     ⚠️ 第一版一律用 up 標記 gate，於是 healthy/booting 也變成「沒容器」→
    #        兩個測試紅，症狀看起來像 ensure-stack 的判斷壞了。
    if state == "absent":
        list_containers = ('if [ -f ' + up_marker + ' ]; then\n'
                           '  case "$*" in *-aq*) echo c1; echo c2; echo c3 ;;\n'
                           '              *) ' + containers + ' ;;\n'
                           '  esac\n'
                           'fi\n')
    else:
        list_containers = ('case "$*" in *-aq*) echo c1; echo c2; echo c3 ;;\n'
                           '            *) ' + containers + ' ;;\n'
                           'esac\n')

    d.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> {C}\n'
        '[ "$1" = "info" ] && exit 0\n'
        '[ "$1" != "compose" ] && exit 0\n'
        'if [ "$2" = "up" ]; then touch ' + up_marker + '; exit 0; fi\n'
        'if [ "$2" = "ps" ]; then\n' + list_containers + '  exit 0\nfi\n'
        'exit 0\n', encoding="utf-8")
    d.chmod(0o755)

    c = bindir / "curl"
    if state == "healthy":
        c.write_text(f'#!/usr/bin/env bash\necho "$*" >> {C}\nexit 0\n', encoding="utf-8")
    elif state == "booting":
        c.write_text(
            "#!/usr/bin/env bash\n"
            f"S={C}.cnt\n"
            'n=$(cat "$S" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$S"\n'
            f'echo "$*" >> {C}\n'
            '[ "$n" -le 2 ] && exit 7\n'   # 前兩次失敗 → 模擬還在啟動
            'exit 0\n', encoding="utf-8")
    else:
        # absent：建立之前 /health 一定失敗，up 之後（標記存在）才成功。
        # 讓 up 真的「修好」，否則這條會走到「重建後仍不健康」那條路徑，
        # 跑滿 15×4s = 60 秒（第一版就是這樣，測試慢到看不出它在測什麼）。
        c.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$*" >> {C}\n'
            'if [ -f ' + up_marker + ' ]; then exit 0; fi\n'
            'exit 7\n', encoding="utf-8")
    c.chmod(0o755)

    return dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}"), calls


def _run(tmp_path: Path, state: str, *extra):
    env, calls = _stub_env(tmp_path, state=state)
    r = subprocess.run(["bash", str(SCRIPT), "--cron", *extra],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    log = calls.read_text(encoding="utf-8") if calls.is_file() else ""
    return r, log


def test_healthy_stack_is_left_completely_alone(tmp_path):
    """健康的 stack 不該有任何 `up` 呼叫 —— 連讀都不該改它。"""
    r, log = _run(tmp_path, "healthy")
    assert r.returncode == 0, r.stderr
    assert "up -d" not in log, f"健康的 stack 被動了：\n{log}"


def test_booting_stack_is_not_force_recreated(tmp_path):
    """**還在啟動**的 stack 不該被 `up` —— 那個 bug 的直接重現。

    冷卻期內它會自己好，所以正確行為是什麼都不做。
    """
    r, log = _run(tmp_path, "booting")
    assert r.returncode == 0, r.stderr
    assert "up -d" not in log, (
        f"還在啟動的 stack 被當成故障重建了 —— 這是那個 bug：\n{log}"
    )


def test_absent_containers_are_created_not_recreated(tmp_path):
    """容器不存在 → `up -d` 建立，且**沒有** `--force-recreate`。"""
    # --grace 2 讓這條跑得完（預設 60 的行為由靜態斷言守住）
    r, log = _run(tmp_path, "absent", "--grace", "2")
    assert "up -d" in log, "容器不存在時應該建立起來"
    assert "--force-recreate" not in log, (
        "容器不存在時不該 force-recreate（那是建立不是重建）"
    )


# ── 真的 docker 也要能跑 ───────────────────────────────────────────────

@pytest.mark.skipif(
    shutil.which("docker") is None, reason="本機沒 docker"
)
def test_runs_against_real_docker_on_a_healthy_stack():
    """真的 docker 跑一遍健康 stack —— 這支是三台的 watchdog，不能自己壞掉。

    stub 測不出「stub 漏掉的參數」這類問題（例如 `ps -aq` 在某個 compose
    版本不支援，那會讓判斷恆為 0 → 永遠走「建立」路徑）。
    """
    # 兩個前提都要檢查，**不可只看 stack 在不在線**。
    #
    # Friction 點（2026-10-02 實測）：只有「stack 不在線就 skip」時，
    # 「.env 不存在但 stack 正在跑」這個組合會讓這條紅 ——
    # `ensure-stack.sh` 內部跑 `docker compose ps`，那需要 .env 做插值，
    # 沒有就整組失敗。CI 剛好不會踩到（CI 也沒有跑著的 stack，所以會 skip），
    # 於是這個缺口可以一直藏著。判準要寫成真正的前提，而不是間接的線索。
    if not (ROOT / ".env").is_file():
        pytest.skip("沒有 .env —— docker compose 的插值需要它（CI 乾淨 clone）")
    if subprocess.run(["curl", "-sf", "-m", "5", "-o", "/dev/null",
                      "http://127.0.0.1:8000/health"],
                     capture_output=True).returncode != 0:
        pytest.skip("本機 stack 不在線（本測試要求它健康才能驗）")
    r = subprocess.run(["bash", str(SCRIPT), "--cron"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("args,why", [
    (["--grace"], "--grace 缺值"),
    (["--grace=abc"], "非數字"),
    (["--grace=-1"], "負數"),
])
def test_rejects_a_bad_grace_value(tmp_path, args, why):
    """`--grace` 的參數驗證 —— 壞的值要明確失敗，不要變成 0 秒冷卻期。

    `GRACE_SEC=abc` 會讓 `seq 1 abc` 報錯但迴圈不跑 → 冷卻期變 0 秒
    → 那個 bug 原封不動回來，而且 exit 0 看起來正常。
    """
    env, _ = _stub_env(tmp_path, state="healthy")
    r = subprocess.run(["bash", str(SCRIPT), "--cron", *args],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 2, f"{why} 應該明確失敗:\n{r.stdout}{r.stderr}"
