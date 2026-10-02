"""`.env` 版面：共用在前、per-host 在最後 —— 2026-10-02。

## 為什麼要有版面這個東西

原本 `.env.example`（以及由它衍生的三台 `.env`）依**設定類別**分節：必填／
必填含憑證／設定選填／憑證選填／未傳入容器／host 端／compose 寫死。
那個分法對「這台怎麼跑」有意義，但對**三台的檔案能不能一致**沒有意義 ——
per-host 的鍵因此散落全檔：

```
L44   HOST_ID              per-host
L63   POSTGRES_PASSWORD    per-host 機密
L82   HOST_API_URLS        per-host
L260  QDRANT_PEER_API_KEY   共用
L294  OLLAMA_URLS          per-host
```

要人眼掃過才知道哪個鍵該跟著共用值更新，而三台長得又不一樣。

## 為什麼 per-host 用「區段標題」而不用前綴

`compose.yaml` 只認 `${VAR}` 插值。前綴若寫在 `.env`：
`LLM_MODEL` 掉回原始碼預設（**靜默**，沒有錯誤）、`TS_IP` 讓 docker 綁錯而
啟動失敗。兩者都是 `settings/env/README.md §9` 記錄的實測。

所以識別寫在**區段標題**（`# ══ HOST: wsl ══`），識別力與前綴相同，鍵名不變。
三欄並排的 `x570_`／`mbp_`／`wsl_` 視圖在**被追蹤的總表**裡（那裡本來就有
前綴），另可用 `env-sync.sh --hosts-table` 印成對齊表格。

## 分類的來源必須是資料，不是程式

`per_host_keys()` 讀三個來源。寫死鍵名就會漂移 —— 而且漂移的症狀是
「版面看起來有分區，但某個 per-host 鍵被歸到共用區」，那不會報錯，只會
讓人以為那個鍵三台相同。
"""
import hashlib
import importlib.util
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "env_audit", ROOT / "scripts" / "env-audit.py")
EA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EA)


def _template() -> str:
    return subprocess.run(
        ["python3", str(ROOT / "scripts" / "env-audit.py"), "--template"],
        capture_output=True, text=True, cwd=ROOT, check=True).stdout


def _sections(text: str) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("# ══ 共用") or line.startswith("# ══ HOST"):
            out.append((i, line.strip()))
    return out


def _keys_after(text: str, start_line: int) -> list[str]:
    return [m.group(1) for m in
            re.finditer(r"^([A-Za-z_][A-Za-z_0-9]*)=",
                        "\n".join(text.splitlines()[start_line:]), re.M)]


# ── 版面結構 ───────────────────────────────────────────────────────────

def test_template_has_exactly_two_blocks_shared_then_host():
    """兩個大區塊、共用在前、per-host 在最後。

    只有兩個：多於兩個會讓「這台怎麼跑」與「三台能否一致」兩件事混在
    同一個維度上 —— 那是原本依設定類別分七段時的問題。
    """
    secs = _sections(_template())
    labels = [l for _, l in secs]
    assert len(secs) == 2, f"應恰有兩個區塊（共用／HOST），實際 {labels}"
    assert "共用" in labels[0], f"第一個區塊應是共用，實際 {labels[0]}"
    assert "HOST" in labels[1], f"第二個區塊應是 HOST，實際 {labels[1]}"


def test_no_per_host_key_appears_before_the_host_block():
    """**任何** per-host 鍵都不可出現在共用區。

    Friction 點：分類錯一個鍵不會報錯，只會讓那個鍵看起來「三台相同」。
    而 per-host 鍵被歸到共用區的實際後果是：有人以為改共用值時該一起改它，
    結果只改了一台。
    """
    text = _template()
    secs = _sections(text)
    assert len(secs) == 2
    host_start = secs[1][0]
    ph = EA.per_host_keys()
    assert ph, "per_host_keys() 回空 —— 分類來源讀不到，版面會全部歸到共用區"
    leaked = [k for k in _keys_after(text, secs[0][0] - 1)
              if k in ph and _keys_after(text, secs[0][0] - 1).index(k) < host_start]
    # 直接用行號比對，避免 index() 在重複鍵時報錯
    before_host = []
    for i, line in enumerate(text.splitlines(), 1):
        if i >= host_start:
            break
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)=", line)
        if m:
            before_host.append(m.group(1))
    assert not (set(before_host) & ph), (
        f"這些 per-host 鍵出現在共用區：{sorted(set(before_host) & ph)}"
    )


def test_shared_block_contains_no_per_host_secrets():
    """per-host **機密**（QDRANT_API_KEY／POSTGRES_PASSWORD）尤其不可進共用區。

    那兩把不在總表、只在各機 `.env` 裡；分類錯了會讓人以為三台的 qdrant
    key 應該同步 —— 而那正是 2026-09-26 「本機 200、遠端 401」不對稱故障的
    來源分類錯誤。
    """
    text = _template()
    host_start = _sections(text)[1][0]
    for i, line in enumerate(text.splitlines(), 1):
        if i >= host_start:
            break
        for k in ("QDRANT_API_KEY", "POSTGRES_PASSWORD"):
            assert not line.startswith(f"{k}="), (
                f"第 {i} 行：{k} 是 per-host 機密，不可出現在共用區"
            )


def test_host_block_header_uses_a_placeholder_in_the_template():
    """範本裡是佔位，不是寫死的 `HOST: wsl`。

    `.env.example` 是**同一份檔三台共用**的（追蹤、無憑證）。寫死機台代號
    會讓另外兩台的 `.env` 帶著別人的代號 —— 而那正是「這份檔描述誰」最基本
    的錯誤。實際代號由 `env-sync.sh render` 依本機 `HOST_ID` 填。
    """
    text = _template()
    assert "# ══ HOST: <本機 HOST_ID> ══" in text
    for host in EA.declared_hosts():
        assert f"# ══ HOST: {host} ══" not in text, (
            f"範本裡寫死了 {host} —— 範本是三台共用的同一份檔"
        )


def test_subcategories_are_preserved_inside_each_block():
    """區塊內仍保留子類別（必填／選填…）。

    把七段壓成兩段會丟掉「哪些必填」這個資訊，而那是填 `.env` 時最需要的
    —— `cf .env.example .env` 的人靠它知道先填哪幾行。
    """
    labels = [l for _, l in _sections(_template())]
    text = _template()
    assert text.count("# ══ 必填 ══") >= 2, (
        "兩個區塊都該有『必填』子類別 —— 否則有一半的鍵沒有必填標示"
    )
    assert labels  # 別讓這條變成空斷言


# ── 分類的來源 ─────────────────────────────────────────────────────────

def test_per_host_keys_come_from_three_declared_sources():
    """三個來源都要真的被讀到 —— 少一個，分類就少一類。

    少讀 `PER_HOST_SECRETS` → 兩把 per-host 機密被歸到共用區
    少讀總表 → 10 個 per-host 設定鍵被歸到共用區
    少讀 `# LOCAL_ONLY:` → TS_IP／HOST_ID 被歸到共用區（而它們是
    三台各填各的）
    """
    ph = EA.per_host_keys()
    for k in ("QDRANT_API_KEY", "POSTGRES_PASSWORD"):        # 來源 2
        assert k in ph, f"{k} 應來自 env-sync.sh 的 PER_HOST_SECRETS"
    for k in ("TS_IP", "HOST_ID"):                            # 來源 3
        assert k in ph, f"{k} 應來自總表的 `# LOCAL_ONLY:` 宣告"
    for k in ("LLM_MODEL", "OLLAMA_URLS", "HOST_NAME"):       # 來源 1
        assert k in ph, f"{k} 應來自 hosts.shared.env 的 <機台>_<鍵> 列"


def test_local_only_declaration_must_be_a_comment():
    """`# LOCAL_ONLY:` 那行**必須**是註解。

    實測：第一版寫成 `LOCAL_ONLY=TS_IP,HOST_ID`（沒有 `#`），被 `py_apply`
    讀成 layer 的一列，而它不符 `<機台>_<鍵>` 格式 →
    `env-sync.sh render` 報「總表有非 <機台>_<鍵> 的行: LOCAL_ONLY」並退出。
    那是好結果（錯在推、第一秒被抓到、沒寫壞任何東西），但值得釘住：
    這個檔裡任何非註解的行都會被當資料，**沒有第三種狀態**。
    """
    table = (ROOT / "settings" / "env" / "hosts.shared.env").read_text(
        encoding="utf-8")
    for line in table.splitlines():
        if "LOCAL_ONLY" in line:
            assert line.lstrip().startswith("#"), (
                f"LOCAL_ONLY 那行必須是註解：{line[:60]}"
            )
    # 而且實際跑一次 render 確認它不會被當成資料
    r = subprocess.run(["bash", str(ROOT / "scripts" / "env-sync.sh"),
                        "render", "--dry-run"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, f"render 失敗（LOCAL_ONLY 被當成資料了？）：{r.stderr}"


def test_per_host_keys_contain_no_equals_signs():
    """推導出來的鍵名不得含 `=`。

    Friction 點：總表的比對 regex 若寫成 `<機台>_(.+)=`，`.+` 貪心會把
    `=值` 一起吃掉，於是 out 裡出現「HOST_NAME=wsl」這種整行。
    分類看起來成功（23 個「鍵」），但一個都對不上實際的鍵名。
    """
    bad = [k for k in EA.per_host_keys() if "=" in k]
    assert not bad, f"鍵名裡混進了整行（含 =值）：{bad}"


# ── 跨機驗證：版面指紋 ─────────────────────────────────────────────────

def test_layout_fingerprint_is_stable_and_value_free(tmp_path):
    """版面指紋：鍵名序列 ＋ 區段標題的 sha12，**不含任何值**。

    這是唯一能在不做跨機 diff 的前提下驗「三台格式一致」的方法 ——
    `.env` 有 8 份憑證，不能拿去版本控制或貼進聊天。
    """
    # 用 importlib 而非 sys.path.insert：`scripts/` 底下有 env-audit.py 與
    # env-sync.sh 同名的那種陷阱（.py 與 .sh 並存），而 sys.path 插入會讓
    # 「import 到哪一個」變成隱藏輸入。spec_from_file_location 是明確的。
    spec = importlib.util.spec_from_file_location(
        "env_relayout", ROOT / "scripts" / "env-relayout.py")
    R = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(R)
    a = R.layout_fingerprint("# ══ 共用（三台應該相同）══\nX=1\nY=2\n# ══ HOST: wsl ══\nZ=3\n")
    b = R.layout_fingerprint("# ══ 共用（三台應該相同）══\nX=9\nY=8\n# ══ HOST: wsl ══\nZ=7\n")
    assert a == b, "同結構不同值必須得到同一指紋 —— 指紋若含值就沒有用途了"
    c = R.layout_fingerprint("# ══ 共用（三台應該相同）══\nY=2\nX=1\n# ══ HOST: wsl ══\nZ=3\n")
    assert a != c, "鍵順序不同必須得到不同指紋 —— 否則它抓不到版面漂移"
    d = R.layout_fingerprint("# ══ 共用（三台應該相同）══\nX=1\nY=2\n# ══ HOST: mbp ══\nZ=3\n")
    assert a != d, "區段標題不同必須得到不同指紋"
    assert "sha12=" in a


def test_check_prints_the_layout_fingerprint():
    """`env-sync.sh --check` 要印版面指紋 —— 否則跨機比對沒有入口。"""
    r = subprocess.run(["bash", str(ROOT / "scripts" / "env-sync.sh"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "版面" in r.stdout and "sha12=" in r.stdout, \
        "--check 沒有印版面指紋"
    assert "必須相同" in r.stdout, "要說明這個數字怎麼用（三台要拿它互比）"


def test_check_fingerprint_explains_it_is_not_value_consistency():
    """指紋只證明**結構**一致，值的一致性是 --fingerprints 的工作。

    不講清楚的話，使用者會把「指紋相同」當成「三台環境一樣」——
    而那兩件事完全不同（值會漂移而結構不變，這正是它要抓的）。
    """
    r = subprocess.run(["bash", str(ROOT / "scripts" / "env-sync.sh"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    assert "--fingerprints" in r.stdout, \
        "--check 的指紋那行要指向 --fingerprints（那是驗值的地方）"
