"""sops + age 分發機制的不變量 —— 2026-10-02 稽核後補。

## 為什麼要這份測試

機制本身是通的（2026-10-02 實測：decrypt 成功、`--add` 加密成功、
撤銷一把公鑰後剩下那把照樣解得開）。但**守衛是缺的**，而且缺的正好是
2026-09-30 x570 踩過的那個坑：

> 「只加 recipient 不 updatekeys 是無效的 —— 檔案裡的資料金鑰仍只加密給原本的
> recipients，新公鑰形同虛設。」

這句話在 `.sops.yaml` 與 README §11 都寫著，但**沒有任何東西檢查它**。
`.sops.yaml` 有三把公鑰、加密檔只有兩把 recipients —— 這兩個檔案可以
長期不一致而 repo 全綠，然后那台機器 `pull` 才報
`no identity matched any of the recipients`。

而那句錯誤訊息同時涵蓋「名單裡沒我」與「我沒私鑰」兩種完全不同的原因，
2026-09-30 wsl 重灌後繞了半小時（見 `test_rotate_secret.py`）。

## 這份測試抓到過什麼

`test_env_sync.py` 有一條 `test_sops_config_has_wsl_recipient_no_private_key`，
斷言 `'x570' in text` 與 `'mbp' in text`。**那兩個字串只出現在註解裡。**
實測：把真正的公鑰從 recipients 拿掉、註解留著，那條測試**照樣綠**。
（2026-10-02 已把那條改成檢查實際 recipients，本檔不再重複。）
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOPS_YAML = ROOT / ".sops.yaml"
ENC = ROOT / "settings" / "env" / "secrets.common.enc.env"
HOSTS_TABLE = ROOT / "settings" / "env" / "hosts.shared.env"

# age 公鑰：`age1` 開頭，bech32 編碼的 x25519 公鑰是 32 bytes → 52 個字元
PUBKEY = re.compile(r"age1[0-9a-z]{58}")
ENC_RECIPIENT = re.compile(
    r"^sops_age__list_(\d+)__map_recipient=(age1[0-9a-z]+)", re.M)


def _yaml_recipients() -> set[str]:
    return set(PUBKEY.findall(SOPS_YAML.read_text(encoding="utf-8")))


def _enc_recipients() -> set[str]:
    return {m.group(2) for m in ENC_RECIPIENT.finditer(
        ENC.read_text(encoding="utf-8"))}


def _hosts() -> list[str]:
    m = re.search(r"^HOSTS=(.+)$",
                  HOSTS_TABLE.read_text(encoding="utf-8"), re.M)
    assert m, "hosts.shared.env 沒有 HOSTS= —— render 會硬失敗"
    return [h.strip() for h in m.group(1).split(",") if h.strip()]


# ── 核心不變量：加了公鑰一定要 updatekeys ────────────────────────────────

def test_sops_yaml_recipients_match_the_encrypted_file():
    """`.sops.yaml` 的 recipients 必須與加密檔裡的**完全一致**。

    這就是「只加 recipient 不 updatekeys」那個坑的守衛。兩邊不一致時：
      - `.sops.yaml` 多、加密檔少 → 那把新公鑰形同虛設（加了沒用）
      - `.sops.yaml` 少、加密檔多 → 有把**已撤銷**的公鑰還留在加密檔裡
        （危險：撤銷沒有真正生效）
    兩種都不會讓任何測試變紅 —— 這就是為什麼要有這條。
    """
    yaml_k, enc_k = _yaml_recipients(), _enc_recipients()
    assert yaml_k == enc_k, (
        "不變量破了：\n"
        f"  只在 .sops.yaml : {sorted(yaml_k - enc_k)}\n"
        f"  只在 加密檔     : {sorted(enc_k - yaml_k)}\n"
        "  多半是加了公鑰但忘了 `sops updatekeys settings/env/secrets.common.enc.env`"
    )


def test_encrypted_file_recipient_indices_are_contiguous():
    """recipients 的索引要從 0 連續編號。

    `sops updatekeys` 刪掉一把之後可能留下 `_list_2_` 這種跳號的殘留。
    那不會讓 sops 解不開，但會讓「數 recipients」這種檢查算錯 ——
    而「數」正是 --check 與人工核對最常做的事。
    """
    idx = sorted(int(m.group(1)) for m in ENC_RECIPIENT.finditer(
        ENC.read_text(encoding="utf-8")))
    assert idx == list(range(len(idx))), f"recipients 索引不連續: {idx}"


# ── 機器清單與公鑰數量 ────────────────────────────────────────────────

def test_one_recipient_per_machine():
    """recipients 的數量必須等於 `HOSTS=` 的機器數。

    加了第 4 台機器卻忘了加它的公鑰 —— 那台機器的 `pull` 會失敗，而症狀是
    `no identity matched any of the recipients`，那句話同時涵蓋
    「名單沒我」與「我沒私鑰」。x570 2026-09-30 就在這上面繞了半小時。
    有了這條，至少「忘了」會在**加機器當下**就紅，而不是在三台都上線後。
    """
    hosts, n = _hosts(), len(_enc_recipients())
    assert n == len(hosts), (
        f"HOSTS 有 {len(hosts)} 台（{hosts}）但只有 {n} 把 age 公鑰。"
        "新增機器要做兩件事：① 回報公鑰並加進 .sops.yaml ② 跑 sops updatekeys"
    )


def test_every_host_is_named_next_to_its_recipient():
    """每把公鑰都要在**註解裡**標明是哪台機器的。

    沒有這個，三把公鑰是三行長得一樣的行字串，沒有人知道哪一把是誰的 ——
    要移除某台機器的公鑰時只能逐一試錯，而「試錯」的失敗模式是
    把**別台**的公鑰移走（那台之後 pull 失敗，且沒有任何提示）。

    這一條順便擋掉 2026-10-02 稽核發現的舊測試問題：那條測試斷言的是
    註解裡的機器名，所以它驗的其實是「註解在」，而不是「公鑰在」。
    """
    lines = SOPS_YAML.read_text(encoding="utf-8").splitlines()
    hosts = _hosts()
    labelled = 0
    for h in hosts:
        # 公鑰那一行（去掉行首空白）之後往上找最近的註解
        for i, line in enumerate(lines):
            if line.strip().startswith("- age1") and PUBKEY.search(line):
                ctx = "\n".join(lines[max(0, i - 3):i])
                if re.search(rf"\b{re.escape(h)}\b", ctx):
                    labelled += 1
                    break
    assert labelled == len(hosts), (
        f"只有 {labelled}/{len(hosts)} 把公鑰在註解裡標明了機器名（{hosts}）。"
        "三行公鑰長得一模一樣，沒有人知道哪一把是哪台的。"
    )


# ── 安全邊界 ──────────────────────────────────────────────────────────

def test_no_private_key_material_anywhere_in_tracked_config():
    """`.sops.yaml` 絕不可含私鑰材料。

    `AGE-SECRET-KEY` 是 age 私鑰的固定前綴；另檢查 `public-key:` 這種
    可能被誤貼的形式。
    """
    text = SOPS_YAML.read_text(encoding="utf-8")
    for needle in ("AGE-SECRET-KEY", "private-key", "BEGIN OPENSSH"):
        assert needle not in text, f".sops.yaml 含私鑰材料（{needle}）"


def test_private_key_file_is_not_tracked_and_ignored():
    """私鑰檔絕不進版控，且必須被 .gitignore 蓋住。

    `age-keygen -o ~/.config/sops/age/keys.txt` 預設寫到家目錄，理論上
    不會被加進 repo。但「理論上」不是保險 —— 而這是**全部 8 把共用憑證**
    的唯一解密材料，洩漏等於全部一起走。
    """
    assert not (ROOT / "keys.txt").is_file(), (
        "repo 根有 keys.txt —— 那是私鑰檔的預設名，絕不可進版控"
    )
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "keys.txt" in gi, ".gitignore 沒有擋 keys.txt"
    tracked = subprocess_grep_keys()
    assert not tracked, f"版控中有 {tracked}"


def subprocess_grep_keys() -> list[str]:
    import subprocess
    r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                       text=True)
    return [f for f in r.stdout.splitlines() if "keys.txt" in f]


def test_public_keys_are_not_secret_so_tracking_them_is_intended():
    """公鑰**是公開的**，進版控是刻意的 —— 這條釘住「不要誤刪」。

    上一條會擋掉私鑰檔；這條提醒另一件事：`agents.json` 之類的人看到
    `age1...` 可能會當成憑證值想辦法清掉，而清掉之後所有機器都 pull 不到。
    """
    assert _yaml_recipients(), ".sops.yaml 的 recipients 是空的 —— 所有機器都會 pull 失敗"


# ── 公鑰指紋：分不清「手上這把是哪台用的」────────────────────────────

def test_each_recipient_has_a_fingerprint_next_to_it():
    """每把公鑰旁邊都要有它的指紋。

    為什麼需要：三把公鑰是三行長度相同的字串，`age1…` 前 12 個字元也只有
    wsl／x570 看得出差異（mbp 與 wsl 前 12 幾乎一樣）。**沒有指紋時，
    「我手上這把私鑰是哪一台的」只能靠記憶或逐一試錯** —— 而試錯的失敗模式
    是移除**別台**的公鑰，那台之後 `pull` 失敗且沒有任何提示。

    指紋是公鑰的 sha256 前 12 字元（公鑰本來就是公開的，指紋不洩漏任何東西）。
    """
    lines = SOPS_YAML.read_text(encoding="utf-8").splitlines()
    missing = []
    for i, line in enumerate(lines):
        if line.strip().startswith("- age1"):
            ctx = "\n".join(lines[max(0, i - 3):i])
            if "sha12=" not in ctx:
                missing.append(line.strip()[:22])
    assert not missing, (
        f"這些公鑰旁邊沒有指紋：{missing}。"
        "指紋是公鑰的 sha256 前 12 字元，用 age-keygen -y 取得後 sha256sum 一下就有"
    )


def test_fingerprints_match_the_actual_public_keys():
    """註解裡的指紋必須真的等於那把公鑰的 sha256 前 12。

    Friction 點：這兩行都是註解，而註解不會被任何程式驗證 —— 若不檢查，
    「指紋」會變成又一份會漂移的真相（而它存在的理由就是防止漂移）。
    實測：`test_env_sync.py` 那條舊測試就是這樣抓不到東西的。
    """
    import hashlib
    lines = SOPS_YAML.read_text(encoding="utf-8").splitlines()
    checked = 0
    for i, line in enumerate(lines):
        m = re.search(r"- (age1[0-9a-z]+)\s*$", line)
        if not m:
            continue
        key = m.group(1)
        ctx = "\n".join(lines[max(0, i - 3):i])
        fp = re.search(r"sha12=([0-9a-f]{12})", ctx)
        assert fp, f"{key[:16]}… 旁邊沒有指紋"
        want = hashlib.sha256(key.encode()).hexdigest()[:12]
        assert fp.group(1) == want, (
            f"{key[:16]}… 的指紋寫錯了：註解說 {fp.group(1)}，實際是 {want}"
        )
        checked += 1
    assert checked == 3, f"應檢查到 3 把公鑰，實際 {checked}"
