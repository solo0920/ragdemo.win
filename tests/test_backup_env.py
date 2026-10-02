"""`scripts/backup-env.sh` 的行為契約。

## 為什麼只備份 2 把，不是整份 `.env`

`.env` 的 39 個鍵分三層，只有中間那層**不可重建**：

| 層 | 鍵數 | 別台有嗎 | 丟了怎樣 |
|---|---|---|---|
| 共用憑證 | 8 | 有（`pull` 寫的明文）| 走 §12b 災難復原 |
| **per-host 機密** | **2** | **沒有**（刻意不進 sops）| **永久消失**，qdrant 讀不到、pg 打不開 |
| 設定值 | 29 | 可 `render` 重建 | 重新 render |

所以備份整份 `.env` 會做兩件壞事：把 8 把共用憑證變成**第二份副本**（多一份要
輪換的東西），以及擴大不必要的曝露面。這個鍵集合的正確大小是 2。

## 為什麼加密給**自己**的公鑰

per-host 機密「不分發」是這個專案刻意維持的邊界（`env-sync.sh` 的
`PER_HOST_SECRETS` 註解：「絕不可把它們寫進 .env、絕不可加進 py_apply 的任何
layer」）。加密給別台的公鑰等於讓別台能解密它，那條線就破了。

自己解自己的在實務上夠用：本機磁碟掛掉時 age 私鑰也沒了，但私鑰另有備份，
鏈是「磁碟掛 → 從私鑰備份拿回私鑰 → 解開這份備份」。
**代價是這份備份的可靠性完全取決於私鑰備份** —— 腳本每次都提醒這件事。
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backup-env.sh"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file() or shutil.which("age") is None,
    reason="需要 scripts/backup-env.sh 與 age",
)


def _src() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _per_host() -> list[str]:
    """與 env-sync.sh 的 PER_HOST_SECRETS 同一份真相。"""
    m = re.search(r'^PER_HOST_SECRETS="([^"]+)"',
                  (ROOT / "scripts" / "env-sync.sh").read_text(encoding="utf-8"), re.M)
    assert m, "env-sync.sh 沒有 PER_HOST_SECRETS"
    return m.group(1).split()


def _sandbox(tmp_path: Path, per_host: dict[str, str]) -> Path:
    """假 repo：scripts/ ＋ 有值的 .env ＋ 一把真的 age key。"""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo / "scripts" / SCRIPT.name)
    shutil.copy2(ROOT / "scripts" / "env-sync.sh", repo / "scripts" / "env-sync.sh")
    os.chmod(repo / "scripts" / SCRIPT.name, 0o755)

    keys = tmp_path / "agekeys.txt"
    subprocess.run(["age-keygen", "-o", str(keys)], capture_output=True, check=True)
    os.chmod(keys, 0o600)

    lines = ["# fake .env", "HOST_ID=testhost"]
    lines += [f"{k}={v}" for k, v in per_host.items()]
    (repo / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return repo


def _run(repo: Path, keys: Path, *args):
    return subprocess.run(["bash", str(repo / "scripts" / SCRIPT.name), *args],
                          capture_output=True, text=True,
                          env=dict(os.environ, SOPS_AGE_KEY_FILE=str(keys)),
                          cwd=repo)


def _fp(v: str) -> str:
    import hashlib
    return f"len={len(v)} sha12={hashlib.sha256(v.encode()).hexdigest()[:12]}"


# ── 備份的內容 ─────────────────────────────────────────────────────────

def test_backup_contains_exactly_the_per_host_secrets(tmp_path):
    """備份**只能**有那 2 把。

    多的兩類都是壞的：共用憑證會變成第二份副本（多一份要輪換），
    設定值可從總表重建（多一份會與總表漂移）。
    """
    vals = {"QDRANT_API_KEY": "self-key-aaaa", "POSTGRES_PASSWORD": "pw-bbbb"}
    repo = _sandbox(tmp_path, vals)
    keys = tmp_path / "agekeys.txt"
    out = tmp_path / "out"
    r = _run(repo, keys, "--out", str(out))
    assert r.returncode == 0, r.stderr

    plain = subprocess.run(["age", "-d", "-i", str(keys),
                            str(out / "testhost-perhost-secrets.env.age")],
                           capture_output=True, text=True).stdout
    got = dict(l.split("=", 1) for l in plain.splitlines() if "=" in l)
    assert set(got) == set(_per_host()), f"備份內容應剛好是 {_per_host()}，實際 {sorted(got)}"
    for k, v in vals.items():
        assert got.get(k) == v, f"{k} 值不符"


def test_shared_secrets_are_never_copied_into_the_backup(tmp_path):
    """共用憑證即使 `.env` 裡有值，也**不可**進備份。

    理由：它們在別台的 `.env` 裡各有一份，而且值是三台輪換的。備份裡再放一份
    = 第三份副本，而輪換時最容易漏掉「不在 sops 裡的那份」——
    漏了的症狀是某台 `pull` 完仍是舊值，而 `--fingerprints` 會顯示它與眾人不符
    （至少那個會被抓到，但更好的做法是根本不要有第三份）。
    """
    vals = {"QDRANT_API_KEY": "self-key-aaaa", "POSTGRES_PASSWORD": "pw-bbbb",
            "ADMIN_TOKEN": "shared-should-not-be-here",
            "CF_AIG_TOKEN": "shared-neither"}
    repo = _sandbox(tmp_path, vals)
    keys = tmp_path / "agekeys.txt"
    out = tmp_path / "out"
    assert _run(repo, keys, "--out", str(out)).returncode == 0
    plain = subprocess.run(["age", "-d", "-i", str(keys),
                            str(out / "testhost-perhost-secrets.env.age")],
                           capture_output=True, text=True).stdout
    for shared in ("ADMIN_TOKEN", "CF_AIG_TOKEN"):
        assert shared not in plain, f"{shared} 是共用憑證，不該被複製進 per-host 備份"


# ── 守衛 ───────────────────────────────────────────────────────────────

def test_refuses_to_write_inside_the_repo(tmp_path):
    """輸出目錄在 repo 內必須拒絕。

    Friction 點：檔名 `*-perhost-secrets.env.age` 不含任何「這是密文」的線索，
    而 `git add -A` 會把它撈進去。commit 出去 = 把一份憑證副本放上公開版控。
    所以這個守衛是必要的，不是潔癖。
    """
    repo = _sandbox(tmp_path, {"QDRANT_API_KEY": "k", "POSTGRES_PASSWORD": "p"})
    keys = tmp_path / "agekeys.txt"
    r = _run(repo, keys, "--out", str(repo / "data"))
    assert r.returncode != 0
    assert "repo" in r.stderr
    assert not (repo / "data").exists() or \
        not list((repo / "data").glob("*.age")), "不該留下任何檔案"


def test_refuses_when_a_per_host_secret_has_no_value(tmp_path):
    """有鍵但沒值 → 拒絕，不要產出「看起來完整」的備份。

    缺值要等到復原當天才發現，而那正是最壞的時候 —— 你會在需要它的那一天
    才知道備份是空的。
    """
    repo = _sandbox(tmp_path, {"QDRANT_API_KEY": "k", "POSTGRES_PASSWORD": ""})
    keys = tmp_path / "agekeys.txt"
    out = tmp_path / "out"
    r = _run(repo, keys, "--out", str(out))
    assert r.returncode != 0
    assert "POSTGRES_PASSWORD" in r.stderr
    assert not out.exists() or not list(out.glob("*.age")), \
        "拒絕時不該留下備份檔 —— 否則會覆蓋掉上一份好的"


def test_output_file_is_not_world_readable(tmp_path):
    """備份檔權限 600（裡面是憑證的密文，但密文也不該隨便被讀）。"""
    repo = _sandbox(tmp_path, {"QDRANT_API_KEY": "k", "POSTGRES_PASSWORD": "p"})
    keys = tmp_path / "agekeys.txt"
    out = tmp_path / "out"
    assert _run(repo, keys, "--out", str(out)).returncode == 0
    f = out / "testhost-perhost-secrets.env.age"
    assert f.stat().st_mode & 0o077 == 0, f"權限應為 600，實際 {oct(f.stat().st_mode)}"


def test_age_header_carries_no_filename_or_timestamp(tmp_path):
    """`-a`：不把檔名/時間戳寫進 age header。

    age 預設會在 header 放 metadata（含原始檔名）。對一個**公開 repo** 的
    解密材料而言，那是免費送出去的資訊：「這是某機器的 .env 備份，最後修改
    什麼時候」。`--print-fingerprints` 的存在讓我們不需要 metadata。
    """
    assert " -a " in _src() or "-a -o" in _src() or "-a \\\n" in _src(), \
        "age 加密必須帶 -a（不寫 metadata）"
    repo = _sandbox(tmp_path, {"QDRANT_API_KEY": "k", "POSTGRES_PASSWORD": "p"})
    keys = tmp_path / "agekeys.txt"
    out = tmp_path / "out"
    assert _run(repo, keys, "--out", str(out)).returncode == 0
    blob = (out / "testhost-perhost-secrets.env.age").read_bytes()
    # header 區是開頭到第一個空行的部分（base64 區）
    head = blob.split(b"\n\n", 1)[0]
    for leak in (b"perhost", b"testhost", b".env"):
        assert leak not in head, f"age header 洩漏了 {leak!r}"


# ── 輸出不得含值 ───────────────────────────────────────────────────────

def test_never_prints_the_values(tmp_path):
    """任何輸出都不可含值 —— 這支的輸出會被貼進聊天回報。

    2026-09-26 三次憑證外洩都是「查證時列印了值」。這支的產出天生就是要回報
    的（給人確認備份是新的），所以只印 `len=` 與 `sha12=`。
    """
    secret_a, secret_b = "NEVERPRINT-KEY-aaaa", "NEVERPRINT-PW-bbbb"
    repo = _sandbox(tmp_path, {"QDRANT_API_KEY": secret_a,
                               "POSTGRES_PASSWORD": secret_b})
    keys = tmp_path / "agekeys.txt"
    r = _run(repo, keys, "--out", str(tmp_path / "out"), "--print-fingerprints")
    assert r.returncode == 0, r.stderr
    for stream in (r.stdout, r.stderr):
        assert secret_a not in stream and secret_b not in stream
        assert "NEVERPRINT" not in stream
    assert "sha12=" in r.stdout, "但要有可指紋比對的輸出"


def test_prints_the_fingerprints_when_asked(tmp_path):
    """`--print-fingerprints` 要能給人回報「這份備份是今天的值」。"""
    vals = {"QDRANT_API_KEY": "key-value-here", "POSTGRES_PASSWORD": "pw-value-here"}
    repo = _sandbox(tmp_path, vals)
    keys = tmp_path / "agekeys.txt"
    r = _run(repo, keys, "--out", str(tmp_path / "out"), "--print-fingerprints")
    assert r.returncode == 0, r.stderr
    import hashlib
    for k, v in vals.items():
        assert _fp(v) in r.stdout, f"缺少 {k} 的指紋"


def test_backup_is_named_after_the_host(tmp_path):
    """檔名帶 HOST_ID —— 三台的備份要能分辨，且不能混。

    解錯機器的備份 = 把別台的 per-host 機密灌進這台，而那台的 qdrant 就此
    打不開（值不同）。
    """
    repo = _sandbox(tmp_path, {"QDRANT_API_KEY": "k", "POSTGRES_PASSWORD": "p"})
    keys = tmp_path / "agekeys.txt"
    out = tmp_path / "out"
    assert _run(repo, keys, "--out", str(out)).returncode == 0
    assert (out / "testhost-perhost-secrets.env.age").is_file()


def test_reminds_that_the_backup_depends_on_the_private_key_backup(tmp_path):
    """每次都提醒「私鑰備份沒做 → 這份解不開」。

    因為 `--print-fingerprints` 不開時輸出很短，這句是唯一會出現在日常操作裡
    的提醒，而它是這份備份**唯一的失效點**。
    """
    repo = _sandbox(tmp_path, {"QDRANT_API_KEY": "k", "POSTGRES_PASSWORD": "p"})
    keys = tmp_path / "agekeys.txt"
    r = _run(repo, keys, "--out", str(tmp_path / "out"))
    assert "私鑰" in r.stdout, "要提醒這份備份取決於 age 私鑰備份"
