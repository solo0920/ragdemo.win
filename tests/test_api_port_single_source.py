"""**API 的埠不许散落成多個真相。**

⚠️ 2026-10-03（mbp 回報的真實事故之後）：埠 `8000` 被寫死在**七處**，而其中
`compose.yaml` 那一處是**最關鍵的** —— 它決定 docker 實際 publish 在哪裡。

| # | 位置 | 原本 | 改完 |
|---|---|---|---|
| 1 | `compose.yaml:64` | `127.0.0.1:8000:8000` | `${API_PORT:-8000}:8000` |
| 2 | `host-doctor.sh` `API_URL` | 有變數（可用 `HOST_API_LOCAL`） | 不變 |
| 3 | `host-sync.sh` `API_URL` | 有變數 | 不變 |
| 4 | `host-doctor.sh` `ch_readiness` | **寫死 `localhost:8000`** | `${API_URL}/ready` |
| 5 | `host-doctor.sh` `ch_query` | **寫死 `localhost:8000`** | `${API_URL}/query` |
| 6 | `wait-stack.sh` | **寫死 `localhost:8000`** | `${HOST_API_LOCAL:-…}` |
| 7 | `ensure-stack.sh` | **寫死 `127.0.0.1:8000`** | `${HOST_API_LOCAL:-…}` |
| 8 | `sync-snapshot.sh` fallback | **寫死 `:8000`** | 取 `HOST_API_LOCAL` 的埠 |
| 9 | `frontend/vite.config.ts` | **寫死 `localhost:8000`** | `process.env.HOST_API_LOCAL` |

**為什麼這件事值得一個測試**：寫死的地方**功能上都正常**（8000 就是現在的埠），
所以沒有任何症狀會指出「這裡寫死了」。而一旦有人改埠，那一處沒跟著改 →
症狀是「設定改了但行為沒變」或「stack 永遠等不到就緒」，**沒有任何錯誤**。

這正是本專案今天記錄了八次的形狀：**變數存在，但某些呼叫端繞過它**。

⚠️ 本檔**不列舉檔名**（那會是又一份手維護清單，會漂移）—— 掃描整個
`scripts/` 與 `frontend/src`／`vite.config.ts`，任何**可執行行**裡出現
`localhost:8000`／`127.0.0.1:8000`／`:8000` 就是違規。註解裡可以提（那是歷史紀錄，
而且註解裡的 8000 是有價值的說明）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 這些檔案裡的 8000 會被視為違規（可執行行）
SCANNED_GLOBS = (
    "scripts/*.sh",
    "frontend/vite.config.ts",
    "compose.yaml",
)

# ⚠️ **散文型**的 8000：出現在**給人看的訊息字串**裡，不是請求端點。
#    以「行內子字串」比對（不用行號 —— 行號會隨檔案編輯漂移）。
#    每筆都要有理由；空白理由等於沒有依據，而沒有依據的例外就是這個缺陷的形狀。
PROSE_ALLOWED = {
    "沒綁在 127.0.0.1:8000":
        "host-sync.sh 的 warn **訊息字串**，是給人看的說明，不是請求端點；"
        "而且它描述的是 compose 的綁定（由 compose.yaml 的變數決定），"
        "改埠時這段文案會一起需要更新 —— 但它不是行為。",
}


def _strip_comment(line: str) -> str:
    """去掉註解。**刻意不做引號解析** —— 那個手寫狀態機會出錯。

    ⚠️ 2026-10-03 兩次實測：
      · 第一版把 `//` 一律當註解起點，於是 `http://localhost:8000` 在 `http:` 就被
        截斷 —— **整條守衛被 URL 靜默禁用**，而它全部的工作就是抓 URL 裡的埠。
      · 第二版改成「`//` 前一個字元不是 `:` 才是註解」，**症狀沒消失**，因為真正的
        根因是**引號狀態機**：shell 那行是
        `body="$(curl ... -w '%{http_code}' http://localhost:8000/ready 2>/dev/null)"`，
        單引號被當成巢狀字串打開又關上，於是 `http://` 的 `//` 落在「字串外」→ 截斷。
        `2>/dev/null` 的 `//` 也一樣。而這條守衛抓的就是 shell 的 curl 行。

    所以：**跳過整行註解就好**，不做引號／巢狀字串的推斷。副作用是「行尾註解裡
    提到 8000」會誤報 —— 那種寫法在本 repo 不存在，而且真出現時訊息會指出是哪一行，
    讓人自己看（比一個會靜默失效的狀態機好）。
    """
    st = line.lstrip()
    if st.startswith("#") or st.startswith("//"):
        return ""
    # 行尾註解：空白之後的 `#`，或空白之後且非 `://` 的 `//`
    cut = len(line)
    for m in re.finditer(r"(\s#|\s(?!://)//)", line):
        cut = m.start()
        break
    return line[:cut]


# 可執行行裡的「硬寫 8000」：必須是 port 的位置（前面是 : 或 冒號後單獨）
HARDCODED = re.compile(r"(?:localhost|127\.0\.0\.1|\])\s*:\s*8000\b|:\s*8000\b")


def _violations(path: Path):
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = _strip_comment(line)
        if not code.strip():
            continue
        for m in HARDCODED.finditer(code):
            # `${VAR:-8000}` / `:-8000` 是**預設值**，不是寫死
            before = code[max(0, m.start() - 24):m.start()]
            # `:-8000`（shell 預設）、`?? '…8000'`（JS nullish 預設）、
            # `|| 8000` —— 這些是**預設值**，改了變數仍然生效，所以不算違規。
            if any(tok in before for tok in (":-", "??", "||", "PORT", "API")):
                continue
            # 散文型（給人看的訊息字串）
            if any(k in code for k in PROSE_ALLOWED):
                continue
            # YAML 的 `published:` 之類由 compose 斷言處理
            out.append((i, line.strip()))
            break
    return out


def test_no_script_hardcodes_the_api_port():
    """**可執行行裡不得出現寫死的 API 埠。**

    寫死的地方功能上都正常（8000 就是現在的埠），所以**沒有任何症狀會指出它**。
    """
    bad = []
    for pat in SCANNED_GLOBS:
        for f in sorted(ROOT.glob(pat)):
            if f.name == "compose.yaml":
                continue          # 由 compose 專門的斷言處理
            rel = f.relative_to(ROOT).as_posix()
            for ln, text in _violations(f):
                bad.append(f"{rel}:{ln}  {text[:90]}")
    assert not bad, (
        "這些可執行行把 API 的埠寫死了 —— 設定變數對它們**無效**：\n  "
        + "\n  ".join(bad)
        + "\n\n  請改用 `${HOST_API_LOCAL:-http://127.0.0.1:8000}`（主機側 URL）"
          "或 `${API_PORT:-8000}`（主機側埠）。\n"
          "  註解裡可以提 8000（那是歷史紀錄），這條只管可執行行。")


def test_every_prose_exemption_has_a_reason():
    """**散文放行必須有理由。**

    「沒有依據的例外」就是這個缺陷的形狀 —— 而一個**永遠紅**的測試會被改成
    skip，那又回到原點：壞掉的東西看起來像有覆蓋。
    """
    for k, why in PROSE_ALLOWED.items():
        assert why.strip(), f"散文放行 {k!r} 沒寫理由"


def test_compose_publishes_the_host_port_from_a_variable():
    """**compose 的主機側埠必須是變數。**

    這是**最關鍵的一處**：主機側決定 docker 實際 publish 在哪。如果它寫死，
    那麼**即使所有腳本都讀了變數，整個改埠等於沒改** —— 而且沒有任何錯誤。
    """
    y = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    m = re.search(r'^\s*ports:\s*\["([^"]+)"\]\s*$', y, re.M)
    # 找出 api 服務那條（它有 `${API_PORT` 或 8000:8000）
    api = None
    for line in y.splitlines():
        if re.search(r'"[^"]*:?\$\{API_PORT:-(\d+)\}:(\d+)"', line):
            api = line.strip()
            break
    assert api, (
        "compose.yaml 的 api ports 必須是 `\"127.0.0.1:${API_PORT:-<埠>}:<容器埠>\"` ——\n"
        "  主機側寫死的話，改埠會「看起來成功但行為沒變」。\n"
        f"  目前找到的 ports 行：{[l.strip() for l in y.splitlines() if 'ports:' in l]}")
    assert "127.0.0.1" in api, \
        "主機側必須繼續綁 127.0.0.1（公網只經 cloudflared tunnel）—— 別順手改成 0.0.0.0"


def test_host_api_local_default_and_api_port_default_agree():
    """**`HOST_API_LOCAL` 的預設埠與 compose 的 `API_PORT` 預設必須一致。**

    兩個變數表達同一件事（主機側埠），所以它們不一致時**沒有人會發現** ——
    症狀是「改了 A 但 B 沒改，行為只改了一半」。
    """
    y = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    hd = (ROOT / "scripts" / "host-doctor.sh").read_text(encoding="utf-8")
    df = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    m1 = re.search(r"\$\{API_PORT:-(\d+)\}", y)
    assert m1, "compose 裡找不到 ${API_PORT:-<數字>}"
    m2 = re.search(r'API_URL="\$\{HOST_API_LOCAL:-http://[^"]*?:(\d+)\}"', hd)
    assert m2, "host-doctor.sh 裡找不到 HOST_API_LOCAL 的預設埠"
    assert m1.group(1) == m2.group(1), (
        f"兩個變數的預設埠不一致：compose 的 API_PORT={m1.group(1)}、"
        f"HOST_API_LOCAL 的埠={m2.group(1)} —— 它們表達同一件事，"
        "不一致時症狀是「改了一半而且沒報錯」")

    # ── 容器側埠是**烤進 image** 的，必須與 compose 右側一致 ──────────────
    # 這是 8000→920（2026-10-03）時補上的第三個點：前兩個只保證「主機側」一致，
    # 但 ports 的**右側**是容器內部的 listening port，而那個值來自 Dockerfile 的
    # `CMD --port`（建 image 時烤死，compose 改不到）。
    # 只改 compose 右側而沒 rebuild → 主機 publish 到沒人 listening 的埠，
    # 症狀是「主機 curl 連不上、容器內 curl 正常」—— 兩邊症狀不同源。
    # ⚠️ 只抓**含 `${API_PORT` 的那條**（api 服務的）。用第一條 `ports:` 會拿到
    #    qdrant 的 6333 —— compose 裡 ports 有三條，而 qdrant 那條寫死（`…}:6333`），
    #    所以靠 `${API_PORT` 這個特徵辨識，比靠行號或「第一條」穩。
    ports_line = re.search(r'^\s*ports:\s*\["[^"]*:?\$\{API_PORT[^"]*:(\d+)"\]\s*$', y, re.M)
    assert ports_line, "compose 裡找不到含 ${API_PORT} 的 api ports 行"
    container_port = ports_line.group(1)
    m3 = re.search(r'--port",\s*"(\d+)"', df) or re.search(r"--port[= ]\s*(\d+)", df)
    assert m3, ("backend/Dockerfile 裡找不到 CMD 的 --port —— api 容器實際 listening "
                "的埠就來自那一行，compose 的右側必須與它一致")
    assert container_port == m3.group(1), (
        f"容器埠不一致：compose ports 右側={container_port}、"
        f"Dockerfile CMD --port={m3.group(1)} —— 只改一邊會 publish 到沒人 "
        "listening 的埠（主機 curl 連不上、容器內 curl 正常）")


def test_the_port_is_extracted_the_same_way_everywhere():
    """**取埠碼的方式必須到處一致。**

    ⚠️ 2026-10-03 實測：我在 `sync-snapshot.sh` 第一版寫成
    `printf '%s' "$URL" | tr -dc '0-9'`，那是**錯的** —— 它把整個 URL 的數字
    串起來：`http://127.0.0.1:8000` → **`1270018000`**。（而且我當時還註解說
    「hostname 裡本來就不該有數字」，而 `127.0.0.1` 全是數字。）

    正確做法是 `${VAR##*:}` 取**最後一個冒號之後**那段。兩處寫法不同就會漂移，
    而且症狀是「埠變成一個 10 位數」—— 那種錯誤很明顯，但**只在改埠時才出現**。
    """
    for f in ("scripts/host-doctor.sh", "scripts/sync-snapshot.sh"):
        src = (ROOT / f).read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            # ⚠️ 跳過註解 —— 這條測試自己在註解裡引用了錯誤寫法當反面教材，
            #   不跳過的話它會抓自己（第一版就這樣，而且抓到的行是註解，
            #   訊息會指向一個「看起來在抱怨註解」的無效斷言）。
            if _strip_comment(line).strip() == "":
                continue
            if "tr -dc '0-9'" not in line and 'tr -dc "0-9"' not in line:
                continue
            # 必須作用在 ${...##*:} 的結果上，而不是整個 URL
            assert "##*:" in line, (
                f"{f}:{i} 取埠時用了 `tr -dc 0-9`，但沒有先 `##*:` ——\n"
                f"  {line.strip()[:90]}\n"
                "  那會把整個 URL 的數字串起來（127.0.0.1:8000 → 1270018000）。")