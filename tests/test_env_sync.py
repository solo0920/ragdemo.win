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
    """假分層：env_dir（common.env＋per-host 總表＋假 enc 檔）＋假 .env＋stub sops。"""
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / "common.env").write_text(
        "COLLECTION=laws\nJEV_BANK_MIN=\n", encoding="utf-8")
    (env_dir / "secrets.common.env.example").write_text(
        "".join(f"{k}=\n" for k in SHARED_SECRETS), encoding="utf-8")
    (env_dir / "secrets.common.enc.env").write_text("ENC-PAYLOAD", encoding="utf-8")
    # per-host 總表：schema 要求三台都有列（值可空）。故意讓 msi 的 TS_IP 與
    # .env 現值不同，這樣「render 到底有沒有跑」才測得出來。
    (env_dir / "hosts.shared.env").write_text(
        "x570_TS_IP=100.119.83.111\n"
        "mbp_TS_IP=100.64.121.9\n"
        "msi_TS_IP=100.0.0.3\n"
        "x570_LLM_MODEL=qwen3:14b\n"
        "mbp_LLM_MODEL=qwen3:14b\n"
        "msi_LLM_MODEL=qwen3:8b\n"
        "x570_HOST_NAME=x570\n"
        "mbp_HOST_NAME=mbp\n"
        "msi_HOST_NAME=MSI\n"
        "x570_HOST_MACHINE_ID=\n"
        "mbp_HOST_MACHINE_ID=\n"
        "msi_HOST_MACHINE_ID=msi-machine-id\n"
        "x570_OLLAMA_URLS=http://a:1,http://b:2\n"
        "mbp_OLLAMA_URLS=http://a:1,http://b:2\n"
        "msi_OLLAMA_URLS=http://a:1,http://b:2\n"
        "x570_OLLAMA_MODELS=\n"          # 空＝該機沿用現值
        "mbp_OLLAMA_MODELS=\n"
        "msi_OLLAMA_MODELS=qwen3:14b,qwen3:8b\n"
        "x570_POSTGRES_DSN=\n"
        "mbp_POSTGRES_DSN=\n"
        "msi_POSTGRES_DSN=postgresql://rag:${PG_PEER_PASSWORD}@100.0.0.1:5432/ragdemo\n",
        encoding="utf-8")
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
        "POSTGRES_PEER_PASSWORD=OLD-PEER-PW\n"
        "PG_PEER_PASSWORD=PEER-PW\n"
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

def test_pull_updates_shared_and_renders_this_host(fx, tmp_path, monkeypatch):
    """pull 更新共用憑證、套用「本機那一欄」的 per-host 值，且不動 HOST_ID。
    Friction 點：HOST_ID 是 render 的選擇器，若 pull 把它覆蓋掉就會自我否定；
    另外 x570/mbp 兩欄不該出現在 MSI 的 .env 裡。
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
    assert vals["COLLECTION"] == "laws"           # common.env 補上缺鍵
    assert vals["POSTGRES_PASSWORD"] == "OLD-PW"   # 解密層沒有就不碰
    # per-host：本機那一欄套用（TS_IP 從舊值被總表取代）
    assert vals["TS_IP"] == "100.0.0.3"
    assert vals["LLM_MODEL"] == "qwen3:8b"
    assert vals["HOST_NAME"] == "MSI"             # 原本 .env 沒這鍵，被補上
    assert vals["HOST_MACHINE_ID"] == "msi-machine-id"
    # 選擇器與別機的值不該被寫進來
    assert vals["HOST_ID"] == "msi"
    assert not any(k.startswith(("x570_", "mbp_")) for k in vals)
    assert "100.119.83.111" not in vals.values()
    text = dotenv.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "# msi local"  # 註解與順序保留
    assert stat.S_IMODE(dotenv.stat().st_mode) == 0o600


def test_empty_layer_values_never_wipe(fx, tmp_path, monkeypatch):
    """layer 的空值不得清空本機真值。
    Friction 點：common.env 的值是空的，合進去等於刪除憑證；
    總表某欄留空（我不確定那台的值）也不該清掉該機既有的設定。
    """
    env_dir, dotenv, bindir = fx
    decrypted = tmp_path / "decrypted.env"
    decrypted.write_text("QDRANT_API_KEY=\n", encoding="utf-8")  # 空的
    monkeypatch.setenv("STUB_SRC", str(decrypted))
    # 總表 msi 欄刻意留空的鍵（OLLAMA_MODELS 的 x570 欄是空的，這裡改成 msi 空）
    table = env_dir / "hosts.shared.env"
    table.write_text(table.read_text(encoding="utf-8").replace(
        "msi_HOST_MACHINE_ID=msi-machine-id", "msi_HOST_MACHINE_ID="))
    dotenv.write_text(dotenv.read_text(encoding="utf-8").replace(
        "HOST_ID=msi", "HOST_ID=msi\nHOST_MACHINE_ID=local-value"))
    r = run_sync(["pull"], env_dir, dotenv, bindir)
    assert r.returncode == 0, r.stderr
    vals = parse_env(dotenv)
    assert vals["QDRANT_API_KEY"] == "OLD-KEY"
    assert vals["HOST_MACHINE_ID"] == "local-value"


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


# ── render（per-host 總表 → .env）────────────────────────────

def test_render_selects_this_host_column(fx):
    """render 只挑本機那一欄，且 HOST_ID 選錯時結果完全不同（可預覽）。
    Friction 點：前綴若直接進 .env，compose 會拿不到值而回退預設；
    這裡驗證的是「表內帶前綴、.env 內不帶」這個不對稱。
    """
    env_dir, dotenv, _ = fx
    r = run_sync(["render", "--host", "x570"], env_dir, dotenv)
    assert r.returncode == 0, r.stderr
    vals = parse_env(dotenv)
    assert vals["TS_IP"] == "100.119.83.111"
    assert vals["HOST_NAME"] == "x570"
    # 別欄的值不該被寫進來
    assert "100.0.0.3" not in vals.values()
    assert "100.64.121.9" not in vals.values()
    # 輸出檔裡不得殘留任何前綴鍵
    assert not any(k.startswith(("x570_", "mbp_", "msi_")) for k in vals)


def test_render_expands_placeholder_from_local_env(fx):
    """${VAR} 由該機 .env 現值展開；這是讓總表能放 DSN 卻不存密碼的機制。"""
    env_dir, dotenv, _ = fx
    r = run_sync(["render", "--host", "msi"], env_dir, dotenv)
    assert r.returncode == 0, r.stderr
    vals = parse_env(dotenv)
    assert vals["POSTGRES_DSN"] == "postgresql://rag:PEER-PW@100.0.0.1:5432/ragdemo"
    assert "${" not in vals["POSTGRES_DSN"]


def test_render_refuses_unresolvable_placeholder(fx):
    """引用的變數為空時必須失敗且不動 .env。
    Friction 點：寫出空密碼的 DSN 比不寫更糟 —— 症狀是「設定看起來都有，
    但 registry 心跳就是失敗」，極難從症狀回推。
    """
    env_dir, dotenv, _ = fx
    dotenv.write_text(dotenv.read_text(encoding="utf-8").replace(
        "PG_PEER_PASSWORD=PEER-PW", "PG_PEER_PASSWORD="))
    before = dotenv.read_text(encoding="utf-8")
    r = run_sync(["render", "--host", "msi"], env_dir, dotenv)
    assert r.returncode == 1
    assert "PG_PEER_PASSWORD" in r.stderr
    assert "PEER-PW" not in r.stderr          # 不印值
    assert dotenv.read_text(encoding="utf-8") == before   # 沒寫半套


def test_render_incomplete_schema_fails(fx):
    """總表漏一列就失敗（寧可報錯也不要靜默少一台）。
    Friction 點：漏寫 mbp 欄會讓「改了表以為三台都生效」變成假設。
    """
    env_dir, dotenv, _ = fx
    table = env_dir / "hosts.shared.env"
    table.write_text("".join(
        l + "\n" for l in table.read_text(encoding="utf-8").splitlines()
        if not l.startswith("mbp_HOST_NAME=")))
    r = run_sync(["render", "--host", "msi"], env_dir, dotenv)
    assert r.returncode == 1
    assert "HOST_NAME" in r.stderr and "mbp" in r.stderr


def test_render_rejects_unprefixed_row(fx):
    """總表裡出現沒有機台前綴的行必須報錯（否則永遠不會被套用）。"""
    env_dir, dotenv, _ = fx
    table = env_dir / "hosts.shared.env"
    table.write_text(table.read_text(encoding="utf-8") + "TS_IP=1.2.3.4\n")
    r = run_sync(["render", "--host", "msi"], env_dir, dotenv)
    assert r.returncode == 1
    assert "TS_IP" in r.stderr


def test_render_requires_host_id(fx):
    """沒有 HOST_ID（render 的選擇器）就明確失敗，不猜。"""
    env_dir, dotenv, _ = fx
    dotenv.write_text(dotenv.read_text(encoding="utf-8").replace(
        "HOST_ID=msi\n", ""))
    r = run_sync(["render"], env_dir, dotenv)
    assert r.returncode == 1
    assert "HOST_ID" in r.stderr


def test_render_rejects_unknown_host_id(fx):
    """HOST_ID 不在 x570/mbp/msi 內就拒絕（拼錯時不會靜默用錯欄）。"""
    env_dir, dotenv, _ = fx
    dotenv.write_text(dotenv.read_text(encoding="utf-8").replace(
        "HOST_ID=msi", "HOST_ID=ms1"))
    r = run_sync(["render"], env_dir, dotenv)
    assert r.returncode == 1
    assert "ms1" in r.stderr


def test_render_enforces_paired_list_length(fx):
    """OLLAMA_MODELS 與 OLLAMA_URLS 位置對應，長度不一致必須擋。
    Friction 點：rag.py:215 用 index i 取對應模型，長度不符不會報錯，
    只會「某台 ollama 拿到別台的模型」—— 症狀是 8b 機器被餵 14b 而 OOM。
    """
    env_dir, dotenv, _ = fx
    table = env_dir / "hosts.shared.env"
    table.write_text(table.read_text(encoding="utf-8").replace(
        "msi_OLLAMA_MODELS=qwen3:14b,qwen3:8b", "msi_OLLAMA_MODELS=qwen3:14b"))
    r = run_sync(["render", "--host", "msi"], env_dir, dotenv)
    assert r.returncode == 1
    assert "OLLAMA_MODELS" in r.stderr and "OLLAMA_URLS" in r.stderr


def test_render_paired_length_uses_effective_values(fx):
    """半填也要驗：總表只給 OLLAMA_URLS 時，拿 .env 現值的 OLLAMA_MODELS 比。
    Friction 點：只在兩個鍵都「由本次 render 寫入」時檢查，會漏掉這種情形。
    """
    env_dir, dotenv, _ = fx
    table = env_dir / "hosts.shared.env"
    table.write_text(table.read_text(encoding="utf-8").replace(
        "msi_OLLAMA_MODELS=qwen3:14b,qwen3:8b\n", ""))
    dotenv.write_text(dotenv.read_text(encoding="utf-8") + "OLLAMA_MODELS=qwen3:14b\n")
    r = run_sync(["render", "--host", "msi"], env_dir, dotenv)
    assert r.returncode == 1
    assert "OLLAMA_MODELS" in r.stderr


def test_dry_run_prints_key_names_only(fx):
    """--dry-run 不寫檔、不印值。"""
    env_dir, dotenv, _ = fx
    before = dotenv.read_text(encoding="utf-8")
    r = run_sync(["render", "--host", "msi", "--dry-run"], env_dir, dotenv)
    assert r.returncode == 0, r.stderr
    assert "100.0.0.3" not in r.stdout and "100.0.0.3" not in r.stderr
    assert "OLD-PW" not in r.stdout and "OLD-PW" not in r.stderr
    assert "TS_IP" in r.stdout
    assert dotenv.read_text(encoding="utf-8") == before


def test_check_detects_per_host_drift(fx):
    """--check 抓「.env 的 per-host 值與總表不同」並只報鍵名。"""
    env_dir, dotenv, _ = fx
    assert run_sync(["render"], env_dir, dotenv).returncode == 0
    assert run_sync(["--check"], env_dir, dotenv).returncode == 0
    dotenv.write_text(dotenv.read_text(encoding="utf-8").replace(
        "TS_IP=100.0.0.3", "TS_IP=999.999.999.999"))
    r = run_sync(["--check"], env_dir, dotenv)
    assert r.returncode == 1
    assert "TS_IP" in r.stderr
    assert "999.999.999.999" not in r.stderr and "100.0.0.3" not in r.stderr


def test_check_fails_before_first_render(fx):
    """還沒 render 過就 --check 必須失敗（否則檢查形同虛設）。"""
    env_dir, dotenv, _ = fx
    r = run_sync(["--check"], env_dir, dotenv)
    assert r.returncode == 1
    assert "TS_IP" in r.stderr


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
    """--check 核對鍵覆蓋率與 per-host 漂移：任一項不符即失敗，且只報鍵名。

    這裡刻意先 render 一次再 --check：fixture 的 .env 是「還沒 render 過」的
    狀態，漂移檢查本來就該擋（這也是為什麼 --check 與 render 要成對）。
    """
    env_dir, dotenv, _ = fx
    assert run_sync(["render"], env_dir, dotenv).returncode == 0
    r = run_sync(["--check"], env_dir, dotenv)
    assert r.returncode == 0, r.stderr
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
    """.gitignore 擋住另立的 per-host 真值檔、解密暫存、明文 staging；
    加密檔與總表必須可追蹤。"""
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pat in ("settings/env/hosts/",
                "settings/env/hosts.local.env",
                "settings/env/.decrypted.*",
                "settings/env/secrets.common.env"):
        assert pat in gi, f".gitignore 缺少 {pat}"
    # 反查：加密檔與總表不能被 ignore（否則 per-host 值無處可追蹤）
    for tracked in ("settings/env/secrets.common.enc.env",
                    "settings/env/hosts.shared.env"):
        r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", tracked],
                           capture_output=True, text=True)
        assert r.returncode != 0, f"{tracked} 被 gitignore 擋掉就追蹤不了"


def test_shared_table_schema_and_secret_freedom():
    """真實總表：每鍵三台都有列、前綴合法、值裡沒有憑證。
    Friction 點：總表是唯一被追蹤的 per-host 真相，schema 缺一列＝某台永遠
    拿不到值；POSTGRES_DSN 內嵌共用密碼，寫死就是新的 401 等級事故。
    """
    table = (ROOT / "settings/env" / "hosts.shared.env").read_text(encoding="utf-8")
    rows = {}
    for line in table.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line)
        assert m, f"非 KEY=VALUE 的可賦值行: {line!r}"
        k, v = m.group(1), m.group(2)
        host, _, base = k.partition("_")
        assert host in ("x570", "mbp", "msi"), f"前綴不合法: {k}"
        assert base, f"前綴後沒有鍵名: {k}"
        rows.setdefault(base, set()).add(host)
    assert rows, "總表沒有任何列"
    for base, hosts in sorted(rows.items()):
        assert hosts == {"x570", "mbp", "msi"}, \
            f"總表 {base} 缺列: {sorted({'x570', 'mbp', 'msi'} - hosts)}"

    # 內嵌憑證的形狀：postgresql://user:password@host —— 密碼段必須是 ${...} 佔位
    for m in re.finditer(r"^(?:x570|mbp|msi)_\w*DSN=(\S+)", table, re.M):
        pw = m.group(1).split("://", 1)[-1].split("@", 1)[0]
        pw = pw.split(":", 1)[1] if ":" in pw else ""
        assert re.fullmatch(r"\$\{[A-Za-z_][A-Za-z_0-9]*\}", pw), \
            "DSN 的密碼段必須是 ${VAR} 佔位，不得寫死憑證"
    for k in SHARED_SECRETS:
        assert not re.search(rf"^(?:x570|mbp|msi)_{k}=.+", table, re.M), \
            f"總表出現憑證鍵 {k} 的實值"


def test_shared_table_dsn_never_expands_own_pg_password():
    """總表的 DSN 不得引用 ${POSTGRES_PASSWORD}。
    Friction 點（2026-09-27 MSI 實測）：POSTGRES_PASSWORD 是**本機** pg 容器的
    密碼，而 DSN 指向 x570。兩者指紋不同（32 字元、值不同）。若用
    ${POSTGRES_PASSWORD} 展開，會把本機密碼塞進指向 x570 的 DSN，症狀是
    registry 心跳持續 password authentication failed，而所有設定「都看起來有」。
    正確做法是引用對端的 POSTGRES_PEER_PASSWORD（沿用 QDRANT_PEER_API_KEY 慣例）。
    """
    table = (ROOT / "settings/env" / "hosts.shared.env").read_text(encoding="utf-8")
    offenders = re.findall(r"^(?:x570|mbp|msi)_\w*DSN=.*POSTGRES_PASSWORD.*$",
                           table, re.M)
    assert not offenders, f"DSN 引用了本機 pg 密碼: {offenders}"

