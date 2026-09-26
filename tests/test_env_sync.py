"""鎖住 env-sync 分層同步的正確性（sops + age，2026-09-27）。

全部用 fixture（/tmp 下的假 repo 層），不用真憑證、不用真 sops：
sops 由 PATH 上的 stub 腳本代替（--decrypt/--encrypt 只做 cp）。
每條斷言都對應一個踩過或預見的故障，見各 docstring。
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts" / "env-sync.sh"

SHARED_SECRETS = ("QDRANT_API_KEY QDRANT_PEER_API_KEY POSTGRES_PASSWORD ADMIN_TOKEN "
                  "CF_AIG_TOKEN HF_TOKEN NVIDIA_API_KEY TYPESAFE_API_KEY ZEN_API_KEY").split()


def run_sync(args, env_dir, dotenv, path_extra=""):
    env = dict(os.environ)
    env["ENV_SYNC_DIR"] = str(env_dir)
    env["ENV_SYNC_ENV"] = str(dotenv)
    if path_extra:
        env["PATH"] = path_extra + os.pathsep + env["PATH"]
    return subprocess.run(["bash", str(SYNC), *args],
                          capture_output=True, text=True, env=env)


@pytest.fixture()
def fx(tmp_path):
    """假分層：env_dir（含 common.env＋假 enc 檔）＋假 .env＋stub sops。"""
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / "common.env").write_text(
        "COLLECTION=laws\nJEV_BANK_MIN=\n", encoding="utf-8")
    (env_dir / "secrets.common.env.example").write_text(
        "".join(f"{k}=\n" for k in SHARED_SECRETS), encoding="utf-8")
    (env_dir / "secrets.common.enc.env").write_text("ENC-PAYLOAD", encoding="utf-8")
    # stub sops：--decrypt/--encrypt 都只是 cp（由 STUB_SRC/STUB_DST 決定內容）
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "sops"
    stub.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        "out=''; src=''\n"
        "while [ $# -gt 0 ]; do case \"$1\" in "
        "--output) out=\"$2\"; shift 2;; "
        "--decrypt|--encrypt) shift;; "
        "--*) shift;; "
        "*) src=\"$1\"; shift;; esac; done\n"
        "cp \"${STUB_SRC:-$src}\" \"$out\"\n", encoding="utf-8")
    stub.chmod(0o755)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# msi local\n"
        "HOST_ID=msi\n"
        "TS_IP=100.65.68.106\n"
        "LLM_MODEL=qwen3:8b\n"
        "COLLECTION=laws\n"
        "JEV_BANK_MIN=0.6\n"
        "QDRANT_API_KEY=OLD-KEY\n"
        "QDRANT_PEER_API_KEY=OLD-PEER\n"
        "POSTGRES_PASSWORD=OLD-PW\n"
        "ADMIN_TOKEN=OLD-ADMIN\n"
        "CF_AIG_TOKEN=OLD-CF\n"
        "HF_TOKEN=OLD-HF\n"
        "NVIDIA_API_KEY=OLD-NV\n"
        "TYPESAFE_API_KEY=OLD-TS\n"
        "ZEN_API_KEY=OLD-ZEN\n",
        encoding="utf-8")
    return env_dir, dotenv, str(bindir)


def parse_env(p):
    vals = {}
    for line in Path(p).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
        if m:
            vals[m.group(1)] = m.group(2)
    return vals


# ── 合併語意 ────────────────────────────────────────────────

def test_pull_updates_shared_leaves_per_machine(fx, tmp_path, monkeypatch):
    """pull 更新共用鍵、per-machine 鍵與註解原樣保留。
    Friction 點：全量覆蓋會寫壞 HOST_ID/TS_IP/LLM_MODEL，故只動分層內的鍵。
    """
    env_dir, dotenv, bindir = fx
    decrypted = tmp_path / "decrypted.env"
    decrypted.write_text("QDRANT_API_KEY=NEW-KEY\nADMIN_TOKEN=NEW-ADMIN\n",
                         encoding="utf-8")
    monkeypatch.setenv("STUB_SRC", str(decrypted))
    r = run_sync(["pull"], env_dir, dotenv, bindir)
    assert r.returncode == 0, r.stderr
    vals = parse_env(dotenv)
    assert vals["QDRANT_API_KEY"] == "NEW-KEY"
    assert vals["ADMIN_TOKEN"] == "NEW-ADMIN"
    assert vals["COLLECTION"] == "laws"          # common.env 補上缺鍵
    assert vals["HOST_ID"] == "msi"              # per-machine 不動
    assert vals["TS_IP"] == "100.65.68.106"
    assert vals["LLM_MODEL"] == "qwen3:8b"
    assert vals["POSTGRES_PASSWORD"] == "OLD-PW"  # 解密層沒有就不碰
    text = dotenv.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "# msi local"  # 註解與順序保留
    assert stat.S_IMODE(dotenv.stat().st_mode) == 0o600


def test_empty_layer_values_never_wipe(fx, tmp_path, monkeypatch):
    """layer 的空值不得清空本機真值。
    Friction 點：common.env／範本檔的值是空的，合進去等於刪除憑證。
    """
    env_dir, dotenv, bindir = fx
    decrypted = tmp_path / "decrypted.env"
    decrypted.write_text("QDRANT_API_KEY=\n", encoding="utf-8")  # 空的
    monkeypatch.setenv("STUB_SRC", str(decrypted))
    r = run_sync(["pull"], env_dir, dotenv, bindir)
    assert r.returncode == 0, r.stderr
    assert parse_env(dotenv)["QDRANT_API_KEY"] == "OLD-KEY"


def test_missing_keys_appended_under_marker(fx, tmp_path, monkeypatch):
    """本機缺的鍵附加到 MANAGED_MARK 下，而不是散落各處。"""
    env_dir, dotenv, bindir = fx
    # 先拿掉一個鍵，模擬舊 .env 缺鍵
    kept = [l for l in dotenv.read_text(encoding="utf-8").splitlines()
            if not l.startswith("TYPESAFE_API_KEY=")]
    dotenv.write_text("\n".join(kept) + "\n", encoding="utf-8")
    decrypted = tmp_path / "decrypted.env"
    decrypted.write_text("TYPESAFE_API_KEY=NEW-TS\n", encoding="utf-8")
    monkeypatch.setenv("STUB_SRC", str(decrypted))
    r = run_sync(["pull"], env_dir, dotenv, bindir)
    assert r.returncode == 0, r.stderr
    lines = dotenv.read_text(encoding="utf-8").splitlines()
    assert "TYPESAFE_API_KEY=NEW-TS" in lines
    assert lines.index("TYPESAFE_API_KEY=NEW-TS") > lines.index(
        "# --- managed by env-sync.sh (shared layers; do not edit below) ---")


# ── 不洩漏 ─────────────────────────────────────────────────

def test_fingerprints_never_print_values(fx):
    """--fingerprints 只印鍵名＋長度＋sha12，絕不能出現值本身。"""
    _, dotenv, _ = fx
    env = dict(os.environ)
    r = subprocess.run(["bash", str(SYNC), "--fingerprints", str(dotenv)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "OLD-KEY" not in r.stdout and "OLD-PW" not in r.stdout
    assert "OLD-KEY" not in r.stderr and "OLD-PW" not in r.stderr
    assert re.search(r"QDRANT_API_KEY\s+len=7\s+sha12=[0-9a-f]{12}", r.stdout)


def test_refuses_xtrace(fx):
    """bash -x 下直接拒絕（2026-09-26 三次外洩之一就是 bash -x）。"""
    env_dir, dotenv = fx[0], fx[1]
    env = dict(os.environ)
    env["ENV_SYNC_DIR"] = str(env_dir)
    env["ENV_SYNC_ENV"] = str(dotenv)
    r = subprocess.run(["bash", "-x", str(SYNC), "--check"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 1
    assert "xtrace" in r.stderr


# ── --check（不需 sops／不需解密）──────────────────────────

def test_check_pass_and_fail(fx):
    """--check 核對鍵覆蓋率：缺鍵即失敗，且只報鍵名。"""
    env_dir, dotenv, _ = fx
    r = run_sync(["--check"], env_dir, dotenv)
    assert r.returncode == 0, r.stdout
    dotenv.write_text("HOST_ID=msi\n", encoding="utf-8")
    r = run_sync(["--check"], env_dir, dotenv)
    assert r.returncode == 1
    assert "QDRANT_API_KEY" in r.stdout  # 只報鍵名
    assert "OLD" not in r.stdout


# ── --init-secrets ────────────────────────────────────────────

def test_init_secrets_roundtrip(fx, monkeypatch):
    """--init-secrets 從本機 .env 抽 9 把加密；空值放行、缺鍵拒絕；暫存必清。
    Friction 點：EXIT trap 引用已出作用域的 local 在 set -u 下報 unbound，
    且失敗時暫存殘留（2026-09-27 MSI 實測，ZEN_API_KEY 為空觸發）。
    """
    env_dir, dotenv, bindir = fx
    monkeypatch.setenv("STUB_SRC", "__unused__")
    # stub encrypt：cp 最後一個位置參數（明文 tmp）到 --output
    stub = Path(bindir) / "sops"
    stub.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        "out=''; args=()\n"
        "while [ $# -gt 0 ]; do case \"$1\" in "
        "--output) out=\"$2\"; shift 2;; "
        "--filename-override) shift 2;; "
        "--*) shift;; "
        "*) args+=(\"$1\"); shift;; esac; done\n"
        "cp \"${args[-1]}\" \"$out\"\n", encoding="utf-8")
    r = run_sync(["--init-secrets", "--force"], env_dir, dotenv, bindir)
    assert r.returncode == 0, r.stderr
    enc = parse_env(env_dir / "secrets.common.enc.env")
    assert set(enc) == set(SHARED_SECRETS)
    assert enc["QDRANT_API_KEY"] == "OLD-KEY"  # stub 是 cp，明文可驗
    assert list(env_dir.glob(".init-secrets.*")) == []  # 暫存已 shred

    # 缺鍵即拒絕，且只報鍵名
    kept = [l for l in dotenv.read_text(encoding="utf-8").splitlines()
            if not l.startswith("ADMIN_TOKEN=")]
    dotenv.write_text("\n".join(kept) + "\n", encoding="utf-8")
    r = run_sync(["--init-secrets", "--force"], env_dir, dotenv, bindir)
    assert r.returncode == 1
    assert "ADMIN_TOKEN" in r.stderr
    assert "OLD-ADMIN" not in r.stderr
    assert list(env_dir.glob(".init-secrets.*")) == []


# ── 版控衛生（真實 repo）───────────────────────────────────

def test_script_and_example_key_lists_in_sync():
    """SHARED_SECRETS／SHARED_CONFIG 與範本檔的鍵集合一致。
    改了一處忘改另一處＝靜默不同步， tests 在這裡擋。
    """
    text = SYNC.read_text(encoding="utf-8")
    script_secrets = set(re.search(
        r'SHARED_SECRETS="([^"]+)"', text).group(1).split())
    script_config = set(re.search(
        r'SHARED_CONFIG="([^"]+)"', text).group(1).split())

    def keys(p):
        out = set()
        for line in (ROOT / p).read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
            if m:
                out.add(m.group(1))
        return out

    assert script_secrets == keys("settings/env/secrets.common.env.example"), \
        "SHARED_SECRETS 與 secrets 範本不同步"
    assert script_config == keys("settings/env/common.env"), \
        "SHARED_CONFIG 與 common.env 不同步"


def test_no_tracked_secret_values():
    """settings/env 下被追蹤的檔，9 把憑證鍵的值必須全空。
    hosts 範本的 TS_IP／SRC_API_URL 等非敏感佔位不在此限
    （tailnet IP 本來就寫在 ARCHITECTURE.md）。
    """
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "settings/env/"],
                             capture_output=True, text=True, check=True).stdout.split()
    assert tracked, "settings/env 下沒有被追蹤的檔案"
    bad = []
    for rel in tracked:
        if rel.endswith(".enc.env"):
            continue  # 加密檔：內容本來就不是明文鍵值
        for line in (ROOT / rel).read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
            if m and m.group(1) in SHARED_SECRETS and m.group(2) != "":
                bad.append(f"{rel}:{m.group(1)}")
    assert not bad, f"追蹤檔含憑證明文值: {bad}"


def test_sops_config_has_msi_recipient_no_private_key():
    """.sops.yaml 有 msi 公鑰、無私鑰材料；x570/mbp 為 TODO 佔位。"""
    text = (ROOT / ".sops.yaml").read_text(encoding="utf-8")
    assert "age19et4d4etz4ptsp2s58838ffmfgzq8sfw3c775xc2gewgh74wtexqgej86q" in text
    assert "AGE-SECRET-KEY" not in text
    assert "x570" in text and "mbp" in text


def test_gitignore_blocks_plaintext_layers():
    """.gitignore 擋住 hosts 真值、解密暫存、明文 staging；加密檔必須可追蹤。"""
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pat in ("settings/env/hosts/*.env",
                "settings/env/.decrypted.*",
                "settings/env/secrets.common.env"):
        assert pat in gi, f".gitignore 缺少 {pat}"
    # 反查：加密檔不能被 ignore（否則會有人誤加整行 secrets.*）
    r = subprocess.run(["git", "-C", str(ROOT), "check-ignore",
                        "settings/env/secrets.common.enc.env"],
                       capture_output=True, text=True)
    assert r.returncode != 0, "加密檔被 gitignore 擋掉就追蹤不了"
