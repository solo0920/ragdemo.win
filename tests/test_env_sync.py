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

# 鏡像 env-sync.sh 的 SHARED_SECRETS（改一處必須改另一處，下面有測試鎖）。
SHARED_SECRETS = ("QDRANT_PEER_API_KEY ADMIN_TOKEN CF_AIG_TOKEN HF_TOKEN "
                  "NVIDIA_API_KEY TYPESAFE_API_KEY ZEN_API_KEY").split()
# per-host 機密：各機自己的值，**不分發**。刻意不放進 SHARED_SECRETS。
PER_HOST_SECRETS = ("QDRANT_API_KEY POSTGRES_PASSWORD").split()


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
    (env_dir / "secrets.host.env.example").write_text(
        "".join(f"{k}=\n" for k in PER_HOST_SECRETS), encoding="utf-8")
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
    decrypted.write_text("QDRANT_PEER_API_KEY=NEW-PEER\nADMIN_TOKEN=NEW-ADMIN\n",
                         encoding="utf-8")
    monkeypatch.setenv("STUB_SRC", str(decrypted))
    r = run_sync(["pull"], env_dir, dotenv, bindir)
    assert r.returncode == 0, r.stderr
    vals = parse_env(dotenv)
    assert vals["QDRANT_PEER_API_KEY"] == "NEW-PEER"
    assert vals["ADMIN_TOKEN"] == "NEW-ADMIN"
    assert vals["COLLECTION"] == "laws"           # common.env 補上缺鍵
    # per-host 機密：解密層就算有同名鍵也不該被 pull 進來（見專門測試）
    assert vals["QDRANT_API_KEY"] == "OLD-KEY"
    assert vals["POSTGRES_PASSWORD"] == "OLD-PW"
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
    decrypted.write_text("QDRANT_PEER_API_KEY=\n", encoding="utf-8")  # 空的
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
    assert vals["QDRANT_PEER_API_KEY"] == "OLD-PEER"
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


# ── per-host 機密：改分類後各機不需要做任何事（2026-09-27）──

def test_pull_never_writes_per_host_secrets(fx, tmp_path, monkeypatch):
    """pull 不得寫入 QDRANT_API_KEY／POSTGRES_PASSWORD。
    Friction 點：解密層若還含這兩個鍵（舊的 9 把加密檔就是這樣），pull 會照樣
    合併進 .env —— 那就等於「還在分發」，另外兩台的 key 會被別台的 pull 換掉。
    這裡刻意讓解密層帶著這兩個鍵，證明 pull 就算拿到也不寫。
    """
    env_dir, dotenv, bindir = fx
    decrypted = tmp_path / "decrypted.env"
    decrypted.write_text(
        "QDRANT_API_KEY=SHOULD-NOT-LAND\nPOSTGRES_PASSWORD=SHOULD-NOT-LAND\n"
        "ADMIN_TOKEN=NEW-ADMIN\n", encoding="utf-8")
    monkeypatch.setenv("STUB_SRC", str(decrypted))
    r = run_sync(["pull"], env_dir, dotenv, bindir)
    assert r.returncode == 0, r.stderr
    vals = parse_env(dotenv)
    assert vals["ADMIN_TOKEN"] == "NEW-ADMIN"          # 共用層照常生效
    assert vals["QDRANT_API_KEY"] == "OLD-KEY"         # per-host 機密原封不動
    assert vals["POSTGRES_PASSWORD"] == "OLD-PW"
    text = dotenv.read_text(encoding="utf-8")
    assert "SHOULD-NOT-LAND" not in text


def test_pull_does_not_clear_per_host_secrets_when_absent_from_layer(fx, tmp_path,
                                                                  monkeypatch):
    """**另外兩台不需要做任何事**的機器證據。
    改分類後，各機 .env 既有的這兩個鍵必須原封不動、值不變、鍵不消失。
    Friction 點：只要合併邏輯有一天改成「以 layer 為準、layer 沒有的鍵就刪掉」，
    x570/mbp 的 .env 就會在下次 pull 被清成空值 —— 而症狀是「本機 qdrant/pg
    突然認證失敗」，距離動因可能是幾天前的一次 pull，極難回推。
    """
    env_dir, dotenv, bindir = fx
    before = dict(parse_env(dotenv))
    decrypted = tmp_path / "decrypted.env"
    decrypted.write_text("ADMIN_TOKEN=NEW-ADMIN\n", encoding="utf-8")  # 兩把都不在
    monkeypatch.setenv("STUB_SRC", str(decrypted))
    r = run_sync(["pull"], env_dir, dotenv, bindir)
    assert r.returncode == 0, r.stderr
    after = parse_env(dotenv)
    for k in PER_HOST_SECRETS:
        assert k in after, f"pull 把 {k} 從 .env 刪掉了"
        assert after[k] == before[k], f"pull 改動了 {k} 的值"
    # 退一步說：就算這兩把的值是「別人給的舊值」，pull 也不該碰它
    dotenv.write_text(dotenv.read_text(encoding="utf-8").replace(
        "QDRANT_API_KEY=OLD-KEY", "QDRANT_API_KEY=some-other-hosts-value"))
    r = run_sync(["pull"], env_dir, dotenv, bindir)
    assert r.returncode == 0, r.stderr
    assert parse_env(dotenv)["QDRANT_API_KEY"] == "some-other-hosts-value"


def test_common_env_cannot_inject_per_host_secrets(fx, tmp_path, monkeypatch):
    """共用非敏感層也不准帶 per-host 機密（防線不只擋解密層）。
    Friction 點：common.env 是被追蹤的明文檔，誰加一行
    `QDRANT_API_KEY=...` 就等於把 per-host 機密變成分發值，而且 commit 出去、
    另外兩台 pull 就換掉了 —— 正是「各機不需要做任何事」這條性質的破口。
    """
    env_dir, dotenv, bindir = fx
    decrypted = tmp_path / "decrypted.env"
    decrypted.write_text("ADMIN_TOKEN=NEW-ADMIN\n", encoding="utf-8")
    monkeypatch.setenv("STUB_SRC", str(decrypted))
    (env_dir / "common.env").write_text(
        "COLLECTION=laws\nJEV_BANK_MIN=\n"
        "QDRANT_API_KEY=INJECTED\nPOSTGRES_PASSWORD=INJECTED\n", encoding="utf-8")
    r = run_sync(["pull"], env_dir, dotenv, bindir)
    assert r.returncode == 0, r.stderr
    vals = parse_env(dotenv)
    assert vals["QDRANT_API_KEY"] == "OLD-KEY"
    assert vals["POSTGRES_PASSWORD"] == "OLD-PW"
    assert "INJECTED" not in dotenv.read_text(encoding="utf-8")
    assert "QDRANT_API_KEY" in r.stderr and "POSTGRES_PASSWORD" in r.stderr


def test_check_fails_if_per_host_secret_in_shared_table(fx):
    """per-host 機密出現在被追蹤的總表 → render 不寫入、--check 硬失敗。
    Friction 點：總表是「三台同檔的真相」，放進去等於把 per-host 機密變成
    必須三台一致的東西（而且值會被 commit 出去）。render 剔除＋警告是對的
    （該機 .env 保持原值、不壞掉），但 --check 必須硬失敗 —— 只剔除的話
    --check 會回 0，問題就被藏起來了。
    """
    env_dir, dotenv, _ = fx
    table = env_dir / "hosts.shared.env"
    table.write_text(table.read_text(encoding="utf-8") + "x570_QDRANT_API_KEY=x\n"
                     "mbp_QDRANT_API_KEY=y\nmsi_QDRANT_API_KEY=z\n")
    # render（apply）：剔除、不寫入、警告
    r = run_sync(["render", "--host", "msi"], env_dir, dotenv)
    assert r.returncode == 0, r.stderr
    assert "QDRANT_API_KEY" in r.stderr
    assert parse_env(dotenv)["QDRANT_API_KEY"] == "OLD-KEY"
    # --check（check action）：硬失敗，不留「已剔除所以沒事」的假象
    r = run_sync(["--check"], env_dir, dotenv)
    assert r.returncode == 1
    assert "QDRANT_API_KEY" in r.stderr


def test_check_flags_missing_per_host_secret(fx):
    """--check 缺 per-host 機密鍵時報錯（只報鍵名，不報值）。
    Friction 點：per-host 機密「不進分發」很容易被誤讀成「不管它的死活」；
    缺了這兩把的症狀是本機 qdrant/pg 認證失敗（401／心跳失敗），
    極難回推到是環境變數沒設。
    """
    env_dir, dotenv, _ = fx
    assert run_sync(["render"], env_dir, dotenv).returncode == 0
    kept = [l for l in dotenv.read_text(encoding="utf-8").splitlines()
            if not l.startswith("QDRANT_API_KEY=")]
    dotenv.write_text("\n".join(kept) + "\n", encoding="utf-8")
    r = run_sync(["--check"], env_dir, dotenv)
    assert r.returncode == 1
    assert "QDRANT_API_KEY" in r.stdout
    assert "OLD-KEY" not in r.stdout
    # 補回去就過
    dotenv.write_text(dotenv.read_text(encoding="utf-8") + "QDRANT_API_KEY=OLD-KEY\n")
    assert run_sync(["--check"], env_dir, dotenv).returncode == 0


def test_fingerprints_marks_per_host_keys_as_not_cross_host(fx):
    """--fingerprints 的 per-host 段必須自我說明「不跨機比對」。
    Friction 點：7 把共用＋2 把 per-host 印在一起，若沒有標記，讀者會拿 9 行
    去三台互比 —— 而 per-host 的值本來就該不同，比出「不一致」就會誤開輪換工單，
    也就是這個 scope 要消除的成本又長回來。
    """
    _, dotenv, _ = fx
    r = subprocess.run(["bash", str(SYNC), "--fingerprints", str(dotenv)],
                       capture_output=True, text=True, env=dict(os.environ))
    assert r.returncode == 0, r.stderr
    assert "不跨機比對" in r.stdout
    for k in PER_HOST_SECRETS:
        line = next(l for l in r.stdout.splitlines() if l.startswith(k))
        assert "不跨機比對" in line, f"{k} 那行沒有標明不跨機比對"
    # 共用段在 per-host 段之前，且共用段不帶該標記（它就是要拿去互比的）
    idx_shared = r.stdout.index("QDRANT_PEER_API_KEY")
    idx_perhost = r.stdout.index("QDRANT_API_KEY")
    assert idx_shared < idx_perhost
    assert "不跨機比對" not in next(
        l for l in r.stdout.splitlines() if l.startswith("QDRANT_PEER_API_KEY"))
    assert "OLD-KEY" not in r.stdout and "OLD-PW" not in r.stdout


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
    """--init-secrets 只抽 7 把共用憑證；空值放行、缺鍵拒絕；暫存必清。
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
    # 只抽 7 把：per-host 機密進了就是「不分發」這條規則被破壞
    assert set(enc) == set(SHARED_SECRETS)
    assert not (set(enc) & set(PER_HOST_SECRETS))
    assert enc["QDRANT_PEER_API_KEY"] == "OLD-PEER"  # stub 是 cp，明文可驗
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


def test_init_secrets_never_exports_per_host_secrets(fx, monkeypatch):
    """--init-secrets 不得把 per-host 機密寫進加密檔，即使 .env 裡有值。
    Friction 點：--init-secrets 讀的是本機 .env，而 per-host 機密的真值就在裡面。
    一旦照「all keys in .env」去抽，per-host 機密就會被加密後分發到三台 ——
    分類又悄悄退回 9 把，而且症狀要等到下次輪換才會浮現（別台的 key 被換掉）。
    """
    env_dir, dotenv, bindir = fx
    monkeypatch.setenv("STUB_SRC", "__unused__")
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
    raw = (env_dir / "secrets.common.enc.env").read_text(encoding="utf-8")
    for k in PER_HOST_SECRETS:
        assert not re.search(rf"^{k}=", raw, re.M), f"加密檔出現 per-host 機密 {k}"
    assert "OLD-KEY" not in raw and "OLD-PW" not in raw


# ── 版控衛生（真實 repo）───────────────────────────────────

def test_script_and_example_key_lists_in_sync():
    """SHARED_SECRETS／SHARED_CONFIG／PER_HOST_SECRETS 與範本檔的鍵集合一致。
    改了一處忘改另一處＝靜默不同步， tests 在這裡擋。
    Friction 點：漏了 per-host 那一層，--check 的鍵覆蓋率會安靜地少驗兩把。
    """
    text = SYNC.read_text(encoding="utf-8")
    script_secrets = set(re.search(
        r'SHARED_SECRETS="([^"]+)"', text).group(1).split())
    script_config = set(re.search(
        r'SHARED_CONFIG="([^"]+)"', text).group(1).split())
    script_perhost = set(re.search(
        r'PER_HOST_SECRETS="([^"]+)"', text).group(1).split())

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
    assert script_perhost == keys("settings/env/secrets.host.env.example"), \
        "PER_HOST_SECRETS 與 per-host 機密範本不同步"


def test_a_key_is_claimed_by_exactly_one_layer():
    """一個鍵只能被一層認領（共用／per-host 機密）。
    Friction 點：同時出現在兩邊的話，「分發」與「不分發」互相矛盾 —— 實測症狀是
    輪換時只換到其中一台，而且是幾天後才發現（沒有任何錯誤訊息）。
    """
    text = SYNC.read_text(encoding="utf-8")
    shared = set(re.search(r'SHARED_SECRETS="([^"]+)"', text).group(1).split())
    perhost = set(re.search(r'PER_HOST_SECRETS="([^"]+)"', text).group(1).split())
    assert not (shared & perhost), f"同一鍵被兩層認領: {sorted(shared & perhost)}"
    # 對端憑證必須是共用（它是唯一有跨機讀寫關係的），本機的必須不是
    assert "QDRANT_PEER_API_KEY" in shared
    assert "QDRANT_API_KEY" in perhost and "POSTGRES_PASSWORD" in perhost


def test_per_host_secrets_never_in_shared_layers():
    """不可變量：per-host 機密不得出現在共用加密檔或 per-host 總表。
    不需要解密就能驗 —— sops 的 dotenv 輸出格式讓鍵名保持明文、只有值是 ENC[...]。
    Friction 點：這兩把曾被歸成「三台必須同值」，代價是每次輪換都要三台鎖步加重啟；
    一旦又被放回加密檔，分類就悄悄退回 9 把，而症狀要等下次輪換才浮現。
    """
    enc = (ROOT / "settings/env" / "secrets.common.enc.env").read_text(encoding="utf-8")
    for k in PER_HOST_SECRETS:
        assert not re.search(rf"^{k}=", enc, re.M), \
            f"per-host 機密 {k} 出現在共用加密檔裡（會被分發到三台）"
    table = (ROOT / "settings/env" / "hosts.shared.env").read_text(encoding="utf-8")
    for k in PER_HOST_SECRETS:
        assert not re.search(rf"^(?:x570|mbp|msi)_{k}=", table, re.M), \
            f"per-host 機密 {k} 出現在被追蹤的總表裡"


def test_no_tracked_secret_values():
    """settings/env 下被追蹤的檔，共用與 per-host 憑證鍵的值必須全空。
    hosts 範本的 TS_IP／SRC_API_URL 等非敏感佔位不在此限
    （tailnet IP 本來就寫在 ARCHITECTURE.md）。
    """
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "settings/env/"],
                             capture_output=True, text=True, check=True).stdout.split()
    assert tracked, "settings/env 下沒有被追蹤的檔案"
    secret_keys = set(SHARED_SECRETS) | set(PER_HOST_SECRETS)
    bad = []
    for rel in tracked:
        if rel.endswith(".enc.env"):
            continue  # 加密檔：內容本來就不是明文鍵值
        for line in (ROOT / rel).read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line.strip())
            if m and m.group(1) in secret_keys and m.group(2) != "":
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
    加密檔、總表、per-host 機密的鍵名宣告檔必須可追蹤。
    Friction 點：`settings/env/secrets.host.env` 這行不能寫成
    `settings/env/secrets.host.env*`，否則連 .example 一起被擋，
    --check 的鍵覆蓋率就沒有來源了（而且是靜默失效）。"""
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pat in ("settings/env/hosts/",
                "settings/env/hosts.local.env",
                "settings/env/.decrypted.*",
                "settings/env/secrets.common.env",
                "settings/env/secrets.host.env"):
        assert pat in gi, f".gitignore 缺少 {pat}"
    # 反查：加密檔、總表、per-host 鍵名宣告檔不能被 ignore
    for tracked in ("settings/env/secrets.common.enc.env",
                    "settings/env/hosts.shared.env",
                    "settings/env/secrets.host.env.example"):
        r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", tracked],
                           capture_output=True, text=True)
        assert r.returncode != 0, f"{tracked} 被 gitignore 擋掉就追蹤不了"
    # 明文 per-host 機密檔必須被擋
    r = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "settings/env/secrets.host.env"],
        capture_output=True, text=True)
    assert r.returncode == 0, "明文 settings/env/secrets.host.env 沒被 gitignore 擋住"


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
    for k in list(SHARED_SECRETS) + list(PER_HOST_SECRETS):
        assert not re.search(rf"^(?:x570|mbp|msi)_{k}=.+", table, re.M), \
            f"總表出現憑證鍵 {k} 的實值"


def test_shared_table_dsn_password_matches_the_host_it_points_at():
    """DSN 的密碼必須屬於「DSN 指的那台」的 pg，不是「本機」的 pg。

    Friction 點（2026-09-27 MSI 實測）：`POSTGRES_PASSWORD` 是**本機** pg 容器的
    密碼（每台不同）。若 DSN 指向**別台**（跨機 registry），密碼段必須是對端的
    `POSTGRES_PEER_PASSWORD`（沿用 `QDRANT_PEER_API_KEY` 慣例），用
    `${POSTGRES_PASSWORD}` 會把本機密碼塞進指向對端的 DSN，症狀是心跳持續
    `password authentication failed for user rag`，而所有設定「都看起來有」。

    ⚠️ 但反過來**也成立**，這是本測試 2026-09-27 改寫的原因：
    **指向自己**的 DSN（主機名是 compose 服務名 `postgres`，只在自己那台的容器內
    解析）就**必須**用 `${POSTGRES_PASSWORD}` —— 那不是踩坑，是正確寫法。
    當時的版本寫成「總表一律不准出現 `POSTGRES_PASSWORD`」，把這個合法寫法也擋掉，
    於是 msi 改指本機 pg 之後測試紅燈，而修法只能退回去忍受離線依賴。

    所以規則不是「不准用 `POSTGRES_PASSWORD`」，而是**「密碼要跟 DSN 指向的
    那台對得上」**：看 DSN 的主機名是不是本機的 compose 服務名。
    判別方式只認 `@postgres:` 一種寫法 —— 這是 compose.yaml 裡 service name
    唯一的解析位置，不去猜 127.0.0.1 之類的別種寫法（容器內 127.0.0.1 指的是
    容器自己，那本來就是另一個錯誤，該在別處擋）。
    """
    table = (ROOT / "settings" / "env" / "hosts.shared.env").read_text(encoding="utf-8")
    own_pw = re.compile(r"\$\{POSTGRES_PASSWORD\}")
    wrong: list[str] = []
    for raw in table.splitlines():
        m = re.match(r"^(?:x570|mbp|msi)_\w*DSN=(\S+)$", raw.strip())
        if not m:
            continue
        dsn = m.group(1)
        points_at_self = "@postgres:" in dsn
        if points_at_self != bool(own_pw.search(dsn)):
            wrong.append(raw.strip())
    assert not wrong, (
        "DSN 指向自己（主機名 postgres）時必須用 ${POSTGRES_PASSWORD}；"
        f"指向別台時必須用對端的 POSTGRES_PEER_PASSWORD。兩者寫反: {wrong}"
    )

