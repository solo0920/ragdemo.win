"""`scripts/rotate-secret.sh` 的行為契約（2026-09-30 新增）。

為什麼要測這支腳本：它是**唯一一支會改寫共用憑證加密檔**的工具，而那個檔案
一改錯就是三台一起壞（症狀是 pull 解不開、而 sops 的錯誤訊息
`no identity matched any of the recipients` 同時涵蓋「名單沒我」與
「我沒私鑰」兩種完全不同的原因 —— 2026-09-30 msi 重灌就撞上後者）。

沒有測試的話，「改壞了」與「沒事」的差別要等到真的輪換時才看得出來。

## 測試用真實的 sops，不是 mock

這支腳本的價值就在「sops 的那些地雷」——`--filename-override` 必要性、
mktemp 副檔名導致格式猜錯、`sops -e` 會重跑 creation_rules。
mock 掉 sops 就等於測不到真正會出錯的地方。

## 為什麼要有 ROTATE_SECRET_ROOT_OVERRIDE

第一版測試直接跑 `scripts/rotate-secret.sh`，而那支腳本的路徑是從自身位置
推導 ROOT 的 —— 於是「測試輪換」真的把測試值寫進了**版控中的加密檔**。
症狀只有比對指紋才發現，因為那個檔案是加密的，`git diff` 看不出值變了。

現在測試在 tmp 裡造完整的假 repo（`.sops.yaml` ＋ `scripts/env-sync.sh` ＋
`settings/env/`），用 override 指過去，真實檔案完全不被觸碰。
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rotate-secret.sh"
OVERRIDE = "ROTATE_SECRET_ROOT_OVERRIDE"

pytestmark = pytest.mark.skipif(
    shutil.which("sops") is None, reason="需要 sops（見 settings/env/README.md §11）"
)


def _keyfile() -> Path:
    return Path(os.environ.get("SOPS_AGE_KEY_FILE",
                               Path.home() / ".config/sops/age/keys.txt"))


def _have_key() -> bool:
    return _keyfile().is_file()


def _sops_env() -> dict:
    return dict(os.environ, SOPS_AGE_KEY_FILE=str(_keyfile()))


def _run(args, env=None, stdin=None, cwd=None):
    return subprocess.run([str(SCRIPT), *args], input=stdin, capture_output=True,
                          text=True, env=env or os.environ, cwd=cwd)


# ── 沙箱 ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sandbox(tmp_path):
    """tmp 裡的假 repo。真實的 settings/env/ 完全不被觸碰。"""
    if not _have_key():
        pytest.skip(f"沒有 age 私鑰（{_keyfile()}）")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "settings" / "env").mkdir(parents=True)

    pub = subprocess.run(["age-keygen", "-y", str(_keyfile())],
                         capture_output=True, text=True).stdout.strip()
    (tmp_path / ".sops.yaml").write_text(
        "creation_rules:\n"
        "  - path_regex: settings/env/secrets\\..*\\.enc\\.env$\n"
        "    age:\n"
        f"      - {pub}\n", encoding="utf-8")

    # env-sync.sh 是 SHARED_SECRETS / PER_HOST_SECRETS 的唯一真相
    src = (ROOT / "scripts" / "env-sync.sh").read_text(encoding="utf-8")
    m = re.search(r'^SHARED_SECRETS="([^"]+)"', src, re.M)
    (tmp_path / "scripts" / "env-sync.sh").write_text(src, encoding="utf-8")
    shared = m.group(1).split()

    env = _sops_env()
    enc = tmp_path / "settings" / "env" / "secrets.common.enc.env"
    plain = tmp_path / "settings" / "env" / ".plain"
    vals = {k: f"OLD-{k}" for k in shared}
    plain.write_text("".join(f"{k}={v}\n" for k, v in vals.items()), encoding="utf-8")
    # ⚠️ 必須給 --output：`sops -e` 沒給時把結果寫到 **stdout**，不寫檔案。
    #   第一版漏了這個參數，fixture 靜靜地沒產生 enc 檔，於是 11 條測試
    #   全部以「no such file」失敗 —— 症狀完全不像「fixture 少一步」。
    r = subprocess.run(
        ["sops", "-e", "--filename-override", str(enc),
         "--input-type", "dotenv", "--output-type", "dotenv",
         "--output", str(enc), str(plain)],
        capture_output=True, text=True, env=env, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert enc.is_file(), f"sops -e 沒產生檔案（stderr: {r.stderr}）"
    plain.unlink()
    (tmp_path / "settings" / "env" / "secrets.common.env.example").write_text(
        "".join(f"{k}=\n" for k in shared), encoding="utf-8")

    return {"root": tmp_path, "enc": enc, "shared": shared, "env": env,
            "vals": vals}


def _decrypt(enc: Path, env: dict) -> dict:
    r = subprocess.run(
        ["sops", "-d", "--filename-override", str(enc),
         "--input-type", "dotenv", str(enc)],
        capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    out = {}
    for line in r.stdout.splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", line)
        if m and not m.group(1).startswith("sops"):
            out[m.group(1)] = m.group(2)
    return out


def _rot(sb, key, value, *extra):
    # 沒指定值來源就補 --from-stdin。**不要**寫成 `extra or ("--from-stdin",)` ——
    # 那會讓 `--add` 這類「只加旗標」的呼叫把值來源一起吃掉，症狀是腳本印
    # usage（回傳 1），而錯誤訊息裡完全看不出是測試呼叫寫錯了。
    if not {"--from-stdin", "--from-file"} & set(extra):
        extra = (*extra, "--from-stdin")
    return _run([key, *extra],
                env=dict(sb["env"], **{OVERRIDE: str(sb["root"])}), stdin=value)


def _drop_from_enc(sb, key):
    """把一把從加密檔拿掉，但**留在 SHARED_SECRETS 與 example 裡**。

    這就是 2026-10-02 CF_ACCESS_CLIENT_* 的實況：鍵名已宣告、程式已在讀，
    但真值還沒進 sops 層 —— 而那正是「新增」這條路徑存在的理由。
    刻意不從 SHARED_SECRETS 移除：真正的分類決定由人做，這裡只模擬
    「宣告完成、真值待補」那個中間狀態。
    """
    plain = sb["root"] / "settings" / "env" / ".rebuild"
    plain.write_text("".join(f"{k}={v}\n" for k, v in sb["vals"].items()
                             if k != key), encoding="utf-8")
    r = subprocess.run(
        ["sops", "-e", "--filename-override", str(sb["enc"]),
         "--input-type", "dotenv", "--output-type", "dotenv",
         "--output", str(sb["enc"]), str(plain)],
        capture_output=True, text=True, env=sb["env"], cwd=sb["root"])
    assert r.returncode == 0, r.stderr
    plain.unlink()
    assert key not in _decrypt(sb["enc"], sb["env"])
    assert key in sb["shared"], "必須仍在 SHARED_SECRETS 裡，否則就不是 --add 的情境"


# ── 不需要私鑰的 CLI 行為 ────────────────────────────────────────────────

def test_list_and_help_do_not_require_private_key():
    """`--list` / `--help` 不該因為沒有私鑰就失敗 —— 它們只是列出鍵名。"""
    env = dict(os.environ, SOPS_AGE_KEY_FILE="/nonexistent/keys.txt")
    for args in (["--list"], ["--help"]):
        r = _run(args, env=env)
        assert r.returncode == 0, r.stderr
    assert "ADMIN_TOKEN" in _run(["--list"]).stdout


def test_usage_snippet_does_not_leak_shell_code():
    """`--help` 不可印出 `set -euo pipefail` 這種程式碼行。

    Friction 點：常見的 `sed -n '2,/^set -euo/p'` 寫法會把那行一起印出來
    （2026-09-30 第一版就是這樣，被自己看到才改掉）。
    """
    out = _run(["--help"]).stdout
    assert "set -euo" not in out
    assert "usage:" in out


def test_rejects_per_host_secret():
    """per-host 兩把不在加密檔裡，必須明確拒絕而不是嘗試。

    若不擋，使用者會得到「輪了但沒換掉任何東西」的假成功 —— 而那正是
    2026-09-26「本機 200、遠端 401」不對稱故障能長期存在的原因。
    """
    r = _run(["QDRANT_API_KEY", "--from-stdin"], stdin="x")
    assert r.returncode != 0
    assert "not a shared secret" in r.stderr


def test_rejects_unknown_key():
    """未知鍵要拒絕。用完整比對而不是 grep -q，否則 ADMIN 會誤中 ADMIN_TOKEN。"""
    r = _run(["NO_SUCH_KEY", "--from-stdin"], stdin="x")
    assert r.returncode != 0
    assert "not a shared secret" in r.stderr


def test_requires_source_option():
    """沒給 --from-stdin / --from-file 要印 usage 並失敗。"""
    r = _run(["ADMIN_TOKEN"])
    assert r.returncode != 0
    assert "usage:" in r.stdout


def test_missing_private_key_error_distinguishes_two_causes(sandbox):
    """沒有私鑰時的訊息必須**點出 sops 那句話有歧義**。

    Friction 點：sops 報的 `no identity matched any of the recipients` 聽起來
    像是「.sops.yaml 的 recipients 沒有我」，但「我在名單裡、私鑰在重灌時
    掉了」會報**完全一樣的句子**。2026-09-30 msi 就是後者，診斷繞了半小時。
    這條釘住「錯誤訊息要能導向正確的排查方向」。
    """
    env = dict(sandbox["env"], SOPS_AGE_KEY_FILE="/nonexistent/keys.txt",
               **{OVERRIDE: str(sandbox["root"])})
    r = _run(["ADMIN_TOKEN", "--from-stdin"], env=env, stdin="x")
    assert r.returncode != 0
    assert "no age private key" in r.stderr
    assert "no identity matched" in r.stderr, "訊息要引用 sops 那句有歧義的錯誤"
    assert "recipients" in r.stderr


# ── 真實 sops 流程 ──────────────────────────────────────────────────────

def test_rotates_only_the_target_key(sandbox):
    """換一把之後：目標鍵是新值，**其餘每把都不變**。

    這是這支腳本最核心的性質。用 --init-secrets 式的「重建」會連沒換的
    那些一起動，而那正是要避免的（會把 6 把沒曝露的憑證也換成未知值）。
    """
    target = "HF_TOKEN"
    r = _rot(sandbox, target, "BRAND-NEW-HF")
    assert r.returncode == 0, r.stderr
    after = _decrypt(sandbox["enc"], sandbox["env"])
    assert after[target] == "BRAND-NEW-HF"
    for k, v in sandbox["vals"].items():
        if k != target:
            assert after[k] == v, f"{k} 不該被動到"


def test_result_is_still_decryptable_by_sops(sandbox):
    """輪換後的檔案必須仍解得開（換值不是換加密）。"""
    assert _rot(sandbox, "HF_TOKEN", "NEW-1").returncode == 0
    assert _decrypt(sandbox["enc"], sandbox["env"])["HF_TOKEN"] == "NEW-1"


def test_recipients_count_unchanged(sandbox):
    """recipients 數量不可變 —— 變了就是有第三把 key 被悄悄加進來。"""
    before = len(re.findall(r"^sops_age__list_\d+__map_recipient=",
                            sandbox["enc"].read_text(encoding="utf-8"), re.M))
    assert _rot(sandbox, "HF_TOKEN", "NEW-2").returncode == 0
    after = len(re.findall(r"^sops_age__list_\d+__map_recipient=",
                           sandbox["enc"].read_text(encoding="utf-8"), re.M))
    assert after == before == 1


def test_key_count_unchanged(sandbox):
    """鍵數不可增減。"""
    before = len([k for k in _decrypt(sandbox["enc"], sandbox["env"])])
    assert _rot(sandbox, "HF_TOKEN", "NEW-3").returncode == 0
    assert len(_decrypt(sandbox["enc"], sandbox["env"])) == before


def test_failure_leaves_original_untouched(sandbox):
    """換值失敗時原檔必須完全沒動。

    這是「先寫暫存檔、驗證通過才 mv」那個設計存在的理由：直接寫原檔的話，
    中途失敗會留下半個壞掉的加密檔，三台一起 pull 不了。
    """
    before = sandbox["enc"].read_bytes()
    _rot(sandbox, "HF_TOKEN", "a\nb")          # 多行 → 應該被擋
    assert sandbox["enc"].read_bytes() == before


@pytest.mark.parametrize("bad,expect", [
    ("", "empty"),
    ("line1\nline2", "newlines"),
])
def test_rejects_bad_values(sandbox, bad, expect):
    """空值與多行值要擋下。

    空值＝刪掉這把憑證；多行＝使用者貼了整份 .env 進來（那是 --init-secrets
    的用途，不是這支）。兩者都會產生一個「看似成功、實際壞掉」的加密檔。
    """
    r = _rot(sandbox, "HF_TOKEN", bad)
    assert r.returncode != 0
    assert expect in r.stderr


def test_rejects_identical_value(sandbox):
    """新舊值相同要拒絕 —— 避免「以為輪換了，其實沒有」。"""
    r = _rot(sandbox, "HF_TOKEN", sandbox["vals"]["HF_TOKEN"])
    assert r.returncode != 0
    assert "相同" in r.stderr


def test_never_prints_the_value(sandbox):
    """**任何輸出都不可包含憑證值本身**。

    2026-09-26 三次憑證外洩都是「查證時列印了值」，其中一次就是把整份 .env
    cat 出來。這支的用途就是換憑證 —— 它最可能被人貼到 issue 或聊天記錄裡。
    只印 len= 與 sha12=。
    """
    secret = "SUPERSECRET-NEVER-PRINT-THIS-VALUE"
    r = _rot(sandbox, "HF_TOKEN", secret)
    assert r.returncode == 0
    for stream in (r.stdout, r.stderr):
        assert secret not in stream
        assert "SUPERSECRET" not in stream
    # 但要有可跨機比對的指紋
    assert "sha12=" in r.stdout


def test_from_file_is_not_deleted(sandbox, tmp_path):
    """`--from-file` 不可刪掉來源檔 —— 只提醒。

    Friction 點：第一版會 shred 來源檔。那是「猜使用者想要什麼」：來源檔可能是
    他刻意留在某處的（範本、貼給別台的片段），刪掉是不可復原的資料損失。
    """
    f = tmp_path / "newkey.txt"
    f.write_text("BRAND-NEW-HF\n", encoding="utf-8")
    r = _rot(sandbox, "HF_TOKEN", None, "--from-file", str(f))
    assert f.is_file(), "--from-file 不可刪除來源檔"
    if r.returncode == 0:
        assert "shred -u" in r.stderr, "要提醒使用者檔案是明文"


def test_tells_you_the_followup_steps(sandbox):
    """成功後要印出接下來該做什麼（pull / 重建容器 / 驗三台一致）。

    印出「換好了」但不說接下來要做什麼，人會以為完事了 —— 而 key 是啟動
    參數，不重啟不生效，那個「沒生效」的症狀要等到有人發現查詢失敗。
    """
    r = _rot(sandbox, "HF_TOKEN", "NEW-4")
    assert r.returncode == 0
    for needle in ("env-sync.sh pull", "docker compose up", "--fingerprints"):
        assert needle in r.stdout, f"忘了印 {needle}"


def test_does_not_commit_or_push(sandbox):
    """這支**不可**自己 commit/push —— 提交是人的決定，要能先看 diff。"""
    r = _rot(sandbox, "HF_TOKEN", "NEW-5")
    assert r.returncode == 0
    assert "commit" in r.stdout and "push" in r.stdout, "要提醒但不代勞"
    # sandbox 不是 git repo，若腳本真的去 commit/push 會在這裡炸
    assert not (sandbox["root"] / ".git").exists()


# ── --add：把新憑證加進加密檔（2026-10-02）────────────────────────────────
#
# 為什麼需要：原本這支碰到加密檔裡沒有的鍵會 die，所以「新增一把共用憑證」
# 只能靠 --init-secrets（毀滅性，會覆蓋整份）或手動 sops -d／改檔／sops -e。
# 後者太痛 → 拖 → 2026-10-02 CF_ACCESS_CLIENT_ID/SECRET 在 sops 層缺席一天，
# 而缺席的症狀是「mbp/x570 人手貼值、貼反與沒開 Access 長得一樣、
# --fingerprints 也看不到那兩把」。所以這條路徑必須不痛。

def test_add_inserts_the_missing_key(sandbox):
    """--add 把缺的那把加進去，且**其他每把都不動**。"""
    key = sandbox["shared"][-1]
    _drop_from_enc(sandbox, key)
    r = _rot(sandbox, key, "BRAND-NEW-CFACCESS", "--add")
    assert r.returncode == 0, r.stderr
    after = _decrypt(sandbox["enc"], sandbox["env"])
    assert after[key] == "BRAND-NEW-CFACCESS"
    assert set(after) == set(sandbox["shared"]), "鍵集合要等於 SHARED_SECRETS"
    for k, v in sandbox["vals"].items():
        if k != key:
            assert after[k] == v, f"{k} 不該被動到"


def test_add_result_is_decryptable_and_keeps_recipients(sandbox):
    """加完仍解得開、recipients 數量不變、鍵數 +1。

    鍵數 +1 是 --add 與 --from-stdin 的**唯一**結構差異，而上面那段
    「key count changed」守衛（原本設計來擋 --init-secrets 式的重建）正是
    會誤傷這裡的地方 —— 所以這條釘住它對 --add 必須放行。
    """
    key = sandbox["shared"][-1]
    _drop_from_enc(sandbox, key)
    before = len(re.findall(r"^sops_age__list_\d+__map_recipient=",
                            sandbox["enc"].read_text(encoding="utf-8"), re.M))
    assert _rot(sandbox, key, "NEW-CF-6", "--add").returncode == 0
    after_txt = sandbox["enc"].read_text(encoding="utf-8")
    assert len(re.findall(r"^sops_age__list_\d+__map_recipient=", after_txt, re.M)) == before
    assert len(_decrypt(sandbox["enc"], sandbox["env"])) == len(sandbox["shared"])


def test_add_refuses_a_key_that_is_already_there(sandbox):
    """已存在卻又給 --add 必須 die —— 那會是**靜默 no-op**。

    這一條比它看起來重要：換值時忘了拿掉 --add，如果靜默成功，使用者會
    以為換掉了，舊值繼續在三台流通，而且沒有任何一行輸出說「其實沒換」。
    """
    r = _rot(sandbox, "HF_TOKEN", "NEW-CF-7", "--add")
    assert r.returncode != 0
    assert "已經在加密檔裡" in r.stderr
    after = _decrypt(sandbox["enc"], sandbox["env"])
    assert after["HF_TOKEN"] == sandbox["vals"]["HF_TOKEN"], "值必須沒被動到"


def test_missing_key_without_add_points_at_add_not_at_init_secrets(sandbox):
    """缺鍵又沒給 --add：錯誤訊息要**指向 --add**，並明說別用 --init-secrets。

    Friction 點：這是最容易回頭走 --init-secrets 的時刻（它確實能加進去）。
    而 --init-secrets 會覆蓋整份加密檔，把另外 7 把已曝露過的憑證全換成未知值。
    訊息裡不擋這一下，那個按鍵就是近在咫尺。
    """
    key = sandbox["shared"][-1]
    _drop_from_enc(sandbox, key)
    r = _rot(sandbox, key, "NEW-CF-8")
    assert r.returncode != 0
    assert "--add" in r.stderr
    assert "--init-secrets" in r.stderr, "要明說那條路會覆蓋整份"
    # 失敗就什麼都不能變：這把仍然缺席（否則會得到「半個加密檔」）
    assert key not in _decrypt(sandbox["enc"], sandbox["env"])


def test_add_failure_leaves_original_untouched(sandbox):
    """--add 失敗時原檔完全沒動（同「先寫暫存、驗過才 mv」的理由）。"""
    key = sandbox["shared"][-1]
    _drop_from_enc(sandbox, key)
    before = sandbox["enc"].read_bytes()
    _rot(sandbox, key, "line1\nline2", "--add")
    assert sandbox["enc"].read_bytes() == before


def test_add_never_prints_the_value(sandbox):
    """--add 的輸出同樣不可含值（新增也是會被貼到聊天記錄裡的操作）。"""
    key = sandbox["shared"][-1]
    _drop_from_enc(sandbox, key)
    secret = "SUPERSECRET-NEVER-PRINT-ON-ADD"
    r = _rot(sandbox, key, secret, "--add")
    assert r.returncode == 0
    for stream in (r.stdout, r.stderr):
        assert secret not in stream and "SUPERSECRET" not in stream
    assert "sha12=" in r.stdout, "但要有可跨機比對的指紋"


def test_add_tells_you_to_sync_the_key_declaration(sandbox):
    """加完要提醒**三處宣告也要同步**，否則 --check 會報「該機缺鍵」。

    Friction 點：`SHARED_SECRETS`、`secrets.common.env.example`、
    `tests/test_env_sync.py` 的鏡像名單是三份。只改其中一份的症狀彼此不通：
    改了 env-sync.sh 沒改 example → `--init-secrets` 少抽一把；
    改了前兩份沒改測試 → pytest 紅；沒改前兩份 → 三台 pull 不到值。
    這個提醒是唯一把它們綁在一起的地方。
    """
    key = sandbox["shared"][-1]
    _drop_from_enc(sandbox, key)
    r = _rot(sandbox, key, "NEW-CF-9", "--add")
    assert r.returncode == 0
    for needle in ("secrets.common.env.example", "SHARED_SECRETS",
                   "test_env_sync.py", "env-sync.sh pull", "--fingerprints"):
        assert needle in r.stdout, f"忘了提醒 {needle}"

